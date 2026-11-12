"""RQ worker entrypoint plus a recovery loop for pending deliveries."""

from __future__ import annotations

import logging
import os
import threading
import time

from src import setup_logging
from src.data.db import init_db, resolve_target
from src.logging_filter import register_env_secrets, register_secret
from src.runtime_settings import get_settings
from src.service import queue as queue_mod
from src.service import worker_jobs
from src.service.bot import PRBot, build_github_client
from src.service.github_app import resolve_github_client
from src.settings import load_settings, project_root

logger = logging.getLogger("prpulse.worker")


def _recovery_loop(db_target, settings, stop: threading.Event) -> None:
    while not stop.wait(settings.recovery_interval_seconds):
        try:
            from src.data.db import connect
            from src.live import signals
            from src.service.deliveries import count_by_status, count_failed_jobs

            conn = connect(db_target)
            try:
                n = queue_mod.recovery_reenqueue_pending(
                    conn,
                    min_age_seconds=settings.recovery_min_age_seconds,
                )
                if n:
                    logger.info("Recovery re-enqueued %s pending deliveries", n)
                depth = queue_mod.get_queue_depth()
                if depth is not None:
                    signals.set_queue_depth(depth)
                signals.set_pending_deliveries(count_by_status(conn, "pending"))
                signals.set_failed_jobs(count_failed_jobs(conn))
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            logger.exception("recovery loop error")


def main() -> None:
    cfg = load_settings()
    runtime = get_settings()
    setup_logging(cfg["logging"]["level"], project_root() / cfg["logging"]["file"])
    register_env_secrets()
    for secret in runtime.secret_values():
        register_secret(secret)
    # Worker requires Redis; allow dry local runs to skip strict App validation.
    if os.environ.get("PRPULSE_SKIP_SETTINGS_CHECK") != "1":
        runtime.require_valid(role="worker")

    db_target = resolve_target(cfg)
    init_db(db_target)

    from src.models.registry import ModelStore, default_models_dir, derive_version
    from src.service.core import PredictionService
    from src.service.store import ensure_model_version
    from src.data.db import connect

    directory = default_models_dir()
    store = ModelStore.load(directory)
    version = derive_version(directory) if not store.empty else None
    service = PredictionService(store, version, db_target) if version else None
    if version:
        conn = connect(db_target)
        try:
            ensure_model_version(conn, version, artifact_path=str(directory), activate=True)
        finally:
            conn.close()

    default_client = build_github_client(runtime)
    bot = PRBot(default_client, service, cfg=cfg, db_path=db_target)

    def bot_factory(client):
        return PRBot(client, service, cfg=cfg, db_path=db_target)

    def client_resolver(installation_id):
        return resolve_github_client(installation_id, runtime, fallback=default_client)

    worker_jobs.configure(
        bot=bot,
        bot_factory=bot_factory,
        db_target=db_target,
        settings=runtime,
        client_resolver=client_resolver,
    )
    queue_mod.configure(
        redis_url=runtime.redis_url,
        queue_name=runtime.queue_name,
        db_target=db_target,
    )

    stop = threading.Event()
    threading.Thread(target=_recovery_loop, args=(db_target, runtime, stop), daemon=True, name="recovery").start()

    from redis import Redis
    # SimpleWorker runs jobs in-process (no fork). Forked RQ horses hang here when
    # the job touches loaded sklearn/joblib models under Docker Desktop/WSL.
    from rq import SimpleWorker

    logger.info("Starting RQ SimpleWorker on queue %s", runtime.queue_name)
    redis_conn = Redis.from_url(runtime.redis_url)
    SimpleWorker([runtime.queue_name], connection=redis_conn).work(with_scheduler=True)


if __name__ == "__main__":
    main()
