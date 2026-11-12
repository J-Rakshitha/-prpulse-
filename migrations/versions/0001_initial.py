"""Initial PostgreSQL schema (version 4).

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src" / "data" / "schema_postgres.sql"

TABLES = (
    "pr_processing_locks",
    "installation_repos",
    "installations",
    "failed_jobs",
    "webhook_deliveries",
    "collection_checkpoints",
    "outcomes",
    "predictions",
    "model_versions",
    "ci_runs",
    "pr_events",
    "pr_commits",
    "pr_files",
    "prs",
    "repos",
    "schema_meta",
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(SCHEMA_SQL.read_text(encoding="utf-8"))
    bind.exec_driver_sql(
        "INSERT INTO schema_meta (key, value) VALUES ('schema_version', '4') "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS authors_history_view")
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
