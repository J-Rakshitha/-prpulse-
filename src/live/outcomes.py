"""Record what happened to PRs we predicted, and measure the errors.

Outcomes arrive from webhooks in any order: a CI result can come before the
merge, and a closed PR can be reopened. Every writer here is an upsert that only
touches the columns it knows about.

Accuracy uses the *earliest* prediction per (PR, stage), because that is the
forecast a user would have seen, not one refreshed just before the merge.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

import numpy as np

from src.data.db import to_utc_iso
from src.service.store import find_pr, now_iso
from src.settings import load_settings


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _norm_ts(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return to_utc_iso(value)
    parsed = parse_iso(value)
    return to_utc_iso(parsed) if parsed else None


def _cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return cfg if cfg is not None else load_settings()


def _upsert(conn: sqlite3.Connection, pr_id: int, **cols: Any) -> None:
    """Insert an outcomes row or update only the provided non-None columns."""
    known = ("actual_merge_hours", "merged", "ci_first_conclusion", "ci_failed_first_run", "resolved_at")
    provided = {k: v for k, v in cols.items() if k in known and v is not None}
    conn.execute(
        "INSERT OR IGNORE INTO outcomes (pr_id, recorded_at) VALUES (?, ?)",
        (pr_id, now_iso()),
    )
    if provided:
        sets = ", ".join(f"{k} = ?" for k in provided)
        conn.execute(
            f"UPDATE outcomes SET {sets}, recorded_at = ? WHERE pr_id = ?",
            (*provided.values(), now_iso(), pr_id),
        )
    conn.commit()


def on_pr_closed(
    conn: sqlite3.Connection,
    repo_full_name: str,
    number: int,
    merged: bool,
    closed_at: str | datetime | None = None,
    merged_at: str | datetime | None = None,
) -> int | None:
    """Record a merge or a close without merge. Returns pr_id, or None when the PR is unknown."""
    pr = find_pr(conn, repo_full_name, number)
    if pr is None:
        return None
    pr_id = int(pr["id"])
    resolved = _norm_ts(merged_at if merged and merged_at else closed_at) or now_iso()
    hours = None
    if merged:
        created = parse_iso(pr["created_at"])
        end = parse_iso(resolved)
        hours = max((end - created).total_seconds() / 3600.0, 0.0)
        state_sql = ("merged", resolved, resolved)
    else:
        state_sql = ("closed", None, resolved)
    if pr["state"] == "open":
        conn.execute(
            "UPDATE prs SET state = ?, merged_at = ?, closed_at = ?, updated_at = ? WHERE id = ?",
            (*state_sql, now_iso(), pr_id),
        )
    _upsert(conn, pr_id, merged=1 if merged else 0, actual_merge_hours=hours, resolved_at=resolved)
    return pr_id


def on_pr_reopened(conn: sqlite3.Connection, repo_full_name: str, number: int) -> int | None:
    """Undo a resolution: a reopened PR has no final outcome yet."""
    pr = find_pr(conn, repo_full_name, number)
    if pr is None:
        return None
    pr_id = int(pr["id"])
    conn.execute(
        "UPDATE outcomes SET merged = NULL, actual_merge_hours = NULL, resolved_at = NULL, recorded_at = ? "
        "WHERE pr_id = ?",
        (now_iso(), pr_id),
    )
    if pr["state"] != "merged":
        conn.execute(
            "UPDATE prs SET state = 'open', closed_at = NULL, merged_at = NULL, was_reopened = 1, updated_at = ? "
            "WHERE id = ?",
            (now_iso(), pr_id),
        )
    conn.commit()
    return pr_id


def on_ci_completed(
    conn: sqlite3.Connection,
    repo_full_name: str,
    number: int,
    conclusion: str | None,
    cfg: dict[str, Any] | None = None,
) -> int | None:
    """Record the first CI run that has a usable conclusion. Later runs are ignored."""
    cfg = _cfg(cfg)
    if conclusion not in cfg["ci"]["label_conclusions"]:
        return None
    pr = find_pr(conn, repo_full_name, number)
    if pr is None:
        return None
    pr_id = int(pr["id"])
    row = conn.execute("SELECT ci_first_conclusion FROM outcomes WHERE pr_id = ?", (pr_id,)).fetchone()
    if row is not None and row["ci_first_conclusion"] is not None:
        return pr_id
    failed = 1 if conclusion in cfg["ci"]["failure_conclusions"] else 0
    _upsert(conn, pr_id, ci_first_conclusion=conclusion, ci_failed_first_run=failed)
    return pr_id


def _brier(p: np.ndarray, y: np.ndarray) -> float | None:
    return float(np.mean((p - y) ** 2)) if len(p) else None


def compute_errors(
    conn: sqlite3.Connection,
    model_version: str | None = None,
    stage: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare earliest predictions with recorded outcomes.

    Metrics are None when there is nothing to score. Nothing is imputed.
    """
    cfg = _cfg(cfg)
    horizon_h = float(cfg["thresholds"]["merged_within_days"]) * 24.0
    where = ["o.resolved_at IS NOT NULL"]
    params: list[Any] = []
    if model_version:
        where.append("p.model_version = ?")
        params.append(model_version)
    if stage:
        where.append("p.stage = ?")
        params.append(stage)
    where_sql = " AND ".join(where)
    sub_filter = "AND p2.model_version = ?" if model_version else ""
    rows = conn.execute(
        f"""
        SELECT p.pr_id, p.stage, p.model_version, p.merge_hours_p10, p.merge_hours_p50, p.merge_hours_p90,
               p.prob_merged_within_7d, p.prob_ci_fail,
               o.merged, o.actual_merge_hours, o.ci_failed_first_run
        FROM predictions p
        JOIN outcomes o ON o.pr_id = p.pr_id
        WHERE {where_sql}
          AND p.id = (
              SELECT MIN(p2.id) FROM predictions p2
              WHERE p2.pr_id = p.pr_id AND p2.stage = p.stage
                {sub_filter}
          )
        """,
        params + ([model_version] if model_version else []),
    ).fetchall()

    def summarize(subset: list[sqlite3.Row]) -> dict[str, Any]:
        merged = [r for r in subset if r["merged"] == 1 and r["actual_merge_hours"] is not None and r["merge_hours_p50"] is not None]
        err = np.array([abs(r["merge_hours_p50"] - r["actual_merge_hours"]) for r in merged], dtype=float)
        covered = [
            r["merge_hours_p10"] <= r["actual_merge_hours"] <= r["merge_hours_p90"]
            for r in merged
            if r["merge_hours_p10"] is not None and r["merge_hours_p90"] is not None
        ]
        p7 = [r for r in subset if r["prob_merged_within_7d"] is not None and r["merged"] is not None]
        p7_p = np.array([r["prob_merged_within_7d"] for r in p7], dtype=float)
        p7_y = np.array(
            [1.0 if (r["merged"] == 1 and r["actual_merge_hours"] is not None and r["actual_merge_hours"] <= horizon_h) else 0.0 for r in p7],
            dtype=float,
        )
        ci = [r for r in subset if r["prob_ci_fail"] is not None and r["ci_failed_first_run"] is not None]
        ci_p = np.array([r["prob_ci_fail"] for r in ci], dtype=float)
        ci_y = np.array([r["ci_failed_first_run"] for r in ci], dtype=float)
        return {
            "n_resolved": len(subset),
            "n_merged": len(merged),
            "mae_hours": float(err.mean()) if len(err) else None,
            "median_ae_hours": float(np.median(err)) if len(err) else None,
            "interval_coverage": float(np.mean(covered)) if covered else None,
            "n_merged_7d_scored": len(p7),
            "brier_merged_7d": _brier(p7_p, p7_y),
            "n_ci_scored": len(ci),
            "brier_ci_fail": _brier(ci_p, ci_y),
        }

    out = summarize(rows)
    out["by_stage"] = {s: summarize([r for r in rows if r["stage"] == s]) for s in sorted({r["stage"] for r in rows})}
    return out
