from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.data.db import connect
from src.service.api import create_app
from src.service.bot import MARKER, parse_prpulse_config
from src.service.queue import process_delivery_now
from src.service.webhook import DeliveryCache, sign, verify_signature

SECRET = "test-secret"


class FakeGitHub:
    """In-memory stand-in for the GitHub REST client."""

    def __init__(self, files: list[dict[str, Any]] | None = None, repo_files: dict[str, str] | None = None):
        self.comments: dict[int, dict[str, Any]] = {}
        self._next = 1
        self.files = files if files is not None else [
            {"filename": "pandas/core/frame.py", "additions": 40, "deletions": 5},
            {"filename": "pandas/tests/test_frame.py", "additions": 20, "deletions": 0},
        ]
        self.repo_files = repo_files or {}
        self.calls: list[str] = []

    def list_issue_comments(self, repo, number):
        return list(self.comments.values())

    def create_issue_comment(self, repo, number, body):
        self.calls.append("create")
        cid = self._next
        self._next += 1
        self.comments[cid] = {"id": cid, "body": body}
        return self.comments[cid]

    def update_issue_comment(self, repo, comment_id, body):
        self.calls.append("update")
        self.comments[comment_id]["body"] = body
        return self.comments[comment_id]

    def list_pr_files(self, repo, number):
        return self.files

    def get_file_text(self, repo, path, ref=None):
        return self.repo_files.get(path)


def pr_payload(action="opened", number=7, labels=(), login="alice", user_type="User", **pr_extra) -> dict[str, Any]:
    pr = {
        "number": number,
        "state": "open",
        "draft": False,
        "user": {"login": login, "type": user_type},
        "created_at": "2026-03-02T10:00:00Z",
        "commits": 2,
        "changed_files": 2,
        "additions": 60,
        "deletions": 5,
        "labels": [{"name": n} for n in labels],
        "base": {"ref": "main"},
    }
    pr.update(pr_extra)
    return {"action": action, "repository": {"full_name": "acme/widgets"}, "pull_request": pr}


@pytest.fixture()
def env(tiny_models_dir, tmp_path):
    gh = FakeGitHub()
    db = tmp_path / "wh.db"
    app = create_app(
        models_dir=tiny_models_dir,
        db_path=db,
        webhook_secret=SECRET,
        github_client=gh,
        start_recovery=False,
    )
    with TestClient(app) as client:
        yield client, gh, db


def deliver(client, payload, event="pull_request", delivery="d-1", secret=SECRET, signature=None):
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": signature if signature is not None else sign(secret, body),
        "Content-Type": "application/json",
    }
    return client.post("/webhook", content=body, headers=headers)


def deliver_and_process(client, payload, event="pull_request", delivery="d-1", secret=SECRET):
    """POST /webhook then drain the persisted delivery (tests have no Redis worker)."""
    response = deliver(client, payload, event=event, delivery=delivery, secret=secret)
    assert response.status_code == 200, response.text
    body = response.json()
    if body.get("status") in ("accepted", "pending"):
        result = process_delivery_now(delivery)
        return response, result
    return response, body


# ---- signature -------------------------------------------------------------


def test_verify_signature_unit():
    body = b'{"a":1}'
    good = sign(SECRET, body)
    assert verify_signature(SECRET, body, good)
    assert not verify_signature(SECRET, body, good[:-1] + "0")
    assert not verify_signature(SECRET, body + b" ", good)
    assert not verify_signature(SECRET, body, None)
    assert not verify_signature(SECRET, body, good.replace("sha256=", "sha1="))
    assert not verify_signature("", body, sign("", body))


def test_valid_signature_creates_comment(env):
    client, gh, _ = env
    _, result = deliver_and_process(client, pr_payload())
    assert result["status"] == "created"
    assert len(gh.comments) == 1
    body = next(iter(gh.comments.values()))["body"]
    assert body.startswith(MARKER)
    assert "PR Pulse estimate" in body and "no-prpulse" in body


def test_invalid_signature_rejected(env):
    client, gh, _ = env
    assert deliver(client, pr_payload(), signature="sha256=" + "0" * 64).status_code == 401
    assert deliver(client, pr_payload(), delivery="d-2", secret="wrong").status_code == 401
    body = json.dumps(pr_payload()).encode()
    r = client.post("/webhook", content=body, headers={"X-GitHub-Event": "pull_request"})
    assert r.status_code == 401
    assert gh.comments == {}


def test_missing_server_secret_is_503(tiny_models_dir, tmp_path):
    app = create_app(
        models_dir=tiny_models_dir,
        db_path=tmp_path / "n.db",
        webhook_secret="",
        github_client=FakeGitHub(),
        start_recovery=False,
    )
    with TestClient(app) as client:
        assert deliver(client, pr_payload()).status_code == 503


def test_ping(env):
    client, _, _ = env
    r = deliver(client, {"zen": "hi"}, event="ping")
    assert r.json()["status"] == "pong"


# ---- idempotency -----------------------------------------------------------


def test_duplicate_delivery_is_ignored(env):
    client, gh, _ = env
    _, first = deliver_and_process(client, pr_payload(), delivery="same")
    second = deliver(client, pr_payload(), delivery="same")
    assert first["status"] == "created"
    assert second.json()["status"] == "duplicate"
    assert gh.calls == ["create"]
    assert len(gh.comments) == 1


def test_update_instead_of_create_on_synchronize(env):
    client, gh, _ = env
    _, opened = deliver_and_process(client, pr_payload("opened"), delivery="d-a")
    assert opened["status"] == "created"
    gh.files = gh.files + [{"filename": "pandas/core/big.py", "additions": 1500, "deletions": 0}]
    _, result = deliver_and_process(client, pr_payload("synchronize", commits=5), delivery="d-b")
    assert result["status"] == "updated"
    assert gh.calls == ["create", "update"]
    marked = [c for c in gh.comments.values() if MARKER in c["body"]]
    assert len(marked) == 1


def test_identical_event_leaves_comment_unchanged(env):
    client, gh, _ = env
    deliver_and_process(client, pr_payload("opened"), delivery="d-a")
    _, result = deliver_and_process(client, pr_payload("synchronize"), delivery="d-b")
    assert result["status"] == "unchanged"
    assert gh.calls == ["create"]


def test_other_comments_are_untouched(env):
    client, gh, _ = env
    gh.comments[99] = {"id": 99, "body": "LGTM from a human"}
    gh._next = 100
    deliver_and_process(client, pr_payload("opened"), delivery="d-a")
    assert gh.comments[99]["body"] == "LGTM from a human"
    assert len(gh.comments) == 2


# ---- skips -----------------------------------------------------------------


def test_opt_out_label(env):
    client, gh, _ = env
    _, result = deliver_and_process(client, pr_payload(labels=["No-PRPulse"]))
    assert result["status"] == "skipped" and "label" in result["reason"]
    assert gh.comments == {}


def test_opt_out_config_file(tiny_models_dir, tmp_path):
    gh = FakeGitHub(repo_files={".prpulse.yml": "enabled: false\n"})
    app = create_app(
        models_dir=tiny_models_dir,
        db_path=tmp_path / "c.db",
        webhook_secret=SECRET,
        github_client=gh,
        start_recovery=False,
    )
    with TestClient(app) as client:
        _, result = deliver_and_process(client, pr_payload())
        assert result["status"] == "skipped" and "config" in result["reason"]
        assert gh.comments == {}


def test_config_enabled_true_and_garbage_do_not_opt_out():
    assert parse_prpulse_config("enabled: true").get("enabled") is True
    assert parse_prpulse_config(": : not yaml [") == {}
    assert parse_prpulse_config(None) == {}


def test_bot_authors_and_closed_prs_are_skipped(env):
    client, gh, _ = env
    _, b1 = deliver_and_process(client, pr_payload(login="dependabot[bot]", user_type="Bot"), delivery="b1")
    assert b1["status"] == "skipped"
    _, b2 = deliver_and_process(client, pr_payload(state="closed"), delivery="b2")
    assert b2["status"] == "skipped"
    assert gh.comments == {}


def test_unhandled_events_and_actions_ignored(env):
    client, gh, _ = env
    _, i1 = deliver_and_process(client, pr_payload("labeled"), delivery="i1")
    assert i1["status"] == "ignored"
    _, i2 = deliver_and_process(client, {}, event="issues", delivery="i2")
    assert i2["status"] == "ignored"
    assert gh.comments == {}


def test_bad_json_is_400_and_retry_allowed(env):
    client, _, _ = env
    body = b"not json"
    headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "j1", "X-Hub-Signature-256": sign(SECRET, body)}
    assert client.post("/webhook", content=body, headers=headers).status_code == 400
    # delivery was never persisted, so a corrected retry is processed
    _, result = deliver_and_process(client, pr_payload(), delivery="j1")
    assert result["status"] == "created"


def test_github_api_error_records_failed_job(tiny_models_dir, tmp_path):
    import httpx

    class ForbiddenGitHub(FakeGitHub):
        def create_issue_comment(self, repo, number, body):
            request = httpx.Request("POST", f"https://api.github.com/repos/{repo}/issues/{number}/comments")
            response = httpx.Response(403, request=request, json={"message": "forbidden"})
            raise httpx.HTTPStatusError("forbidden", request=request, response=response)

    gh = ForbiddenGitHub()
    db = tmp_path / "err.db"
    app = create_app(
        models_dir=tiny_models_dir,
        db_path=db,
        webhook_secret=SECRET,
        github_client=gh,
        start_recovery=False,
    )
    with TestClient(app) as client:
        _, first = deliver_and_process(client, pr_payload(), delivery="err-1")
        assert first["status"] == "error"
        assert first["reason"] == "github_api_403"
        assert gh.comments == {}
        # Same delivery id is durable-duplicate (replay is the retry path).
        second = deliver(client, pr_payload(), delivery="err-1")
        assert second.json()["status"] == "duplicate"
        conn = connect(db)
        try:
            failed = conn.execute("SELECT COUNT(*) AS n FROM failed_jobs").fetchone()["n"]
            assert failed == 1
        finally:
            conn.close()


def test_delivery_cache_release():
    cache = DeliveryCache(max_size=2)
    assert cache.claim("a") and not cache.claim("a")
    cache.release("a")
    assert cache.claim("a")
    cache.claim("b")
    cache.claim("c")  # evicts oldest
    assert cache.claim("a")


# ---- outcomes via webhook --------------------------------------------------


def test_merge_and_ci_events_record_outcome(env):
    client, _, db = env
    deliver_and_process(client, pr_payload("opened"), delivery="o1")
    run = {
        "action": "completed",
        "repository": {"full_name": "acme/widgets"},
        "workflow_run": {"conclusion": "failure", "pull_requests": [{"number": 7}]},
    }
    _, wr = deliver_and_process(client, run, event="workflow_run", delivery="o2")
    assert wr["status"] == "outcome_recorded"
    closed = pr_payload("closed", merged=True, state="closed", merged_at="2026-03-02T22:00:00Z", closed_at="2026-03-02T22:00:00Z")
    _, cr = deliver_and_process(client, closed, delivery="o3")
    assert cr["status"] == "outcome_recorded"
    conn = connect(db)
    try:
        o = conn.execute("SELECT * FROM outcomes").fetchone()
        assert o["merged"] == 1
        assert o["actual_merge_hours"] == pytest.approx(12.0)
        assert o["ci_failed_first_run"] == 1
        assert o["resolved_at"] == "2026-03-02T22:00:00Z"
    finally:
        conn.close()


def test_closed_unknown_pr_is_ignored(env):
    client, _, db = env
    _, result = deliver_and_process(
        client, pr_payload("closed", number=999, merged=False, state="closed"), delivery="u1"
    )
    assert result["status"] == "ignored"


# ---- Phase 13.1: fast ack --------------------------------------------------


def test_slow_predictor_does_not_block_webhook_ack(tiny_models_dir, tmp_path):
    """Webhook returns before model work; slow predict only runs in process_delivery_now."""

    class SlowService:
        def predict(self, *args, **kwargs):
            time.sleep(0.4)
            return {
                "merge_hours_p10": 1.0,
                "merge_hours_p50": 2.0,
                "merge_hours_p90": 3.0,
                "prob_merged_within_7d": 0.5,
                "prob_ci_fail": None,
                "model_version": "test",
                "explanation": [],
                "suggestions": [],
            }

    gh = FakeGitHub()
    db = tmp_path / "slow.db"
    app = create_app(
        models_dir=tiny_models_dir,
        db_path=db,
        webhook_secret=SECRET,
        github_client=gh,
        start_recovery=False,
    )
    with TestClient(app) as client:
        app.state.bot.service = SlowService()
        started = time.perf_counter()
        r = deliver(client, pr_payload(), delivery="slow-1")
        ack = time.perf_counter() - started
        assert r.status_code == 200
        assert r.json()["status"] in ("accepted", "pending")
        assert ack < 0.35, f"webhook ack too slow: {ack:.3f}s"
        # Drain after the ack measurement so the slow predict does not count.
        result = process_delivery_now("slow-1")
        assert result["status"] == "created"
        assert len(gh.comments) == 1


def test_redis_down_still_acks_pending(env):
    client, gh, db = env
    r = deliver(client, pr_payload(), delivery="pending-1")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["delivery"] == "pending-1"
    conn = connect(db)
    try:
        row = conn.execute(
            "SELECT status FROM webhook_deliveries WHERE delivery_id = ?",
            ("pending-1",),
        ).fetchone()
        assert row["status"] == "pending"
    finally:
        conn.close()
    assert gh.comments == {}
    result = process_delivery_now("pending-1")
    assert result["status"] == "created"
    assert len(gh.comments) == 1
