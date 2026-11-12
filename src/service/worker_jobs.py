"""Background job: load a persisted delivery and run the bot / install handlers."""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import timedelta
from typing import Any, Callable

from src.data.db import connect, to_utc_iso
from src.live import signals
from src.runtime_settings import RuntimeSettings, get_settings
from src.service import deliveries as deliv
from src.service.locks import acquire_pr_lock, release_pr_lock
from src.service.store import now_iso, utc_now

logger = logging.getLogger("prpulse.worker")

# Injected by api lifespan / worker main / tests.
_bot: Any = None
_bot_factory: Callable[[Any], Any] | None = None
_db_target: Any = None
_settings: RuntimeSettings | None = None
_client_resolver: Callable[[int | None], Any] | None = None


class TransientJobError(Exception):
    """Retryable failure (GitHub 5xx, rate limit, lock busy, timeout)."""

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class PermanentJobError(Exception):
    """Non-retryable failure; goes straight to failed_jobs."""


def configure(
    *,
    bot: Any = None,
    bot_factory: Callable[[Any], Any] | None = None,
    db_target: Any = None,
    settings: RuntimeSettings | None = None,
    client_resolver: Callable[[int | None], Any] | None = None,
) -> None:
    global _bot, _bot_factory, _db_target, _settings, _client_resolver
    if bot is not None:
        _bot = bot
    if bot_factory is not None:
        _bot_factory = bot_factory
    if db_target is not None:
        _db_target = db_target
    if settings is not None:
        _settings = settings
    if client_resolver is not None:
        _client_resolver = client_resolver


def _runtime() -> RuntimeSettings:
    return _settings if _settings is not None else get_settings()


def backoff_seconds(attempt: int, settings: RuntimeSettings | None = None) -> float:
    cfg = settings or _runtime()
    base = cfg.job_backoff_base_seconds * (2 ** max(0, attempt - 1))
    capped = min(base, cfg.job_backoff_cap_seconds)
    jitter = random.uniform(0, capped * 0.25)
    return capped + jitter


def process_delivery(delivery_id: str, db_target: Any = None) -> dict[str, Any]:
    """RQ entrypoint and test helper. Always returns a result dict."""
    target = db_target if db_target is not None else _db_target
    if target is None:
        raise RuntimeError("worker_jobs: db_target is not configured")
    settings = _runtime()
    conn = connect(target)
    try:
        row = deliv.load_delivery(conn, delivery_id)
        if row is None:
            return {"status": "error", "reason": "delivery not found"}
        if row["status"] in ("done", "skipped", "dead"):
            try:
                return json.loads(row["result_json"] or "{}") or {"status": row["status"]}
            except json.JSONDecodeError:
                return {"status": row["status"]}
        if not deliv.mark_processing(conn, delivery_id):
            return {"status": "duplicate", "delivery": delivery_id}

        payload = json.loads(row["payload_json"] or "{}")
        event = row["event"]
        repo = row["repo_full_name"]
        pr_number = row["pr_number"]
        installation_id = row["installation_id"]
        attempts = int(row["attempts"]) + 1  # mark_processing already incremented

        lock_owner = None
        try:
            if repo and pr_number is not None:
                lock_owner = _wait_for_lock(conn, repo, int(pr_number), settings)
                if _should_skip_stale_open(conn, repo, int(pr_number), event, payload):
                    result = {"status": "skipped", "reason": "stale open after later closed"}
                    deliv.mark_finished(conn, delivery_id, result, status="skipped")
                    signals.record_webhook(event, result["status"])
                    return result

            result = _dispatch(event, payload, installation_id)
            if isinstance(result, dict) and result.get("status") == "error":
                # Soft errors from the bot (e.g. GitHub 403): no comment, mark failed.
                # Transient HTTP/transport failures raise instead and are retried.
                reason = str(result.get("reason") or "bot error")
                deliv.mark_finished(conn, delivery_id, result, status="failed")
                deliv.record_failed_job(
                    conn,
                    delivery_id=delivery_id,
                    event=event,
                    repo_full_name=repo,
                    pr_number=pr_number,
                    error_class="BotError",
                    error_message=reason,
                    attempts=attempts,
                    retryable=reason.startswith("github_api_5"),
                    payload_json=row["payload_json"],
                )
                signals.record_webhook(event, "error")
                signals.record_worker_job("failed")
                return result
            deliv.mark_finished(conn, delivery_id, result)
            status = result.get("status", "unknown") if isinstance(result, dict) else "ok"
            signals.record_webhook(event, status)
            signals.record_worker_job("ok" if status in ("created", "updated", "unchanged", "done", "skipped", "outcome_recorded", "pong") else status)
            return result
        except TransientJobError as exc:
            signals.record_worker_job("retry")
            return _handle_failure(
                conn,
                delivery_id=delivery_id,
                row=row,
                attempts=attempts,
                exc=exc,
                retryable=True,
                retry_after=exc.retry_after,
            )
        except PermanentJobError as exc:
            signals.record_worker_job("failed")
            return _handle_failure(
                conn,
                delivery_id=delivery_id,
                row=row,
                attempts=attempts,
                exc=exc,
                retryable=False,
            )
        except Exception as exc:  # noqa: BLE001
            from src.service.bot import GitHubRateLimitError
            import httpx

            if isinstance(exc, GitHubRateLimitError):
                return _handle_failure(
                    conn,
                    delivery_id=delivery_id,
                    row=row,
                    attempts=attempts,
                    exc=exc,
                    retryable=True,
                    retry_after=exc.retry_after,
                )
            if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
                return _handle_failure(
                    conn,
                    delivery_id=delivery_id,
                    row=row,
                    attempts=attempts,
                    exc=exc,
                    retryable=True,
                )
            logger.exception(
                "delivery failed delivery_id=%s repo=%s pr=%s",
                delivery_id,
                repo,
                pr_number,
            )
            return _handle_failure(
                conn,
                delivery_id=delivery_id,
                row=row,
                attempts=attempts,
                exc=exc,
                retryable=True,
            )
        finally:
            if lock_owner and repo and pr_number is not None:
                release_pr_lock(conn, repo, int(pr_number), lock_owner)
    finally:
        conn.close()


def _wait_for_lock(conn, repo: str, pr_number: int, settings: RuntimeSettings) -> str:
    deadline = time.monotonic() + min(30.0, settings.pr_lock_ttl_seconds)
    while True:
        owner = acquire_pr_lock(conn, repo, pr_number, ttl_seconds=settings.pr_lock_ttl_seconds)
        if owner:
            return owner
        if time.monotonic() >= deadline:
            raise TransientJobError("pr lock busy", retry_after=settings.job_backoff_base_seconds)
        time.sleep(0.05)


def _should_skip_stale_open(conn, repo: str, pr_number: int, event: str, payload: dict[str, Any]) -> bool:
    """Out-of-order: skip commenting if a later ``closed`` for this PR already finished."""
    if event != "pull_request":
        return False
    action = payload.get("action")
    if action not in ("opened", "reopened", "synchronize", "ready_for_review"):
        return False
    event_ts = (payload.get("pull_request") or {}).get("updated_at") or (payload.get("pull_request") or {}).get("created_at")
    # Avoid ``? IS NULL`` — Postgres cannot infer the parameter type.
    if event_ts is None:
        rows = conn.execute(
            """
            SELECT 1 FROM webhook_deliveries
            WHERE repo_full_name = ? AND pr_number = ? AND action = 'closed'
              AND status IN ('done', 'skipped')
            LIMIT 1
            """,
            (repo, pr_number),
        ).fetchone()
    else:
        rows = conn.execute(
            """
            SELECT 1 FROM webhook_deliveries
            WHERE repo_full_name = ? AND pr_number = ? AND action = 'closed'
              AND status IN ('done', 'skipped')
              AND (event_ts IS NULL OR event_ts >= ?)
            LIMIT 1
            """,
            (repo, pr_number, event_ts),
        ).fetchone()
    return rows is not None


def _dispatch(event: str, payload: dict[str, Any], installation_id: int | None) -> dict[str, Any]:
    if event == "ping":
        return {"status": "pong"}
    if event in ("installation", "installation_repositories"):
        return _handle_installation(event, payload)

    bot = _resolve_bot(installation_id)
    if bot is None:
        return {"status": "ignored", "reason": "bot not configured"}
    if event == "pull_request":
        return bot.on_pull_request(payload)
    if event == "workflow_run":
        return bot.on_workflow_run(payload)
    return {"status": "ignored", "reason": f"event {event!r} not handled"}


def _resolve_bot(installation_id: int | None) -> Any:
    # Prefer the shared bot so tests can mutate ``bot.service`` / ``bot.client``.
    if _bot is not None:
        if _client_resolver is not None:
            client = _client_resolver(installation_id)
            if client is not None:
                _bot.client = client
        return _bot
    if _bot_factory is not None:
        client = _client_resolver(installation_id) if _client_resolver else None
        return _bot_factory(client)
    return None


def _handle_installation(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    from src.service.installations import apply_installation_event

    target = _db_target
    if target is None:
        return {"status": "ignored", "reason": "no database"}
    conn = connect(target)
    try:
        return apply_installation_event(conn, event, payload)
    finally:
        conn.close()


def _handle_failure(
    conn,
    *,
    delivery_id: str,
    row,
    attempts: int,
    exc: BaseException,
    retryable: bool,
    retry_after: float | None = None,
) -> dict[str, Any]:
    settings = _runtime()
    message = f"{type(exc).__name__}: {exc}"
    logger.error(
        "job failure delivery_id=%s repo=%s pr=%s attempts=%s retryable=%s error=%s",
        delivery_id,
        row["repo_full_name"],
        row["pr_number"],
        attempts,
        retryable,
        message,
    )
    max_attempts = settings.job_max_attempts
    if retryable and attempts < max_attempts:
        delay = retry_after if retry_after is not None else backoff_seconds(attempts, settings)
        next_at = to_utc_iso(utc_now() + timedelta(seconds=delay))
        deliv.mark_retry(conn, delivery_id, message, next_at)
        # Best-effort re-enqueue for when Redis is up.
        try:
            from src.service.queue import enqueue_delivery

            enqueue_delivery(delivery_id)
        except Exception:  # noqa: BLE001
            pass
        signals.record_webhook(row["event"], "retry")
        return {"status": "retry", "reason": message, "next_attempt_at": next_at, "attempts": attempts}

    deliv.mark_dead(conn, delivery_id, message)
    deliv.record_failed_job(
        conn,
        delivery_id=delivery_id,
        event=row["event"],
        repo_full_name=row["repo_full_name"],
        pr_number=row["pr_number"],
        error_class=type(exc).__name__,
        error_message=message,
        attempts=attempts,
        retryable=retryable,
        payload_json=row["payload_json"],
    )
    signals.record_webhook(row["event"], "dead")
    signals.record_worker_job("failed")
    signals.set_failed_jobs(deliv.count_failed_jobs(conn))
    return {"status": "dead", "reason": message, "attempts": attempts}
