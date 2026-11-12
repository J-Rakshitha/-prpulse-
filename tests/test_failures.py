from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src.data.db import connect
from src.service.api import create_app
from src.service.bot import GitHubRateLimitError, HttpGitHubClient
from src.service.queue import process_delivery_now
from src.service.replay import replay_failed
from src.service.webhook import sign
from src.service.worker_jobs import TransientJobError, backoff_seconds, configure as configure_jobs
from src.runtime_settings import RuntimeSettings

SECRET = "test-secret"


class FakeGitHub:
    def __init__(self):
        self.comments = {}
        self.calls = []
        self.files = [{"filename": "a.py", "additions": 1, "deletions": 0}]
        self.fail_mode = None

    def list_issue_comments(self, repo, number):
        return list(self.comments.values())

    def create_issue_comment(self, repo, number, body):
        self.calls.append("create")
        if self.fail_mode == "boom":
            raise RuntimeError("model boom")
        self.comments[1] = {"id": 1, "body": body}
        return self.comments[1]

    def update_issue_comment(self, repo, comment_id, body):
        self.calls.append("update")
        self.comments[comment_id]["body"] = body
        return self.comments[comment_id]

    def list_pr_files(self, repo, number):
        if self.fail_mode == "timeout":
            raise httpx.ReadTimeout("slow")
        if self.fail_mode == "5xx":
            req = httpx.Request("GET", "https://api.github.com")
            resp = httpx.Response(502, request=req)
            raise httpx.HTTPStatusError("bad gateway", request=req, response=resp)
        if self.fail_mode == "rate":
            raise GitHubRateLimitError("rate", retry_after=1.0, remaining=0)
        return self.files

    def get_file_text(self, repo, path, ref=None):
        return None


def _payload(number=3):
    return {
        "action": "opened",
        "repository": {"full_name": "acme/widgets"},
        "pull_request": {
            "number": number,
            "state": "open",
            "draft": False,
            "user": {"login": "alice", "type": "User"},
            "created_at": "2026-03-02T10:00:00Z",
            "commits": 1,
            "changed_files": 1,
            "additions": 1,
            "deletions": 0,
            "labels": [],
            "base": {"ref": "main"},
        },
    }


def _post(client, delivery, payload=None):
    body = json.dumps(payload or _payload()).encode()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sign(SECRET, body),
        },
    )


@pytest.fixture()
def app_env(tiny_models_dir, tmp_path):
    gh = FakeGitHub()
    db = tmp_path / "f.db"
    settings = RuntimeSettings(job_max_attempts=2, job_backoff_base_seconds=0.01, job_backoff_cap_seconds=0.05)
    app = create_app(
        models_dir=tiny_models_dir,
        db_path=db,
        webhook_secret=SECRET,
        github_client=gh,
        start_recovery=False,
        runtime_settings=settings,
    )
    with TestClient(app) as client:
        configure_jobs(settings=settings, db_target=db)
        yield client, gh, db


def test_model_error_posts_no_comment_and_retries_then_dead(app_env):
    client, gh, db = app_env

    class BoomService:
        def predict(self, *a, **k):
            raise RuntimeError("predict failed")

    client.app.state.bot.service = BoomService()
    assert _post(client, "fail-1").status_code == 200
    result = process_delivery_now("fail-1")
    assert result["status"] in ("retry", "dead")
    assert gh.comments == {}
    # Drain remaining retries
    for i in range(5):
        row = connect(db)
        try:
            status = row.execute(
                "SELECT status FROM webhook_deliveries WHERE delivery_id='fail-1'"
            ).fetchone()["status"]
        finally:
            row.close()
        if status in ("dead", "done", "failed"):
            break
        process_delivery_now("fail-1")
    conn = connect(db)
    try:
        status = conn.execute(
            "SELECT status FROM webhook_deliveries WHERE delivery_id='fail-1'"
        ).fetchone()["status"]
        assert status == "dead"
        assert conn.execute("SELECT COUNT(*) AS n FROM failed_jobs").fetchone()["n"] >= 1
    finally:
        conn.close()
    assert gh.comments == {}


def test_github_5xx_is_retryable(app_env):
    client, gh, db = app_env
    gh.fail_mode = "5xx"
    _post(client, "x5")
    result = process_delivery_now("x5")
    assert result["status"] == "retry"
    assert gh.comments == {}


def test_rate_limit_reschedules(app_env):
    client, gh, _ = app_env
    gh.fail_mode = "rate"
    _post(client, "rl")
    result = process_delivery_now("rl")
    assert result["status"] == "retry"
    assert "next_attempt_at" in result
    assert gh.comments == {}


def test_timeout_posts_no_comment(app_env):
    client, gh, _ = app_env
    gh.fail_mode = "timeout"
    _post(client, "to")
    result = process_delivery_now("to")
    assert result["status"] == "retry"
    assert gh.comments == {}


def test_replay_failed_resets_delivery(app_env):
    client, gh, db = app_env

    class BoomService:
        def predict(self, *a, **k):
            raise RuntimeError("predict failed")

    client.app.state.bot.service = BoomService()
    _post(client, "rp1")
    for _ in range(6):
        process_delivery_now("rp1")
        conn = connect(db)
        try:
            st = conn.execute(
                "SELECT status FROM webhook_deliveries WHERE delivery_id='rp1'"
            ).fetchone()["status"]
        finally:
            conn.close()
        if st == "dead":
            break
    # Fix the service and replay
    from src.service.core import PredictionService

    client.app.state.bot.service = client.app.state.service
    gh.fail_mode = None
    conn = connect(db)
    try:
        replayed = replay_failed(conn, delivery_id="rp1")
        assert replayed == ["rp1"]
    finally:
        conn.close()
    result = process_delivery_now("rp1")
    assert result["status"] == "created"
    assert len(gh.comments) == 1


def test_http_client_raises_on_rate_limit_headers():
    # Unit-level: _note_rate_headers path via a stubbed response.
    client = HttpGitHubClient("token", timeout=1.0)
    req = httpx.Request("GET", "https://api.github.com/rate")
    resp = httpx.Response(
        403,
        request=req,
        headers={"X-RateLimit-Remaining": "0", "Retry-After": "12"},
        text="API rate limit exceeded",
    )
    with pytest.raises(GitHubRateLimitError) as ei:
        client._note_rate_headers(resp)
    assert ei.value.retry_after == 12.0
