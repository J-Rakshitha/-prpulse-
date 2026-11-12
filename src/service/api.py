"""FastAPI inference service.

    python tasks.py serve            # or: python -m src.service.api

Models are loaded once at startup from artifacts/models. Predictions are stored
in the ``predictions`` table when the request names a repo and PR number.
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from src import setup_logging
from src.data.db import connect, init_db, resolve_db_path, resolve_target
from src.live import signals
from src.logging_filter import register_env_secrets, register_secret
from src.models.registry import (
    ModelStore,
    default_models_dir,
    derive_version,
    promote_version,
    read_registry,
    rollback_version,
    smoke_predict,
)
from src.runtime_settings import get_settings
from src.service import queue as queue_mod
from src.service import webhook
from src.service import worker_jobs
from src.service.bot import GitHubClient, PRBot, build_github_client
from src.service.core import PredictionService, StageUnavailable, UnknownFeatures
from src.service.github_app import resolve_github_client
from src.service.schemas import ExplainRequest, ExplainResponse, Health, Models, PredictRequest, PredictResponse
from src.service.store import ensure_model_version
from src.settings import load_settings, project_root

logger = logging.getLogger("prpulse.api")


class ActivateBody(BaseModel):
    version: str | None = Field(default=None, description="Version to promote; omit with action=rollback")
    action: str = Field(default="promote", description="promote | rollback")


def create_app(
    models_dir: Path | None = None,
    db_path: Path | None = None,
    cfg: dict[str, Any] | None = None,
    webhook_secret: str | None = None,
    github_client: GitHubClient | None = None,
    *,
    start_recovery: bool | None = None,
    runtime_settings: Any = None,
) -> FastAPI:
    """Build the app. Models and the DB are opened in the startup lifespan, once.

    Unit tests pass ``github_client`` and leave Redis unset so deliveries stay
    ``pending`` until ``queue.process_delivery_now`` drains them.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = cfg if cfg is not None else load_settings()
        runtime = runtime_settings if runtime_settings is not None else get_settings()
        register_env_secrets()
        for secret in runtime.secret_values():
            register_secret(secret)

        directory = Path(models_dir) if models_dir is not None else default_models_dir()
        if db_path is not None:
            path: Path | str = db_path
        elif os.environ.get("DATABASE_URL", "").strip():
            path = resolve_target(settings)
        else:
            path = resolve_db_path(settings)
        init_db(path)
        store = ModelStore.load(directory)
        version = derive_version(directory) if not store.empty else None
        require_models = bool(getattr(runtime, "require_models", False)) or os.environ.get("PRPULSE_REQUIRE_MODELS", "").strip() in (
            "1",
            "true",
            "True",
            "yes",
        )
        if require_models:
            if store.empty or not version:
                raise RuntimeError(f"PRPULSE_REQUIRE_MODELS=1 but no model bundles found in {directory}")
            try:
                smoke_predict(directory, stage="A")
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Model smoke predict failed: {exc}") from exc
        service = None
        if version:
            service = PredictionService(store, version, path)
            conn = connect(path)
            try:
                ensure_model_version(conn, version, artifact_path=str(directory), activate=True)
            finally:
                conn.close()
        else:
            logger.warning("No model bundles found in %s; /predict will return 503", directory)
        signals.set_model_version(version)
        signals.set_webhook_silence_seconds(runtime.silence_seconds())
        app.state.cfg = settings
        app.state.runtime = runtime
        app.state.models_dir = directory
        app.state.db_path = path
        app.state.store = store
        app.state.model_version = version
        app.state.service = service
        app.state.started = time.monotonic()
        secret = webhook_secret if webhook_secret is not None else runtime.webhook_secret_value() or os.environ.get("WEBHOOK_SECRET", "").strip()
        app.state.webhook_secret = secret
        app.state.delivery_cache = webhook.DeliveryCache()
        client = github_client if github_client is not None else build_github_client(runtime)
        bot = PRBot(client, service, cfg=settings, db_path=path)
        app.state.bot = bot

        def bot_factory(c):
            return PRBot(c, service, cfg=settings, db_path=path)

        def client_resolver(installation_id):
            if github_client is not None:
                return github_client
            return resolve_github_client(installation_id, runtime, fallback=client)

        worker_jobs.configure(
            bot=bot,
            bot_factory=bot_factory,
            db_target=path,
            settings=runtime,
            client_resolver=client_resolver,
        )
        # Tests pass start_recovery=False and drain with process_delivery_now (no Redis).
        redis_url = "" if start_recovery is False else (runtime.redis_url or os.environ.get("REDIS_URL", "")).strip()
        queue_mod.configure(
            redis_url=redis_url,
            queue_name=runtime.queue_name or os.environ.get("RQ_QUEUE_NAME", "prpulse"),
            db_target=path,
        )

        stop = threading.Event()
        do_recovery = start_recovery if start_recovery is not None else bool(redis_url)
        if do_recovery and redis_url:
            threading.Thread(
                target=_recovery_loop,
                args=(path, runtime, stop),
                daemon=True,
                name="webhook-recovery",
            ).start()
        app.state._recovery_stop = stop

        logger.info("Loaded %d model bundles; active version %s", len(store.bundles), version)
        yield
        stop.set()

    app = FastAPI(title="PR Pulse", version="0.1.0", lifespan=lifespan)
    app.include_router(webhook.router)

    @app.middleware("http")
    async def latency_logging(request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            endpoint = getattr(route, "path", "unmatched")
            signals.observe_request(endpoint, request.method, status, elapsed)
            logger.info("%s %s -> %s in %.1f ms", request.method, endpoint, status, elapsed * 1000)

    @app.exception_handler(UnknownFeatures)
    async def _unknown_features(request: Request, exc: UnknownFeatures):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(StageUnavailable)
    async def _stage_unavailable(request: Request, exc: StageUnavailable):
        return JSONResponse({"detail": str(exc.args[0]) if exc.args else "stage unavailable"}, status_code=503)

    def service_of(request: Request) -> PredictionService:
        service = getattr(request.app.state, "service", None)
        if service is None:
            raise HTTPException(status_code=503, detail="No models are loaded. Run `python tasks.py train` first.")
        return service

    @app.post("/predict", response_model=PredictResponse)
    def predict(req: PredictRequest, request: Request) -> dict[str, Any]:
        start = time.perf_counter()
        service = service_of(request)
        result = service.predict(
            req.stage,
            req.features,
            repo_full_name=req.repo_full_name,
            pr_number=req.pr_number,
            author_login=req.author_login,
            include_explanation=req.include_explanation,
        )
        result["latency_ms"] = (time.perf_counter() - start) * 1000.0
        return result

    @app.post("/explain", response_model=ExplainResponse)
    def explain(req: ExplainRequest, request: Request) -> dict[str, Any]:
        return service_of(request).explain(req.stage, req.features, req.top_k, req.include_suggestions)

    @app.get("/health", response_model=Health)
    def health(request: Request) -> dict[str, Any]:
        state = request.app.state
        service = getattr(state, "service", None)
        db_ok = service.db_ok() if service else _db_reachable(state.db_path)
        loaded = len(state.store.bundles)
        return {
            "status": "ok" if loaded and db_ok else "degraded",
            "model_version": state.model_version,
            "models_loaded": loaded,
            "stages": state.store.stages(),
            "db_ok": db_ok,
            "uptime_seconds": time.monotonic() - state.started,
        }

    @app.get("/models", response_model=Models)
    def models(request: Request) -> dict[str, Any]:
        state = request.app.state
        registry = read_registry(Path(state.models_dir).parent / "registry.json")
        return {
            "active_version": state.model_version,
            "registry_active": registry.get("active"),
            "models": state.store.describe(),
        }

    def _check_admin(token: str | None, runtime: Any) -> None:
        expected = ""
        if runtime is not None:
            expected = getattr(runtime, "admin_token_value", lambda: "")()
        if not expected:
            expected = os.environ.get("PRPULSE_ADMIN_TOKEN", "").strip()
        if not expected or not token or not hmac.compare_digest(token, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    def _reload_models(state: Any, directory: Path) -> None:
        store = ModelStore.load(directory)
        version = derive_version(directory) if not store.empty else None
        state.store = store
        state.model_version = version
        if version:
            state.service = PredictionService(store, version, state.db_path)
            if getattr(state, "bot", None) is not None:
                state.bot.service = state.service
            conn = connect(state.db_path)
            try:
                ensure_model_version(conn, version, artifact_path=str(directory), activate=True)
            finally:
                conn.close()
        else:
            state.service = None
        signals.set_model_version(version)

    @app.post("/admin/models/activate")
    def activate_model(
        request: Request,
        payload: ActivateBody = Body(...),
        x_prpulse_admin_token: str | None = Header(default=None, alias="X-PRPulse-Admin-Token"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        runtime = getattr(request.app.state, "runtime", None)
        token = x_prpulse_admin_token
        if not token and authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
        _check_admin(token, runtime)
        models = Path(request.app.state.models_dir)
        # Layout: <root>/artifacts/models and <root>/artifacts/versions/<ver>
        root = models.parent.parent if models.parent.name == "artifacts" else project_root()
        if payload.action == "rollback":
            result = rollback_version(root=root, models_dir=models)
        else:
            if not payload.version:
                raise HTTPException(status_code=422, detail="version is required for promote")
            result = promote_version(payload.version, root=root, models_dir=models)
        _reload_models(request.app.state, models)
        registry = read_registry(models.parent / "registry.json")
        return {
            "ok": True,
            "action": payload.action,
            "active_version": request.app.state.model_version,
            "registry_active": registry.get("active"),
            "previous": registry.get("previous"),
            "detail": result,
        }

    @app.get("/metrics")
    def metrics(request: Request) -> Response:
        try:
            conn = connect(request.app.state.db_path)
            try:
                signals.set_db_up(True)
                signals.refresh_live_accuracy(conn, cfg=request.app.state.cfg)
                from src.service.deliveries import count_by_status, count_failed_jobs
                from src.service.installations import count_active_installations

                signals.set_pending_deliveries(count_by_status(conn, "pending"))
                failed = count_failed_jobs(conn)
                signals.set_failed_jobs(failed)
                signals.set_installations_count(count_active_installations(conn))
                last = conn.execute(
                    "SELECT received_at FROM webhook_deliveries ORDER BY received_at DESC LIMIT 1"
                ).fetchone()
                if last is not None:
                    raw = last["received_at"] if hasattr(last, "keys") else last[0]
                    try:
                        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
                        signals.set_last_webhook_unixtime(ts)
                    except ValueError:
                        pass
                rows = conn.execute(
                    """
                    SELECT COALESCE(coverage_status, 'unknown') AS status, COUNT(*) AS n
                    FROM predictions GROUP BY COALESCE(coverage_status, 'unknown')
                    """
                ).fetchall()
                signals.set_coverage_status_counts(
                    {str(r["status"] if hasattr(r, "keys") else r[0]): int(r["n"] if hasattr(r, "keys") else r[1]) for r in rows}
                )
                runtime = getattr(request.app.state, "runtime", None)
                if runtime is not None:
                    signals.set_webhook_silence_seconds(runtime.silence_seconds())
            finally:
                conn.close()
            depth = queue_mod.get_queue_depth()
            if depth is not None:
                signals.set_queue_depth(depth)
        except Exception:  # noqa: BLE001 - metrics must still render
            signals.set_db_up(False)
            logger.exception("could not refresh live accuracy gauges")
        body, content_type = signals.render_metrics()
        return Response(content=body, media_type=content_type)

    return app


def _recovery_loop(db_target, runtime, stop: threading.Event) -> None:
    while not stop.wait(runtime.recovery_interval_seconds):
        try:
            conn = connect(db_target)
            try:
                n = queue_mod.recovery_reenqueue_pending(
                    conn, min_age_seconds=runtime.recovery_min_age_seconds
                )
                if n:
                    logger.info("Recovery re-enqueued %s pending deliveries", n)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            logger.exception("recovery loop error")


def _db_reachable(path: Path | str) -> bool:
    try:
        conn = connect(path)
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    import uvicorn

    cfg = load_settings()
    runtime = get_settings()
    setup_logging(cfg["logging"]["level"], project_root() / cfg["logging"]["file"])
    register_env_secrets()
    for secret in runtime.secret_values():
        register_secret(secret)
    if os.environ.get("PRPULSE_SKIP_SETTINGS_CHECK") != "1":
        # Local/dev often uses pat + empty App fields; only enforce when serving for real.
        if runtime.webhook_secret_value() or runtime.auth_mode == "app":
            try:
                runtime.require_valid(role="api")
            except RuntimeError as exc:
                logger.error("%s", exc)
                raise SystemExit(1) from exc
    host = os.environ.get("PRPULSE_HOST", "127.0.0.1")
    port = int(os.environ.get("PRPULSE_PORT", "8000"))
    uvicorn.run(
        "src.service.api:create_app",
        factory=True,
        host=host,
        port=port,
    )


if __name__ == "__main__":
    main()

