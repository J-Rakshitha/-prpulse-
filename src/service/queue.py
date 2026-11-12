"""Redis + RQ enqueue helpers. Webhook handler never blocks on the worker."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("prpulse.queue")

# Lazily configured by api lifespan / worker main.
_redis_url: str = ""
_queue_name: str = "prpulse"
_db_target: Any = None
_job_timeout: int = 600


def configure(*, redis_url: str = "", queue_name: str = "prpulse", db_target: Any = None, job_timeout: int = 600) -> None:
    global _redis_url, _queue_name, _db_target, _job_timeout
    _redis_url = (redis_url or "").strip()
    _queue_name = queue_name or "prpulse"
    if db_target is not None:
        _db_target = db_target
    _job_timeout = job_timeout


def redis_available(url: str | None = None) -> bool:
    target = (url if url is not None else _redis_url).strip()
    if not target:
        return False
    try:
        from redis import Redis

        client = Redis.from_url(target)
        client.ping()
        return True
    except Exception:  # noqa: BLE001
        return False


def _queue():
    from redis import Redis
    from rq import Queue

    if not _redis_url:
        raise RuntimeError("REDIS_URL is not configured")
    return Queue(_queue_name, connection=Redis.from_url(_redis_url))


def enqueue_delivery(delivery_id: str) -> bool:
    """Enqueue ``process_delivery``. False when Redis is down or unset."""
    if not _redis_url:
        return False
    try:
        q = _queue()
        q.enqueue(
            "src.service.worker_jobs.process_delivery",
            delivery_id,
            job_id=f"delivery-{delivery_id}",
            job_timeout=_job_timeout,
            result_ttl=3600,
            failure_ttl=86400,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception("Failed to enqueue delivery %s", delivery_id)
        return False


def get_queue_depth() -> int | None:
    if not _redis_url:
        return None
    try:
        return int(_queue().count)
    except Exception:  # noqa: BLE001
        return None


def recovery_reenqueue_pending(conn, *, min_age_seconds: float = 120.0, limit: int = 100) -> int:
    """Re-enqueue pending deliveries when Redis is back. Returns how many were enqueued."""
    from src.service.deliveries import list_recoverable, mark_enqueued

    if not redis_available():
        return 0
    ids = list_recoverable(conn, min_age_seconds=min_age_seconds, limit=limit)
    done = 0
    for delivery_id in ids:
        if enqueue_delivery(delivery_id):
            mark_enqueued(conn, delivery_id)
            done += 1
    return done


def process_delivery_now(delivery_id: str, db_target: Any = None) -> dict[str, Any]:
    """Synchronously process one delivery (used by unit tests after POST /webhook)."""
    from src.service import worker_jobs

    target = db_target if db_target is not None else _db_target
    return worker_jobs.process_delivery(delivery_id, db_target=target)
