"""Copy an existing SQLite PR Pulse database into PostgreSQL.

    python scripts/migrate_sqlite_to_postgres.py --postgres postgresql://prpulse:***@localhost:5432/prpulse
    python scripts/migrate_sqlite_to_postgres.py --sqlite data/prpulse.db --postgres $DATABASE_URL --truncate
    python scripts/migrate_sqlite_to_postgres.py --dry-run          # counts only, no PostgreSQL needed

Tables are copied parent-first so foreign keys hold, ids are preserved, and the
identity sequences are advanced afterwards so new inserts do not collide.
Run `python -m src.data.db` (or `alembic upgrade head`) against PostgreSQL first.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.database import connect, redact_target
from src.data.db import init_db, resolve_db_path
from src.settings import load_settings

# Parent tables before children. pr_processing_locks is runtime-only and not copied.
COPY_ORDER = (
    "repos",
    "prs",
    "pr_files",
    "pr_commits",
    "pr_events",
    "ci_runs",
    "model_versions",
    "predictions",
    "outcomes",
    "collection_checkpoints",
    "installations",
    "installation_repos",
    "webhook_deliveries",
    "failed_jobs",
)
ID_TABLES = tuple(t for t in COPY_ORDER if t not in ("installations",))
BATCH = 1000


def sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def postgres_columns(conn, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ? "
        "ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


def shared_columns(src_cols: list[str], dst_cols: list[str]) -> list[str]:
    """Columns present on both sides, in destination order."""
    have = set(src_cols)
    return [c for c in dst_cols if c in have]


def copy_table(src: sqlite3.Connection, dst, table: str, batch: int = BATCH) -> int:
    cols = shared_columns(sqlite_columns(src, table), postgres_columns(dst, table))
    if not cols:
        return 0
    col_sql = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    insert_sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    cur = src.execute(f"SELECT {col_sql} FROM {table} ORDER BY 1")
    copied = 0
    while True:
        rows = cur.fetchmany(batch)
        if not rows:
            break
        dst.executemany(insert_sql, [tuple(r) for r in rows])
        copied += len(rows)
    dst.commit()
    return copied


def reset_sequences(dst) -> None:
    for table in ID_TABLES:
        dst.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
        )
    dst.commit()


def truncate_all(dst) -> None:
    names = ", ".join(reversed(COPY_ORDER))
    dst.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    dst.commit()


def migrate(sqlite_path: Path, postgres_url: str, truncate: bool = False) -> dict[str, tuple[int, int]]:
    """Return ``{table: (sqlite_rows, copied_rows)}``."""
    init_db(postgres_url)
    src = sqlite3.connect(f"file:{sqlite_path.as_posix()}?mode=ro", uri=True)
    dst = connect(postgres_url)
    result: dict[str, tuple[int, int]] = {}
    try:
        if truncate:
            truncate_all(dst)
        present = sqlite_tables(src)
        for table in COPY_ORDER:
            if table not in present:
                result[table] = (0, 0)
                continue
            total = int(src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            result[table] = (total, copy_table(src, dst, table))
        reset_sequences(dst)
    finally:
        src.close()
        dst.close()
    return result


def dry_run(sqlite_path: Path) -> dict[str, int]:
    src = sqlite3.connect(f"file:{sqlite_path.as_posix()}?mode=ro", uri=True)
    try:
        present = sqlite_tables(src)
        return {t: int(src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) if t in present else 0 for t in COPY_ORDER}
    finally:
        src.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", type=Path, default=None, help="Source SQLite file (default: config.yaml path)")
    parser.add_argument("--postgres", default=os.environ.get("DATABASE_URL", ""), help="Target URL (default: $DATABASE_URL)")
    parser.add_argument("--truncate", action="store_true", help="Empty the target tables first")
    parser.add_argument("--dry-run", action="store_true", help="Only count source rows")
    args = parser.parse_args()

    sqlite_path = args.sqlite or resolve_db_path(load_settings())
    if not sqlite_path.is_file():
        raise SystemExit(f"SQLite file not found: {sqlite_path}")
    if args.dry_run:
        for table, n in dry_run(sqlite_path).items():
            print(f"{table:24s} {n}")
        return
    if not args.postgres:
        raise SystemExit("Pass --postgres or set DATABASE_URL.")
    print(f"Migrating {sqlite_path} -> {redact_target(args.postgres)}")
    for table, (total, copied) in migrate(sqlite_path, args.postgres, args.truncate).items():
        print(f"{table:24s} source={total} copied={copied}")


if __name__ == "__main__":
    main()
