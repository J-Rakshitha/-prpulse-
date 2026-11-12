from __future__ import annotations

import pytest

from src.runtime_settings import RuntimeSettings


def test_auth_mode_defaults_and_explicit():
    assert RuntimeSettings(auth_mode_raw="pat").auth_mode == "pat"
    assert RuntimeSettings(auth_mode_raw="app", github_app_id="1").auth_mode == "app"
    assert RuntimeSettings(github_app_id="42").auth_mode == "app"
    assert RuntimeSettings().auth_mode == "pat"


def test_problems_for_api_and_worker():
    bad = RuntimeSettings(auth_mode_raw="app", webhook_secret="")
    issues = bad.problems(role="api")
    assert any("WEBHOOK_SECRET" in i for i in issues)
    assert any("GITHUB_APP_ID" in i for i in issues)

    worker = RuntimeSettings(redis_url="")
    assert any("REDIS_URL" in i for i in worker.problems(role="worker"))


def test_placeholder_webhook_secret_detected():
    s = RuntimeSettings(webhook_secret="changeme-please")
    assert any("placeholder" in i for i in s.problems(role="api"))


def test_require_valid_raises():
    s = RuntimeSettings(auth_mode_raw="nope")
    with pytest.raises(RuntimeError, match="Invalid configuration"):
        s.require_valid(role="api")


def test_app_private_key_from_path(tmp_path):
    pem = tmp_path / "app.pem"
    pem.write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    s = RuntimeSettings(github_app_private_key_path=str(pem))
    assert "PRIVATE KEY" in s.app_private_key()
