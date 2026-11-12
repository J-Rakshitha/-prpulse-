"""GitHub App JWT + installation access-token auth."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx
import jwt

from src.runtime_settings import RuntimeSettings, get_settings

logger = logging.getLogger("prpulse.github_app")

# installation_id -> (token, expires_at_epoch)
_token_cache: dict[int, tuple[str, float]] = {}
_cache_lock = threading.Lock()


class GitHubAppError(RuntimeError):
    pass


def build_app_jwt(app_id: str, private_key_pem: str, *, now: float | None = None, lifetime: int = 540) -> str:
    """Sign a short-lived JWT for App authentication (GitHub allows max 10 minutes)."""
    if not app_id or not private_key_pem:
        raise GitHubAppError("App id and private key are required to sign a JWT")
    issued = int(now if now is not None else time.time())
    payload = {
        "iat": issued - 60,  # clock skew allowance
        "exp": issued + lifetime,
        "iss": str(app_id),
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def request_installation_token(
    app_jwt: str,
    installation_id: int,
    *,
    api_url: str = "https://api.github.com",
    timeout: httpx.Timeout | float = 15.0,
    client: httpx.Client | None = None,
) -> tuple[str, float]:
    """Exchange an App JWT for an installation token. Returns ``(token, expires_at_epoch)``."""
    own_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.post(
            f"{api_url.rstrip('/')}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if response.status_code >= 400:
            raise GitHubAppError(f"installation token exchange failed: HTTP {response.status_code}")
        data = response.json()
        token = data["token"]
        # GitHub returns expires_at like 2026-01-01T00:00:00Z
        expires_at = data.get("expires_at")
        if expires_at:
            from datetime import datetime, timezone

            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
        else:
            exp = time.time() + 3600
        return token, float(exp)
    finally:
        if own_client:
            http.close()


def get_installation_token(
    installation_id: int,
    settings: RuntimeSettings | None = None,
    *,
    refresh_skew_seconds: float = 120.0,
    http_client: httpx.Client | None = None,
    now: float | None = None,
) -> str:
    """Cached installation token; refreshes ~2 minutes before expiry."""
    cfg = settings or get_settings()
    clock = now if now is not None else time.time()
    with _cache_lock:
        cached = _token_cache.get(installation_id)
        if cached and cached[1] - refresh_skew_seconds > clock:
            return cached[0]
    app_jwt = build_app_jwt(cfg.github_app_id, cfg.app_private_key())
    token, expires = request_installation_token(
        app_jwt,
        installation_id,
        api_url=cfg.github_api_url,
        timeout=httpx.Timeout(cfg.github_read_timeout_seconds, connect=cfg.github_connect_timeout_seconds),
        client=http_client,
    )
    with _cache_lock:
        _token_cache[installation_id] = (token, expires)
    return token


def clear_token_cache() -> None:
    with _cache_lock:
        _token_cache.clear()


def build_installation_client(installation_id: int, settings: RuntimeSettings | None = None):
    """Return an ``HttpGitHubClient`` authenticated as the installation."""
    from src.service.bot import HttpGitHubClient

    cfg = settings or get_settings()
    token = get_installation_token(installation_id, cfg)
    return HttpGitHubClient(
        token,
        base_url=cfg.github_api_url,
        timeout=httpx.Timeout(cfg.github_read_timeout_seconds, connect=cfg.github_connect_timeout_seconds),
    )


def resolve_github_client(
    installation_id: int | None = None,
    settings: RuntimeSettings | None = None,
    *,
    fallback: Any = None,
) -> Any:
    """Pick PAT or App client based on ``PRPULSE_AUTH_MODE``."""
    from src.service.bot import DryRunGitHubClient, HttpGitHubClient

    cfg = settings or get_settings()
    if cfg.auth_mode == "pat":
        token = cfg.github_token.get_secret_value().strip()
        if not token:
            return fallback if fallback is not None else DryRunGitHubClient()
        return HttpGitHubClient(
            token,
            base_url=cfg.github_api_url,
            timeout=httpx.Timeout(cfg.github_read_timeout_seconds, connect=cfg.github_connect_timeout_seconds),
        )
    if installation_id is None:
        return fallback if fallback is not None else DryRunGitHubClient()
    return build_installation_client(installation_id, cfg)
