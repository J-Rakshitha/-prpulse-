from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from src.data.db import connect
from src.service.api import create_app
from src.service.locks import acquire_pr_lock, release_pr_lock
from src.service.queue import process_delivery_now
from src.service.webhook import sign

SECRET = "test-secret"


class FakeGitHub:
    def __init__(self):
        self.comments = {}
        self._next = 1
        self.calls = []
        self.files = [{"filename": "a.py", "additions": 10, "deletions": 0}]
        self.lock = threading.Lock()

    def list_issue_comments(self, repo, number):
        with self.lock:
            return list(self.comments.values())

    def create_issue_comment(self, repo, number, body):
        with self.lock:
            self.calls.append("create")
            cid = self._next
            self._next += 1
            self.comments[cid] = {"id": cid, "body": body}
            return self.comments[cid]

    def update_issue_comment(self, repo, comment_id, body):
        with self.lock:
            self.calls.append("update")
            self.comments[comment_id]["body"] = body
            return self.comments[comment_id]

    def list_pr_files(self, repo, number):
        return self.files

    def get_file_text(self, repo, path, ref=None):
        return None


def pr_payload(action="opened", number=7, **extra):
    pr = {
        "number": number,
        "state": "open",
        "draft": False,
        "user": {"login": "alice", "type": "User"},
        "created_at": "2026-03-02T10:00:00Z",
        "updated_at": "2026-03-02T10:00:00Z",
        "commits": 1,
        "changed_files": 1,
        "additions": 10,
        "deletions": 0,
        "labels": [],
        "base": {"ref": "main"},
    }
    pr.update(extra)
    return {"action": action, "repository": {"full_name": "acme/widgets"}, "pull_request": pr}


def post(client, payload, delivery, event="pull_request"):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sign(SECRET, body),
        },
    )


@pytest.fixture()
def env(tiny_models_dir, tmp_path):
    gh = FakeGitHub()
    db = tmp_path / "id.db"
    app = create_app(
        models_dir=tiny_models_dir,
        db_path=db,
        webhook_secret=SECRET,
        github_client=gh,
        start_recovery=False,
    )
    with TestClient(app) as client:
        yield client, gh, db


def test_same_payload_twice_one_comment(env):
    client, gh, _ = env
    assert post(client, pr_payload(), "dup-a").json()["status"] == "pending"
    assert process_delivery_now("dup-a")["status"] == "created"
    assert post(client, pr_payload(), "dup-a").json()["status"] == "duplicate"
    assert len(gh.comments) == 1
    assert gh.calls == ["create"]


def test_concurrent_events_same_pr_single_comment(env):
    client, gh, _ = env
    post(client, pr_payload("opened"), "c1")
    post(client, pr_payload("synchronize", updated_at="2026-03-02T11:00:00Z"), "c2")

    errors = []

    def run(did):
        try:
            process_delivery_now(did)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=run, args=("c1",))
    t2 = threading.Thread(target=run, args=("c2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors
    marked = [c for c in gh.comments.values() if "prpulse:marker" in c["body"]]
    assert len(marked) == 1


def test_out_of_order_closed_before_opened(env):
    client, gh, db = env
    closed = pr_payload(
        "closed",
        state="closed",
        merged=True,
        merged_at="2026-03-02T12:00:00Z",
        closed_at="2026-03-02T12:00:00Z",
        updated_at="2026-03-02T12:00:00Z",
    )
    # Seed a prediction so closed can record an outcome, then process closed first.
    post(client, pr_payload("opened", updated_at="2026-03-02T10:00:00Z"), "oo-open")
    process_delivery_now("oo-open")
    assert len(gh.comments) == 1

    post(client, closed, "oo-close")
    assert process_delivery_now("oo-close")["status"] == "outcome_recorded"

    # A late synchronize after closed should be skipped by out-of-order guard.
    post(client, pr_payload("synchronize", updated_at="2026-03-02T11:00:00Z"), "oo-late")
    result = process_delivery_now("oo-late")
    assert result["status"] == "skipped"
    assert "stale" in result.get("reason", "")
    assert len(gh.comments) == 1


def test_pr_lock_exclusive(tmp_path):
    from src.data.db import init_db

    path = tmp_path / "lock.db"
    init_db(path)
    conn = connect(path)
    try:
        owner = acquire_pr_lock(conn, "acme/widgets", 1, ttl_seconds=60)
        assert owner
        assert acquire_pr_lock(conn, "acme/widgets", 1, ttl_seconds=60) is None
        release_pr_lock(conn, "acme/widgets", 1, owner)
        assert acquire_pr_lock(conn, "acme/widgets", 1, ttl_seconds=60)
    finally:
        conn.close()
