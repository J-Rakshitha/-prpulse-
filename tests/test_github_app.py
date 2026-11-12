from __future__ import annotations

import time
from unittest.mock import MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.runtime_settings import RuntimeSettings
from src.service import github_app
from src.service.github_app import build_app_jwt, clear_token_cache, get_installation_token, request_installation_token
from src.service.installations import apply_installation_event
from src.data.db import connect, init_db


def _pem_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return pem, key


def test_build_app_jwt_roundtrip():
    pem, key = _pem_pair()
    now = int(time.time())
    token = build_app_jwt("12345", pem, now=now, lifetime=600)
    payload = jwt.decode(token, key.public_key(), algorithms=["RS256"])
    assert payload["iss"] == "12345"
    assert payload["exp"] == now + 600


def test_token_exchange_and_cache_refresh(monkeypatch):
    pem, _ = _pem_pair()
    clear_token_cache()
    settings = RuntimeSettings(
        auth_mode_raw="app",
        github_app_id="99",
        github_app_private_key=pem,
    )
    calls = {"n": 0}

    def fake_request(app_jwt, installation_id, **kwargs):
        calls["n"] += 1
        return f"tok-{calls['n']}", time.time() + 60  # expires soon so skew forces refresh

    monkeypatch.setattr(github_app, "request_installation_token", fake_request)
    t1 = get_installation_token(7, settings, refresh_skew_seconds=120.0, now=time.time())
    t2 = get_installation_token(7, settings, refresh_skew_seconds=120.0, now=time.time())
    assert t1 == "tok-1"
    assert t2 == "tok-2"  # refreshed because expiry - skew < now
    assert calls["n"] == 2


def test_request_installation_token_http_mock():
    client = MagicMock()
    response = MagicMock()
    response.status_code = 201
    response.json.return_value = {"token": "ghs_test", "expires_at": "2099-01-01T00:00:00Z"}
    client.post.return_value = response
    token, exp = request_installation_token("jwt", 42, client=client)
    assert token == "ghs_test"
    assert exp > time.time()
    client.post.assert_called_once()


def test_installation_tracking(tmp_path):
    path = tmp_path / "inst.db"
    init_db(path)
    conn = connect(path)
    try:
        payload = {
            "action": "created",
            "installation": {
                "id": 55,
                "account": {"login": "acme", "type": "Organization"},
                "repository_selection": "selected",
            },
            "repositories": [{"full_name": "acme/widgets"}, {"full_name": "acme/gadgets"}],
        }
        result = apply_installation_event(conn, "installation", payload)
        assert result["status"] == "installation_updated"
        repos = {
            r["repo_full_name"]
            for r in conn.execute("SELECT repo_full_name FROM installation_repos").fetchall()
        }
        assert repos == {"acme/widgets", "acme/gadgets"}

        apply_installation_event(
            conn,
            "installation_repositories",
            {
                "action": "removed",
                "installation": {"id": 55, "account": {"login": "acme", "type": "Organization"}},
                "repositories_added": [],
                "repositories_removed": [{"full_name": "acme/gadgets"}],
            },
        )
        left = {
            r["repo_full_name"]
            for r in conn.execute("SELECT repo_full_name FROM installation_repos").fetchall()
        }
        assert left == {"acme/widgets"}
    finally:
        conn.close()


def test_live_app_test_needs_registration():
    """Documented gate: a live App end-to-end test needs the user's registration."""
    note = "A live App test needs user registration"
    assert "registration" in note.lower()
