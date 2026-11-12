"""Remove stored application data for a repo or author (privacy deletion)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.db import connect, init_db, resolve_db_path
from src.settings import load_settings


def _table_exists(conn, name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {name} LIMIT 1")
        return True
    except Exception:
        return False


def delete_repo(conn, repo_full_name: str) -> dict[str, int]:
    row = conn.execute("SELECT id FROM repos WHERE full_name = ?", (repo_full_name,)).fetchone()
    if row is None:
        # Still clear v4 tables keyed by repo name even if no repos row.
        counts = {"repos": 0, "prs": 0}
        for table, sql, params in (
            ("webhook_deliveries", "DELETE FROM webhook_deliveries WHERE repo_full_name = ?", (repo_full_name,)),
            ("failed_jobs", "DELETE FROM failed_jobs WHERE repo_full_name = ?", (repo_full_name,)),
            ("installation_repos", "DELETE FROM installation_repos WHERE repo_full_name = ?", (repo_full_name,)),
            ("pr_processing_locks", "DELETE FROM pr_processing_locks WHERE repo_full_name = ?", (repo_full_name,)),
        ):
            if _table_exists(conn, table):
                cur = conn.execute(sql, params)
                counts[table] = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        conn.commit()
        return counts

    repo_id = int(row["id"] if hasattr(row, "keys") else row[0])
    pr_ids = [int(r["id"] if hasattr(r, "keys") else r[0]) for r in conn.execute("SELECT id FROM prs WHERE repo_id = ?", (repo_id,))]
    counts: dict[str, int] = {"prs": len(pr_ids), "repos": 1}
    for table in ("predictions", "outcomes", "pr_files", "pr_commits", "pr_events", "ci_runs"):
        if pr_ids and _table_exists(conn, table):
            placeholders = ",".join("?" * len(pr_ids))
            cur = conn.execute(f"DELETE FROM {table} WHERE pr_id IN ({placeholders})", pr_ids)
            counts[table] = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        else:
            counts[table] = 0
    conn.execute("DELETE FROM prs WHERE repo_id = ?", (repo_id,))
    # failed_jobs first if it references delivery_id.
    for table, sql, params in (
        ("failed_jobs", "DELETE FROM failed_jobs WHERE repo_full_name = ?", (repo_full_name,)),
        ("webhook_deliveries", "DELETE FROM webhook_deliveries WHERE repo_full_name = ?", (repo_full_name,)),
        ("installation_repos", "DELETE FROM installation_repos WHERE repo_full_name = ?", (repo_full_name,)),
        ("pr_processing_locks", "DELETE FROM pr_processing_locks WHERE repo_full_name = ?", (repo_full_name,)),
    ):
        if _table_exists(conn, table):
            cur = conn.execute(sql, params)
            counts[table] = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
    conn.commit()
    return counts


def delete_author(conn, author_login: str) -> dict[str, int]:
    pr_ids = [
        int(r["id"] if hasattr(r, "keys") else r[0])
        for r in conn.execute("SELECT id FROM prs WHERE author_login = ?", (author_login,))
    ]
    counts: dict[str, int] = {"prs": len(pr_ids), "author": author_login}  # type: ignore[assignment]
    for table in ("predictions", "outcomes", "pr_files", "pr_commits", "pr_events", "ci_runs"):
        if pr_ids and _table_exists(conn, table):
            placeholders = ",".join("?" * len(pr_ids))
            cur = conn.execute(f"DELETE FROM {table} WHERE pr_id IN ({placeholders})", pr_ids)
            counts[table] = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        else:
            counts[table] = 0
    if pr_ids:
        placeholders = ",".join("?" * len(pr_ids))
        conn.execute(f"DELETE FROM prs WHERE id IN ({placeholders})", pr_ids)
    conn.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete stored PR Pulse data for a repo or author")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--repo", help="owner/name")
    g.add_argument("--author", help="GitHub login")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    cfg = load_settings()
    path = args.db or resolve_db_path(cfg)
    init_db(path)
    conn = connect(path)
    try:
        result = delete_repo(conn, args.repo) if args.repo else delete_author(conn, args.author)
    finally:
        conn.close()
    print(result)


if __name__ == "__main__":
    main()
