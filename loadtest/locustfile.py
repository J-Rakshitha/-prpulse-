"""Local load test: signed webhooks + /predict. Not a production capacity claim."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid

from locust import HttpUser, between, events, task

SECRET = os.environ.get("WEBHOOK_SECRET", "loadtest-secret")


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class PRPulseUser(HttpUser):
    wait_time = between(0.05, 0.2)

    @task(3)
    def webhook_pull_request(self) -> None:
        delivery = str(uuid.uuid4())
        payload = {
            "action": "opened",
            "repository": {"full_name": "loadtest/repo"},
            "pull_request": {
                "number": int(time.time() * 1000) % 1_000_000,
                "state": "open",
                "draft": False,
                "user": {"login": "loadtester", "type": "User"},
                "created_at": "2026-01-01T00:00:00Z",
                "commits": 1,
                "changed_files": 1,
                "additions": 10,
                "deletions": 1,
                "labels": [],
                "base": {"ref": "main"},
            },
        }
        body = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sign(body),
        }
        self.client.post("/webhook", data=body, headers=headers, name="/webhook")

    @task(1)
    def predict(self) -> None:
        body = {
            "stage": "A",
            "features": {
                "draft": 0,
                "commits_at_open": 1,
                "n_files_approx": 2,
                "log1p_n_files_approx": 1.1,
                "log1p_additions_approx": 2.0,
                "log1p_deletions_approx": 0.5,
                "hour_utc": 12,
                "weekday": 2,
                "is_weekend": 0,
                "month": 3,
                "is_friday_evening": 0,
                "repo_is_pandas": 0,
            },
            "include_explanation": False,
        }
        self.client.post("/predict", json=body, name="/predict")


@events.quitting.add_listener
def _(environment, **_kwargs):
    stats = environment.stats
    print("LOADTEST_DONE", "users_planned_check_reports")
    for name in ("/webhook", "/predict"):
        entry = stats.entries.get((name, "POST"))
        if entry:
            print(name, "n", entry.num_requests, "fail", entry.num_failures, "p50", entry.get_response_time_percentile(0.5), "p95", entry.get_response_time_percentile(0.95), "p99", entry.get_response_time_percentile(0.99))
