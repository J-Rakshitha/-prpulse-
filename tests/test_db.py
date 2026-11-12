"""Schema tests, including the point-in-time author-history view.

Most cases exercise SQLite PRAGMA / user_version migration paths. When
``PRPULSE_TEST_DATABASE_URL`` is set, this module is skipped so the rest of the
suite can run against Postgres.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.data.db import SCHEMA_VERSION, connect, init_db, resolve_db_path, to_utc_iso

pytestmark = pytest.mark.skipif(
    bool(os.environ.get("PRPULSE_TEST_DATABASE_URL", "").strip()),
    reason="SQLite PRAGMA / migration tests; skipped on Postgres suite runs",
)

NOW = "2026-01-01T00:00:00Z"
AS_OF = "2024-06-01T00:00:00Z"


def _init(tmp_path: Path) -> Path:
    path = tmp_path / "prpulse.db"
    init_db(path)
    return path


def _add_repo(conn: sqlite3.Connection, repo_id: int, full_name: str) -> None:
    owner, name = full_name.split("/")
    conn.execute(
        """
        INSERT INTO repos (id, owner, name, full_name, collected_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (repo_id, owner, name, full_name, NOW, NOW),
    )


def _add_pr(
    conn: sqlite3.Connection,
    pr_id: int,
    repo_id: int,
    number: int,
    author: str,
    created_at: str,
    *,
    state: str = "open",
    merged_at: str | None = None,
    closed_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO prs (
            id, repo_id, number, author_login, created_at, merged_at, closed_at,
            state, collected_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (pr_id, repo_id, number, author, created_at, merged_at, closed_at, state, NOW, NOW),
    )


def test_init_creates_the_planned_tables_and_is_idempotent(tmp_path: Path) -> None:
    path = _init(tmp_path)
    init_db(path)
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        found = {row["name"] for row in rows}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert {
        "repos",
        "prs",
        "pr_files",
        "pr_commits",
        "pr_events",
        "ci_runs",
        "predictions",
        "outcomes",
        "model_versions",
        "collection_checkpoints",
        "authors_history_view",
        "webhook_deliveries",
        "failed_jobs",
        "installations",
        "installation_repos",
        "pr_processing_locks",
    } <= found
    assert version == SCHEMA_VERSION


def test_foreign_key_rejects_a_pr_without_a_repo(tmp_path: Path) -> None:
    path = _init(tmp_path)
    with connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            _add_pr(conn, 1, 999, 1, "octocat", AS_OF)


def test_bad_timestamp_is_rejected(tmp_path: Path) -> None:
    path = _init(tmp_path)
    with connect(path) as conn:
        _add_repo(conn, 1, "psf/requests")
        with pytest.raises(sqlite3.IntegrityError):
            _add_pr(conn, 1, 1, 1, "octocat", "2024-06-01 00:00:00")


def test_merged_state_requires_merged_at(tmp_path: Path) -> None:
    path = _init(tmp_path)
    with connect(path) as conn:
        _add_repo(conn, 1, "psf/requests")
        with pytest.raises(sqlite3.IntegrityError):
            _add_pr(conn, 1, 1, 1, "octocat", AS_OF, state="merged", merged_at=None)


def test_unknown_event_type_is_rejected(tmp_path: Path) -> None:
    path = _init(tmp_path)
    with connect(path) as conn:
        _add_repo(conn, 1, "psf/requests")
        _add_pr(conn, 1, 1, 1, "octocat", AS_OF)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO pr_events (pr_id, event_type, occurred_at, source_key)
                VALUES (1, 'force_pushed', ?, 'x')
                """,
                (AS_OF,),
            )


def test_only_one_model_can_be_active(tmp_path: Path) -> None:
    path = _init(tmp_path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO model_versions (version, created_at, is_active) VALUES ('v1', ?, 1)",
            (NOW,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO model_versions (version, created_at, is_active) VALUES ('v2', ?, 1)",
                (NOW,),
            )


def test_author_history_ignores_the_future_and_other_repos(tmp_path: Path) -> None:
    """History for a PR opened at AS_OF.

    Counts:
    * a PR merged earlier by the same author in the same repo
    * a PR opened earlier whose merge is still in the future (opened, not merged)
    * a PR merged at the exact same timestamp (opened, not merged)
    Does not count:
    * a PR opened later
    * the same author in a different repo
    * a different author in the same repo
    """
    path = _init(tmp_path)
    with connect(path) as conn:
        _add_repo(conn, 1, "psf/requests")
        _add_repo(conn, 2, "pallets/flask")
        _add_pr(
            conn, 1, 1, 1, "octocat", "2024-01-01T00:00:00Z",
            state="merged", merged_at="2024-01-02T00:00:00Z", closed_at="2024-01-02T00:00:00Z",
        )
        _add_pr(
            conn, 2, 1, 2, "octocat", "2024-05-01T00:00:00Z",
            state="merged", merged_at="2024-08-01T00:00:00Z", closed_at="2024-08-01T00:00:00Z",
        )
        _add_pr(
            conn, 3, 1, 3, "octocat", "2024-03-01T00:00:00Z",
            state="merged", merged_at=AS_OF, closed_at=AS_OF,
        )
        _add_pr(conn, 4, 1, 4, "octocat", AS_OF, state="open")
        _add_pr(
            conn, 5, 1, 5, "octocat", "2024-07-01T00:00:00Z",
            state="merged", merged_at="2024-07-02T00:00:00Z", closed_at="2024-07-02T00:00:00Z",
        )
        _add_pr(
            conn, 6, 2, 1, "octocat", "2024-02-01T00:00:00Z",
            state="merged", merged_at="2024-02-02T00:00:00Z", closed_at="2024-02-02T00:00:00Z",
        )
        _add_pr(
            conn, 7, 1, 6, "other", "2024-02-01T00:00:00Z",
            state="merged", merged_at="2024-02-02T00:00:00Z", closed_at="2024-02-02T00:00:00Z",
        )
        row = conn.execute(
            "SELECT * FROM authors_history_view WHERE pr_id = 4"
        ).fetchone()
    assert row["as_of"] == AS_OF
    assert row["prior_opened_count"] == 3
    assert row["prior_merged_count"] == 1
    assert row["prior_resolved_count"] == 1


def test_rows_survive_a_second_init(tmp_path: Path) -> None:
    path = _init(tmp_path)
    with connect(path) as conn:
        _add_repo(conn, 1, "psf/requests")
    init_db(path)
    with connect(path) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"]
    assert count == 1


def test_schema_version_mismatch_is_refused(tmp_path: Path) -> None:
    path = _init(tmp_path)
    with connect(path) as conn:
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="schema version 99"):
        init_db(path)


def test_ci_label_available_column_exists(tmp_path: Path) -> None:
    path = _init(tmp_path)
    with connect(path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(prs)").fetchall()}
    assert "ci_label_available" in cols


def test_coverage_status_column_and_v3_migration(tmp_path: Path) -> None:
    path = tmp_path / "v3.db"
    # Build a v3-shaped DB (no coverage_status / phase13 tables), then migrate.
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 3")
    conn.executescript(
        """
        CREATE TABLE repos (
            id INTEGER PRIMARY KEY, owner TEXT, name TEXT, full_name TEXT UNIQUE,
            default_branch TEXT, has_github_actions INTEGER,
            collected_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE prs (
            id INTEGER PRIMARY KEY, repo_id INTEGER NOT NULL,
            number INTEGER NOT NULL, author_login TEXT NOT NULL,
            created_at TEXT NOT NULL, merged_at TEXT, closed_at TEXT,
            state TEXT NOT NULL, draft INTEGER NOT NULL DEFAULT 0,
            ci_label_available INTEGER NOT NULL DEFAULT 0,
            ingest_status TEXT NOT NULL DEFAULT 'partial',
            collected_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE model_versions (
            id INTEGER PRIMARY KEY, version TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY, pr_id INTEGER NOT NULL,
            model_version TEXT NOT NULL, made_at TEXT NOT NULL, stage TEXT NOT NULL,
            merge_hours_p10 REAL, merge_hours_p50 REAL, merge_hours_p90 REAL,
            prob_merged_within_7d REAL, prob_ci_fail REAL,
            shap_json TEXT, suggestions_json TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    init_db(path)
    with connect(path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        pred_cols = {row[1] for row in conn.execute("PRAGMA table_info(predictions)").fetchall()}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert version == SCHEMA_VERSION
    assert "coverage_status" in pred_cols
    assert {"webhook_deliveries", "failed_jobs", "installations", "pr_processing_locks"} <= tables


def test_to_utc_iso_rejects_naive_and_normalises_offsets() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_utc_iso(datetime(2024, 6, 1, 0, 0, 0))
    stamped = datetime(2024, 6, 1, 5, 30, tzinfo=timezone.utc).astimezone()
    # astimezone() may change the zone; the helper must still emit UTC Z.
    aware = datetime(2024, 6, 1, 5, 30, tzinfo=timezone.utc)
    assert to_utc_iso(aware) == "2024-06-01T05:30:00Z"
    assert to_utc_iso(stamped) == "2024-06-01T05:30:00Z"


def test_relative_db_path_is_under_the_given_root(tmp_path: Path) -> None:
    path = resolve_db_path({"database": {"sqlite_path": "data/prpulse.db"}}, root=tmp_path)
    assert path == tmp_path / "data" / "prpulse.db"
