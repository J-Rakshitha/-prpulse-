"""Per-repo training-coverage check for unseen-repo comment fallback."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("prpulse.coverage")

DEFAULT_MIN_RESOLVED = 50


def min_resolved_threshold(explicit: int | None = None) -> int:
    if explicit is not None:
        return max(0, int(explicit))
    raw = os.environ.get("UNSEEN_REPO_MIN_RESOLVED", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.warning("Invalid UNSEEN_REPO_MIN_RESOLVED=%r; using %s", raw, DEFAULT_MIN_RESOLVED)
    return DEFAULT_MIN_RESOLVED


def count_resolved_prs(conn, repo_full_name: str) -> int:
    """Resolved PRs in the DB for this repo (prefer complete ingest rows)."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM prs p
        JOIN repos r ON r.id = p.repo_id
        WHERE r.full_name = ?
          AND (p.merged_at IS NOT NULL OR p.closed_at IS NOT NULL)
          AND p.ingest_status = 'complete'
        """,
        (repo_full_name,),
    ).fetchone()
    n = int(row["n"] if row else 0)
    if n > 0:
        return n
    # Fall back to any resolved row (live outcomes may not have ingest_status=complete).
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM prs p
        JOIN repos r ON r.id = p.repo_id
        WHERE r.full_name = ?
          AND (p.merged_at IS NOT NULL OR p.closed_at IS NOT NULL)
        """,
        (repo_full_name,),
    ).fetchone()
    return int(row["n"] if row else 0)


def coverage_status_for_repo(conn, repo_full_name: str, *, threshold: int | None = None) -> str:
    n = count_resolved_prs(conn, repo_full_name)
    return "seen" if n >= min_resolved_threshold(threshold) else "unseen"


def coarse_bucket(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 24:
        return "under 1 day"
    if hours <= 24 * 7:
        return "1 to 7 days"
    return "over 7 days"


def adapt_result_for_coverage(result: dict[str, Any], status: str) -> dict[str, Any]:
    """Annotate a prediction result; strip suggestions for unseen repos."""
    out = dict(result)
    out["coverage_status"] = status
    if status == "unseen":
        out["coarse_bucket"] = coarse_bucket(out.get("merge_hours_p50"))
        out["suggestions"] = []
        # Keep probabilities; hide precise hour figures from the comment renderer.
        out["unseen"] = True
    else:
        out["unseen"] = False
    return out
