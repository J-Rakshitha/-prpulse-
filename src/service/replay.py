"""Replay dead-letter / failed_jobs rows back onto the queue."""

from __future__ import annotations

import argparse
import logging
from typing import Any

from src.data.db import connect, init_db, resolve_target
from src.runtime_settings import get_settings
from src.service import queue as queue_mod
from src.service.store import now_iso
from src.settings import load_settings

logger = logging.getLogger("prpulse.replay")


def replay_failed(conn, *, delivery_id: str | None = None, limit: int = 50) -> list[str]:
    """Reset matching failed_jobs to replayed and re-queue their deliveries."""
    if delivery_id:
        rows = conn.execute(
            "SELECT id, delivery_id FROM failed_jobs WHERE status = 'failed' AND delivery_id = ?",
            (delivery_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, delivery_id FROM failed_jobs WHERE status = 'failed' ORDER BY failed_at LIMIT ?",
            (limit,),
        ).fetchall()
    replayed: list[str] = []
    ts = now_iso()
    for row in rows:
        did = str(row["delivery_id"])
        conn.execute(
            """
            UPDATE webhook_deliveries
            SET status = 'pending', next_attempt_at = NULL, last_error = NULL, updated_at = ?
            WHERE delivery_id = ?
            """,
            (ts, did),
        )
        conn.execute(
            "UPDATE failed_jobs SET status = 'replayed', replayed_at = ? WHERE id = ?",
            (ts, row["id"]),
        )
        conn.commit()
        if not queue_mod.enqueue_delivery(did):
            logger.warning("Replay left delivery %s pending (Redis unavailable)", did)
        replayed.append(did)
    return replayed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Replay failed webhook jobs")
    parser.add_argument("--delivery-id", default=None)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    cfg = load_settings()
    runtime = get_settings()
    target = resolve_target(cfg)
    init_db(target)
    queue_mod.configure(redis_url=runtime.redis_url, queue_name=runtime.queue_name, db_target=target)
    conn = connect(target)
    try:
        ids = replay_failed(conn, delivery_id=args.delivery_id, limit=args.limit)
    finally:
        conn.close()
    print(f"replayed {len(ids)}: {', '.join(ids) if ids else '(none)'}")


if __name__ == "__main__":
    main()
