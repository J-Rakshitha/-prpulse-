"""Persist and update durable webhook delivery rows (schema v4)."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.data.db import to_utc_iso
from src.service.store import now_iso, utc_now

logger = logging.getLogger("prpulse.deliveries")


def extract_meta(event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Pull repo / PR / installation / event_ts out of a GitHub webhook body."""
    repo = None
    if isinstance(payload.get("repository"), dict):
        repo = payload["repository"].get("full_name")
    pr_number = None
    event_ts = None
    action = payload.get("action")
    pr = payload.get("pull_request")
    if isinstance(pr, dict):
        pr_number = pr.get("number")
        event_ts = pr.get("updated_at") or pr.get("closed_at") or pr.get("created_at")
    run = payload.get("workflow_run")
    if pr_number is None and isinstance(run, dict):
        prs = run.get("pull_requests") or []
        if prs:
            pr_number = prs[0].get("number")
        event_ts = event_ts or run.get("updated_at") or run.get("completed_at")
    installation = payload.get("installation")
    installation_id = None
    if isinstance(installation, dict):
        installation_id = installation.get("id")
    return {
        "action": action,
        "repo_full_name": repo,
        "pr_number": int(pr_number) if pr_number is not None else None,
        "installation_id": int(installation_id) if installation_id is not None else None,
        "event_ts": event_ts,
    }


def insert_delivery(
    conn,
    *,
    delivery_id: str,
    event: str,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    """Insert a new delivery. Returns ``(inserted, status)``.

    When ``delivery_id`` already exists, returns ``(False, existing_status)`` without
    changing the row (durable idempotency).
    """
    existing = conn.execute(
        "SELECT status FROM webhook_deliveries WHERE delivery_id = ?",
        (delivery_id,),
    ).fetchone()
    if existing is not None:
        return False, str(existing["status"])
    meta = extract_meta(event, payload)
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO webhook_deliveries (
            delivery_id, event, action, repo_full_name, pr_number, installation_id,
            event_ts, status, attempts, payload_json, received_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
        """,
        (
            delivery_id,
            event,
            meta["action"],
            meta["repo_full_name"],
            meta["pr_number"],
            meta["installation_id"],
            meta["event_ts"],
            json.dumps(payload),
            ts,
            ts,
        ),
    )
    conn.commit()
    return True, "pending"


def mark_enqueued(conn, delivery_id: str) -> None:
    ts = now_iso()
    conn.execute(
        "UPDATE webhook_deliveries SET enqueued_at = ?, updated_at = ? WHERE delivery_id = ?",
        (ts, ts, delivery_id),
    )
    conn.commit()


def load_delivery(conn, delivery_id: str):
    return conn.execute(
        "SELECT * FROM webhook_deliveries WHERE delivery_id = ?",
        (delivery_id,),
    ).fetchone()


def mark_processing(conn, delivery_id: str) -> bool:
    """Claim a delivery for processing. False if already done / claimed by another worker."""
    row = load_delivery(conn, delivery_id)
    if row is None:
        return False
    if row["status"] in ("done", "skipped", "dead"):
        return False
    ts = now_iso()
    conn.execute(
        """
        UPDATE webhook_deliveries
        SET status = 'processing', attempts = attempts + 1, updated_at = ?
        WHERE delivery_id = ?
        """,
        (ts, delivery_id),
    )
    conn.commit()
    return True


def mark_finished(conn, delivery_id: str, result: dict[str, Any], *, status: str | None = None) -> None:
    final = status or ("skipped" if result.get("status") in ("skipped", "ignored", "pong") else "done")
    if result.get("status") == "error":
        final = "failed"
    ts = now_iso()
    conn.execute(
        """
        UPDATE webhook_deliveries
        SET status = ?, result_json = ?, processed_at = ?, updated_at = ?, last_error = NULL
        WHERE delivery_id = ?
        """,
        (final, json.dumps(result), ts, ts, delivery_id),
    )
    conn.commit()


def mark_retry(conn, delivery_id: str, error: str, next_attempt_at: str) -> None:
    ts = now_iso()
    conn.execute(
        """
        UPDATE webhook_deliveries
        SET status = 'pending', last_error = ?, next_attempt_at = ?, updated_at = ?
        WHERE delivery_id = ?
        """,
        (error[:2000], next_attempt_at, ts, delivery_id),
    )
    conn.commit()


def mark_dead(conn, delivery_id: str, error: str) -> None:
    ts = now_iso()
    conn.execute(
        """
        UPDATE webhook_deliveries
        SET status = 'dead', last_error = ?, processed_at = ?, updated_at = ?
        WHERE delivery_id = ?
        """,
        (error[:2000], ts, ts, delivery_id),
    )
    conn.commit()


def record_failed_job(
    conn,
    *,
    delivery_id: str,
    event: str | None,
    repo_full_name: str | None,
    pr_number: int | None,
    error_class: str,
    error_message: str,
    attempts: int,
    retryable: bool,
    payload_json: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO failed_jobs (
            delivery_id, event, repo_full_name, pr_number, error_class, error_message,
            attempts, retryable, payload_json, status, failed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?)
        """,
        (
            delivery_id,
            event,
            repo_full_name,
            pr_number,
            error_class,
            error_message[:2000],
            attempts,
            int(bool(retryable)),
            payload_json,
            now_iso(),
        ),
    )
    conn.commit()


def list_recoverable(conn, *, min_age_seconds: float = 120.0, limit: int = 100) -> list[str]:
    """Pending deliveries that are due (or never enqueued) and old enough to reclaim."""
    cutoff = to_utc_iso(utc_now())  # compare lexically as ISO Z
    # Age filter: received_at older than now - min_age. ISO Z strings compare lexicographically.
    from datetime import timedelta

    age_cut = to_utc_iso(utc_now() - timedelta(seconds=min_age_seconds))
    rows = conn.execute(
        """
        SELECT delivery_id FROM webhook_deliveries
        WHERE status = 'pending'
          AND received_at <= ?
          AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
        ORDER BY received_at
        LIMIT ?
        """,
        (age_cut, cutoff, limit),
    ).fetchall()
    return [str(r["delivery_id"]) for r in rows]


def count_by_status(conn, status: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM webhook_deliveries WHERE status = ?",
        (status,),
    ).fetchone()
    return int(row["n"] if row else 0)


def count_failed_jobs(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM failed_jobs WHERE status = 'failed'"
    ).fetchone()
    return int(row["n"] if row else 0)
