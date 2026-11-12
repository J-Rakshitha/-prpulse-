"""Typed runtime settings for the API and worker (environment / .env).

``src/settings.py`` still owns config.yaml (non-secret modelling settings).
This module owns deployment settings: database URL, Redis, auth mode, secrets,
retry and retention knobs. Secrets are ``SecretStr`` so a ``repr`` or a logged
settings object cannot leak them.

``RuntimeSettings.problems()`` lists misconfiguration; ``require_valid()`` raises.
The API ``main()`` and the worker call it at startup so a bad deploy fails fast,
not on the first webhook.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.settings import project_root

PLACEHOLDER_MARKERS = ("changeme", "change-me", "your-", "your_", "<", "xxxx", "replace-me", "example")


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(project_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    database_url: str = ""
    redis_url: str = ""
    queue_name: str = "prpulse"

    webhook_secret: SecretStr = SecretStr("")
    auth_mode_raw: str = Field(default="", validation_alias=AliasChoices("PRPULSE_AUTH_MODE", "auth_mode_raw"))
    github_token: SecretStr = SecretStr("")
    github_app_id: str = ""
    github_app_private_key: SecretStr = SecretStr("")
    github_app_private_key_path: str = ""

    unseen_repo_min_resolved: int = Field(default=50, ge=0)
    payload_retention_days: int = Field(default=90, ge=1)

    job_max_attempts: int = Field(default=5, ge=1)
    job_backoff_base_seconds: float = Field(default=5.0, gt=0)
    job_backoff_cap_seconds: float = Field(default=300.0, gt=0)
    pr_lock_ttl_seconds: int = Field(default=300, ge=5)
    recovery_interval_seconds: float = Field(default=30.0, ge=0)
    recovery_min_age_seconds: float = Field(default=120.0, ge=0)

    github_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    github_read_timeout_seconds: float = Field(default=15.0, gt=0)
    github_http_attempts: int = Field(default=3, ge=1)
    github_api_url: str = "https://api.github.com"

    privacy_url: str = ""
    webhook_silence_seconds: int = Field(default=86400, ge=60)
    webhook_silence_hours: float | None = Field(default=None, ge=0)
    worker_metrics_port: int = Field(default=9100, ge=0)
    admin_token: SecretStr = Field(default=SecretStr(""), validation_alias=AliasChoices("PRPULSE_ADMIN_TOKEN", "admin_token"))
    require_models: bool = Field(default=False, validation_alias=AliasChoices("PRPULSE_REQUIRE_MODELS", "require_models"))

    # ---- derived ---------------------------------------------------------

    @property
    def auth_mode(self) -> Literal["pat", "app"]:
        """Explicit PRPULSE_AUTH_MODE wins. Unset: App when an App id is configured, else PAT."""
        raw = self.auth_mode_raw.strip().lower()
        if raw in ("pat", "app"):
            return raw  # type: ignore[return-value]
        return "app" if self.github_app_id.strip() else "pat"

    def webhook_secret_value(self) -> str:
        return self.webhook_secret.get_secret_value().strip()

    def admin_token_value(self) -> str:
        return self.admin_token.get_secret_value().strip()

    def silence_seconds(self) -> int:
        if self.webhook_silence_hours is not None:
            return max(60, int(float(self.webhook_silence_hours) * 3600))
        return int(self.webhook_silence_seconds)

    def app_private_key(self) -> str:
        """PEM text from GITHUB_APP_PRIVATE_KEY, else the file at GITHUB_APP_PRIVATE_KEY_PATH."""
        inline = self.github_app_private_key.get_secret_value().strip()
        if inline:
            return inline.replace("\\n", "\n")
        if self.github_app_private_key_path.strip():
            path = Path(self.github_app_private_key_path.strip())
            if path.is_file():
                return path.read_text(encoding="utf-8")
        return ""

    def secret_values(self) -> list[str]:
        """Concrete secret strings, used by the log redaction filter."""
        values = [
            self.webhook_secret_value(),
            self.github_token.get_secret_value().strip(),
            self.github_app_private_key.get_secret_value().strip(),
        ]
        return [v for v in values if len(v) >= 8]

    # ---- validation ------------------------------------------------------

    def problems(self, role: str = "api") -> list[str]:
        issues: list[str] = []
        if self.auth_mode_raw.strip() and self.auth_mode_raw.strip().lower() not in ("pat", "app"):
            issues.append("PRPULSE_AUTH_MODE must be 'pat' or 'app'")
        secret = self.webhook_secret_value()
        if role == "api":
            if not secret:
                issues.append("WEBHOOK_SECRET is not set")
            elif _is_placeholder(secret):
                issues.append("WEBHOOK_SECRET looks like a placeholder")
        if self.auth_mode == "app":
            if not self.github_app_id.strip():
                issues.append("GITHUB_APP_ID is required when PRPULSE_AUTH_MODE=app")
            elif not self.github_app_id.strip().isdigit():
                issues.append("GITHUB_APP_ID must be numeric")
            key = self.app_private_key()
            if not key:
                issues.append("GITHUB_APP_PRIVATE_KEY or a readable GITHUB_APP_PRIVATE_KEY_PATH is required in app mode")
            elif "PRIVATE KEY" not in key:
                issues.append("GitHub App private key is not a PEM")
        else:
            token = self.github_token.get_secret_value().strip()
            if token and _is_placeholder(token):
                issues.append("GITHUB_TOKEN looks like a placeholder")
        if self.database_url and not self.database_url.lower().startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
            issues.append("DATABASE_URL must be a postgresql:// URL")
        if self.redis_url and not self.redis_url.lower().startswith(("redis://", "rediss://", "unix://")):
            issues.append("REDIS_URL must start with redis://, rediss:// or unix://")
        if role == "worker" and not self.redis_url:
            issues.append("REDIS_URL is required for the worker")
        return issues

    def require_valid(self, role: str = "api") -> None:
        issues = self.problems(role)
        if issues:
            raise RuntimeError("Invalid configuration: " + "; ".join(issues))


def get_settings(**overrides) -> RuntimeSettings:
    """Build settings from the environment now (not cached, so tests can monkeypatch env)."""
    return RuntimeSettings(**overrides)
