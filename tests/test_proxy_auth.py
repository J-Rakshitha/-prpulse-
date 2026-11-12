"""Proxy / dashboard auth expectations (Caddyfile + optional live check)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CADDYFILE = ROOT / "deploy" / "Caddyfile"


def test_caddyfile_denies_metrics_and_auths_dashboard():
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "handle /dashboard*" in text
    assert "basic_auth" in text
    assert "{$DASHBOARD_USER}" in text
    assert "{$DASHBOARD_PASSWORD_HASH}" in text
    # /metrics must not be reverse-proxied on the public port.
    assert "handle /metrics" not in text
    assert 'respond "Not found" 404' in text
    # Public API surface still present.
    assert "handle /webhook*" in text
    assert "handle /health" in text


def test_check_proxy_auth_script_exists():
    script = ROOT / "scripts" / "check_proxy_auth.py"
    assert script.is_file()
    src = script.read_text(encoding="utf-8")
    assert "/metrics" in src and "/dashboard" in src
