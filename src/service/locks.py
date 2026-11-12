"""Cross-process per-PR processing locks (schema table ``pr_processing_locks``)."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from src.data.db import to_utc_iso
from src.service.store import now_iso, utc_now

logger = logging.getLogger("prpulse.locks")


def acquire_pr_lock(conn, repo_full_name: str, pr_number: int, *, ttl_seconds: int = 300, owner: str | None = None) -> str | None:
    """Try to take the lock. Returns the owner token on success, else None."""
    token = owner or uuid.uuid4().hex
    now = utc_now()
    acquired = to_utc_iso(now)
    expires = to_utc_iso(now + timedelta(seconds=ttl_seconds))
    # Drop expired locks first so a crashed worker does not block forever.
    conn.execute(
        "DELETE FROM pr_processing_locks WHERE repo_full_name = ? AND pr_number = ? AND expires_at <= ?",
        (repo_full_name, pr_number, acquired),
    )
    existing = conn.execute(
        "SELECT owner FROM pr_processing_locks WHERE repo_full_name = ? AND pr_number = ?",
        (repo_full_name, pr_number),
    ).fetchone()
    if existing is not None:
        conn.commit()
        return None
    try:
        conn.execute(
            """
            INSERT INTO pr_processing_locks (repo_full_name, pr_number, owner, acquired_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (repo_full_name, pr_number, token, acquired, expires),
        )
        conn.commit()
        return token
    except Exception:  # noqa: BLE001 - unique conflict under concurrent insert
        conn.rollback()
        return None


def release_pr_lock(conn, repo_full_name: str, pr_number: int, owner: str) -> None:
    conn.execute(
        "DELETE FROM pr_processing_locks WHERE repo_full_name = ? AND pr_number = ? AND owner = ?",
        (repo_full_name, pr_number, owner),
    )
    conn.commit()


def freshen_pr_lock(conn, repo_full_name: str, pr_number: int, owner: str, *, ttl_seconds: int = 300) -> None:
    expires = to_utc_iso(utc_now() + timedelta(seconds=ttl_seconds))
    conn.execute(
        """
        UPDATE pr_processing_locks SET expires_at = ?
        WHERE repo_full_name = ? AND pr_number = ? AND owner = ?
        """,
        (expires, repo_full_name, pr_number, owner),
    )
    conn.commit()
