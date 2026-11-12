"""Retention job + opt-out must not leave payloads / predictions unexpectedly."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from src.data.db import connect, init_db
from src.service.api import create_app
from src.service.retention import clear_old_payloads
from src.service.store import now_iso, utc_now
from tests.test_webhook import SECRET, FakeGitHub, deliver_and_process, pr_payload, sign


def test_retention_nulls_old_payload_json(tmp_path: Path):
    db = tmp_path / "ret.db"
    init_db(db)
    conn = connect(db)
    old = (utc_now() - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = now_iso()
    conn.execute(
        """
        INSERT INTO webhook_deliveries (
            delivery_id, event, action, repo_full_name, pr_number, installation_id,
            event_ts, status, attempts, payload_json, result_json, last_error,
            received_at, enqueued_at, next_attempt_at, updated_at, processed_at
        ) VALUES
        ('old1', 'pull_request', 'opened', 'o/r', 1, NULL, NULL, 'done', 1, '{"x":1}', NULL, NULL, ?, NULL, NULL, ?, NULL),
        ('new1', 'pull_request', 'opened', 'o/r', 2, NULL, NULL, 'done', 1, '{"x":2}', NULL, NULL, ?, NULL, NULL, ?, NULL)
        """,
        (old, old, recent, recent),
    )
    conn.commit()
    result = clear_old_payloads(conn, older_than_days=90)
    assert result["deliveries_cleared"] == 1
    rows = {r["delivery_id"]: r["payload_json"] for r in conn.execute("SELECT delivery_id, payload_json FROM webhook_deliveries")}
    assert rows["old1"] is None
    assert rows["new1"] is not None
    conn.close()


def test_opt_out_no_comment_and_no_prediction(tiny_models_dir, tmp_path):
    gh = FakeGitHub()
    db = tmp_path / "opt.db"
    app = create_app(
        models_dir=tiny_models_dir,
        db_path=db,
        webhook_secret=SECRET,
        github_client=gh,
        start_recovery=False,
    )
    with TestClient(app) as client:
        _, result = deliver_and_process(client, pr_payload(labels=["no-prpulse"]))
        assert result["status"] == "skipped"
        assert gh.comments == {}
        conn = connect(db)
        n = conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]
        conn.close()
        assert int(n) == 0


def test_render_comment_footer_has_privacy_and_opt_out():
    from src.service.bot import render_comment
    import os

    os.environ["PRIVACY_URL"] = "https://example.com/privacy"
    body = render_comment(
        {
            "merge_hours_p10": 1.0,
            "merge_hours_p50": 2.0,
            "merge_hours_p90": 4.0,
            "model_version": "test",
        },
        "A",
    )
    assert "Automated estimate" in body
    assert "Privacy: https://example.com/privacy" in body
    assert "no-prpulse" in body
    assert ".prpulse.yml" in body


def test_delete_repo_data_clears_v4_tables(tmp_path: Path):
    from scripts.delete_repo_data import delete_repo
    from src.service.store import ensure_model_version

    db = tmp_path / "del.db"
    init_db(db)
    conn = connect(db)
    ensure_model_version(conn, "v1", artifact_path="unused", activate=True)
    conn.execute(
        "INSERT INTO repos (owner, name, full_name, default_branch, collected_at, updated_at) VALUES ('acme', 'widget', 'acme/widget', 'main', ?, ?)",
        (now_iso(), now_iso()),
    )
    repo_id = conn.execute("SELECT id FROM repos").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO prs (repo_id, number, author_login, created_at, state, draft, ingest_status, collected_at, updated_at)
        VALUES (?, 7, 'alice', ?, 'open', 0, 'partial', ?, ?)
        """,
        (repo_id, now_iso(), now_iso(), now_iso()),
    )
    pr_id = conn.execute("SELECT id FROM prs").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO predictions (pr_id, model_version, made_at, stage, merge_hours_p50)
        VALUES (?, 'v1', ?, 'A', 1.0)
        """,
        (pr_id, now_iso()),
    )
    conn.execute(
        """
        INSERT INTO webhook_deliveries (
            delivery_id, event, status, attempts, payload_json, received_at, updated_at, repo_full_name, pr_number
        ) VALUES ('d1', 'pull_request', 'done', 1, '{}', ?, ?, 'acme/widget', 7)
        """,
        (now_iso(), now_iso()),
    )
    conn.execute(
        """
        INSERT INTO failed_jobs (delivery_id, repo_full_name, pr_number, error_message, attempts, failed_at, status)
        VALUES ('d1', 'acme/widget', 7, 'x', 1, ?, 'failed')
        """,
        (now_iso(),),
    )
    conn.execute(
        """
        INSERT INTO pr_processing_locks (repo_full_name, pr_number, owner, acquired_at, expires_at)
        VALUES ('acme/widget', 7, 't', ?, ?)
        """,
        (now_iso(), now_iso()),
    )
    conn.commit()
    counts = delete_repo(conn, "acme/widget")
    assert counts["repos"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM webhook_deliveries").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM failed_jobs").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM pr_processing_locks").fetchone()["n"] == 0
    conn.close()
