"""Privacy retention: clear raw webhook payloads older than PAYLOAD_RETENTION_DAYS."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.data.db import connect, to_utc_iso
from src.runtime_settings import get_settings
from src.service.store import utc_now

logger = logging.getLogger("prpulse.retention")


def clear_old_payloads(conn, *, older_than_days: int, now: datetime | None = None) -> dict[str, int | str]:
    """NULL out ``payload_json`` on webhook_deliveries (and failed_jobs) older than N days.

    Keeps delivery rows for idempotency; only the raw body is removed.
    """
    if older_than_days < 1:
        raise ValueError("older_than_days must be >= 1")
    moment = now or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = moment - timedelta(days=older_than_days)
    cutoff_iso = to_utc_iso(cutoff)
    updated_at = to_utc_iso(utc_now())

    cur = conn.execute(
        """
        UPDATE webhook_deliveries
        SET payload_json = NULL, updated_at = ?
        WHERE payload_json IS NOT NULL
          AND received_at < ?
        """,
        (updated_at, cutoff_iso),
    )
    deliveries = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    failed = 0
    try:
        cur2 = conn.execute(
            """
            UPDATE failed_jobs
            SET payload_json = NULL
            WHERE payload_json IS NOT NULL
              AND failed_at < ?
            """,
            (cutoff_iso,),
        )
        failed = cur2.rowcount if cur2.rowcount is not None and cur2.rowcount >= 0 else 0
    except Exception:  # noqa: BLE001 - table may be absent on very old DBs
        logger.debug("failed_jobs payload retention skipped", exc_info=True)
    conn.commit()
    return {"deliveries_cleared": int(deliveries), "failed_jobs_cleared": int(failed), "cutoff": cutoff_iso}


def run_retention(*, db_target: Any = None, days: int | None = None) -> dict[str, int | str]:
    from src.data.db import init_db, resolve_target
    from src.settings import load_settings

    settings = get_settings()
    cfg = load_settings()
    target = db_target if db_target is not None else resolve_target(cfg)
    init_db(target)
    retention_days = days if days is not None else int(settings.payload_retention_days)
    conn = connect(target)
    try:
        result = clear_old_payloads(conn, older_than_days=retention_days)
    finally:
        conn.close()
    logger.info("retention cleared %s", result)
    return result
