"""Streamlit dashboard.

    streamlit run src/dashboard/app.py

Sections are chosen in the sidebar. Every section handles a missing database,
missing tables, or an empty table by saying so instead of failing.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `streamlit run` only adds the script folder
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from src.data.db import resolve_db_path  # noqa: E402
from src.live.outcomes import compute_errors  # noqa: E402
from src.models.registry import default_models_dir, read_registry, ModelStore  # noqa: E402
from src.settings import load_settings  # noqa: E402

SECTIONS = ("Live accuracy", "Open PRs: stuck risk", "Repo health", "Model info")


def open_db_readonly(path: Path) -> sqlite3.Connection | None:
    """Open an existing DB without creating one. Returns None when the file is missing."""
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _query_df(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def fmt(value: float | None, digits: int = 2, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.{digits}f}{suffix}"


def section_live_accuracy(conn: sqlite3.Connection, cfg: dict) -> None:
    st.header("Live accuracy")
    st.caption("Earliest stored prediction per PR and stage, compared with what actually happened.")
    summary = compute_errors(conn, cfg=cfg)
    if summary["n_resolved"] == 0:
        st.info("No live data yet. Accuracy appears here after predicted PRs are merged or closed.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Resolved PRs", summary["n_resolved"])
    c2.metric("MAE (hours)", fmt(summary["mae_hours"]))
    c3.metric("10-90 coverage", fmt(summary["interval_coverage"], 3))
    c4.metric("Brier, merged in 7d", fmt(summary["brier_merged_7d"], 3))
    by_stage = pd.DataFrame(summary["by_stage"]).T
    if not by_stage.empty:
        st.subheader("By stage")
        st.dataframe(by_stage)


def section_stuck(conn: sqlite3.Connection) -> None:
    st.header("Open PRs: stuck risk")
    st.warning("Placeholder heuristic: an open PR is flagged when its age exceeds its stored p90 merge-time forecast. It is not a trained stuck-PR model.")
    df = _query_df(
        conn,
        """
        SELECT r.full_name AS repo, p.number, p.created_at, pr.merge_hours_p50, pr.merge_hours_p90, pr.made_at
        FROM prs p
        JOIN repos r ON r.id = p.repo_id
        JOIN predictions pr ON pr.id = (SELECT MAX(id) FROM predictions WHERE pr_id = p.id)
        WHERE p.state = 'open'
        ORDER BY p.created_at DESC
        LIMIT 200
        """,
    )
    if df.empty:
        st.info("No open PRs with stored predictions yet.")
        return
    created = pd.to_datetime(df["created_at"], utc=True)
    df["age_hours"] = (datetime.now(timezone.utc) - created).dt.total_seconds() / 3600.0
    df["over_p90"] = df["age_hours"] > df["merge_hours_p90"]
    st.dataframe(df.sort_values(["over_p90", "age_hours"], ascending=False))


def section_repo_health(conn: sqlite3.Connection) -> None:
    st.header("Repo health")
    counts = _query_df(
        conn,
        """
        SELECT r.full_name AS repo,
               COUNT(*) AS prs,
               SUM(p.state = 'open') AS open,
               SUM(p.state = 'merged') AS merged,
               SUM(p.state = 'closed') AS closed_unmerged
        FROM prs p JOIN repos r ON r.id = p.repo_id
        WHERE p.is_bot = 0
        GROUP BY r.full_name
        ORDER BY prs DESC
        """,
    )
    if counts.empty:
        st.info("No PRs in the database yet. Run the collector first.")
        return
    merged = _query_df(
        conn,
        """
        SELECT r.full_name AS repo,
               (julianday(p.merged_at) - julianday(p.created_at)) * 24.0 AS merge_hours
        FROM prs p JOIN repos r ON r.id = p.repo_id
        WHERE p.is_bot = 0 AND p.state = 'merged'
        """,
    )
    med = merged.groupby("repo")["merge_hours"].median().rename("median_merge_hours")
    p90 = merged.groupby("repo")["merge_hours"].quantile(0.9).rename("p90_merge_hours")
    table = counts.merge(med, on="repo", how="left").merge(p90, on="repo", how="left")
    st.caption("Non-bot PRs stored in the local database (all periods).")
    st.dataframe(table)


def section_model_info(conn: sqlite3.Connection | None) -> None:
    st.header("Model info")
    models_dir = default_models_dir()
    registry = read_registry(models_dir.parent / "registry.json")
    st.write(f"Active version in registry: `{registry.get('active') or 'none'}`")
    store = ModelStore.load(models_dir)
    if store.empty:
        st.info("No model bundles in artifacts/models. Run `python tasks.py train`.")
    else:
        st.dataframe(pd.DataFrame(store.describe()))
    if registry.get("versions"):
        st.subheader("Registry versions")
        st.code(json.dumps(registry["versions"], indent=2, default=str), language="json")
    if conn is not None:
        mv = _query_df(conn, "SELECT version, created_at, is_active, notes FROM model_versions ORDER BY id DESC")
        st.subheader("model_versions table")
        if mv.empty:
            st.info("No rows yet. The API registers a version when it first starts with models loaded.")
        else:
            st.dataframe(mv)


def main() -> None:
    st.set_page_config(page_title="PR Pulse", layout="wide")
    st.title("PR Pulse")
    section = st.sidebar.radio("Section", SECTIONS)
    cfg = load_settings()
    db_path = resolve_db_path(cfg)
    conn = open_db_readonly(db_path)
    if conn is None:
        st.warning(f"Database not found at {db_path}. Run `python tasks.py init-db`.")
        if section == "Model info":
            section_model_info(None)
        return
    try:
        if section == "Live accuracy":
            section_live_accuracy(conn, cfg)
        elif section == "Open PRs: stuck risk":
            section_stuck(conn)
        elif section == "Repo health":
            section_repo_health(conn)
        else:
            section_model_info(conn)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        st.error(f"Could not read the database ({exc}). Has `python tasks.py init-db` been run on it?")
    finally:
        conn.close()


main()
