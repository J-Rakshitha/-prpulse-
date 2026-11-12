"""SQLite helpers shared by the API, the bot, and live outcome tracking.

Rows created here for PRs that were never collected get ``ingest_status =
'partial'``. The feature builder only reads ``complete`` rows, so live PRs do
not leak into training data until a real collection pass finishes them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.data.db import to_utc_iso


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return to_utc_iso(utc_now())


def ensure_model_version(
    conn: sqlite3.Connection,
    version: str,
    artifact_path: str | None = None,
    metrics: dict[str, Any] | None = None,
    notes: str | None = None,
    activate: bool = True,
) -> None:
    """Insert a model_versions row when absent; optionally make it the single active one."""
    exists = conn.execute("SELECT 1 FROM model_versions WHERE version = ?", (version,)).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO model_versions (version, created_at, artifact_path, metrics_json, is_active, notes) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (version, now_iso(), artifact_path, json.dumps(metrics) if metrics else None, notes),
        )
    if activate:
        # The partial unique index allows one active row, so clear before setting.
        conn.execute("UPDATE model_versions SET is_active = 0 WHERE is_active = 1 AND version <> ?", (version,))
        conn.execute("UPDATE model_versions SET is_active = 1 WHERE version = ?", (version,))
    conn.commit()


def get_or_create_pr(
    conn: sqlite3.Connection,
    repo_full_name: str,
    number: int,
    author_login: str | None = None,
    created_at: str | None = None,
    draft: bool = False,
    html_url: str | None = None,
) -> int:
    """Return prs.id for (repo, number), creating a partial row when needed."""
    if repo_full_name.count("/") != 1:
        raise ValueError(f"Invalid repo name {repo_full_name!r}")
    owner, name = repo_full_name.split("/")
    ts = now_iso()
    row = conn.execute("SELECT id FROM repos WHERE full_name = ?", (repo_full_name,)).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO repos (owner, name, full_name, collected_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (owner, name, repo_full_name, ts, ts),
        )
        repo_id = int(cur.lastrowid)
    else:
        repo_id = int(row["id"])
    pr = conn.execute("SELECT id FROM prs WHERE repo_id = ? AND number = ?", (repo_id, number)).fetchone()
    if pr is not None:
        return int(pr["id"])
    cur = conn.execute(
        """
        INSERT INTO prs (repo_id, number, author_login, created_at, state, draft, html_url,
                         ingest_status, collected_at, updated_at)
        VALUES (?, ?, ?, ?, 'open', ?, ?, 'partial', ?, ?)
        """,
        (repo_id, number, author_login or "unknown", created_at or ts, int(bool(draft)), html_url, ts, ts),
    )
    conn.commit()
    return int(cur.lastrowid)


def find_pr(conn: sqlite3.Connection, repo_full_name: str, number: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT p.* FROM prs p JOIN repos r ON r.id = p.repo_id WHERE r.full_name = ? AND p.number = ?",
        (repo_full_name, number),
    ).fetchone()


def insert_prediction(
    conn: sqlite3.Connection,
    pr_id: int,
    model_version: str,
    stage: str,
    prediction: dict[str, Any],
    shap: list[dict[str, Any]] | None = None,
    suggestions: list[dict[str, Any]] | None = None,
    coverage_status: str | None = None,
) -> int:
    status = coverage_status if coverage_status in ("seen", "unseen") else prediction.get("coverage_status")
    if status not in ("seen", "unseen"):
        status = None
    cur = conn.execute(
        """
        INSERT INTO predictions (pr_id, model_version, made_at, stage, merge_hours_p10, merge_hours_p50,
                                 merge_hours_p90, prob_merged_within_7d, prob_ci_fail, shap_json, suggestions_json,
                                 coverage_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pr_id,
            model_version,
            now_iso(),
            stage,
            prediction.get("merge_hours_p10"),
            prediction.get("merge_hours_p50"),
            prediction.get("merge_hours_p90"),
            prediction.get("prob_merged_within_7d"),
            prediction.get("prob_ci_fail"),
            json.dumps(shap) if shap is not None else None,
            json.dumps(suggestions) if suggestions is not None else None,
            status,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)
