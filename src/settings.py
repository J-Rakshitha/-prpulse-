"""Load config.yaml and .env.

config.yaml holds non-secret settings (repos, thresholds, paths).
.env holds secrets. They are loaded into the process environment and are
never written into the settings dict, so a log of the settings cannot leak them.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Names that must exist in the environment before the phase that uses them.
# Phase 0 does not require any of them, because it never calls GitHub.
SECRET_ENV_VARS = (
    "GITHUB_TOKEN",
    "WEBHOOK_SECRET",
    "GITHUB_APP_ID",
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "GITHUB_APP_INSTALLATION_ID",
)

_SECRET_KEY_FRAGMENTS = ("token", "secret", "password", "private_key")


def project_root() -> Path:
    """Return the repository root (the directory that contains config.yaml)."""
    return Path(__file__).resolve().parents[1]


def load_settings(config_path: Path | None = None) -> dict[str, Any]:
    """Read config.yaml, load .env if it exists, and validate the config.

    A missing .env is allowed in Phase 0. The collector (Phase 1) must call
    require_env("GITHUB_TOKEN") before the first API request.
    """
    root = project_root()
    load_dotenv(root / ".env")
    path = config_path if config_path is not None else root / "config.yaml"
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must be a mapping at the top level.")
    return validate_settings(loaded)


def require_env(name: str) -> str:
    """Return a required secret from the environment, or raise.

    The value is not logged. Callers should pass it straight into the client.
    """
    if name not in SECRET_ENV_VARS:
        raise ValueError(f"{name} is not a known secret. Refusing to read an arbitrary variable.")
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is missing. Copy .env.example to .env and set it there. "
            "Do not hardcode it."
        )
    return value


def validate_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Reject a config that would hide a secret or break the split rules."""
    _reject_secret_keys(cfg, "")

    lookback = cfg.get("lookback_years")
    if not isinstance(lookback, int) or isinstance(lookback, bool) or lookback < 1:
        raise ValueError("lookback_years must be an integer >= 1.")

    database = _mapping(cfg, "database")
    sqlite_path = database.get("sqlite_path")
    if not isinstance(sqlite_path, str) or not sqlite_path.strip():
        raise ValueError("database.sqlite_path must be a non-empty string.")

    logging_cfg = _mapping(cfg, "logging")
    level = logging_cfg.get("level")
    if not isinstance(level, str) or level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError("logging.level must be DEBUG, INFO, WARNING, or ERROR.")
    log_file = logging_cfg.get("file")
    if not isinstance(log_file, str) or not log_file.strip():
        raise ValueError("logging.file must be a non-empty string.")

    repos = cfg.get("repos")
    if not isinstance(repos, list) or not repos:
        raise ValueError("repos must be a non-empty list of 'owner/name' strings.")
    for repo in repos:
        if not isinstance(repo, str) or repo.count("/") != 1:
            raise ValueError(f"Invalid repo {repo!r}. Expected 'owner/name'.")
        owner, name = repo.split("/")
        if not owner or not name:
            raise ValueError(f"Invalid repo {repo!r}. Expected 'owner/name'.")

    collection = _mapping(cfg, "collection")
    limit = collection.get("initial_repo_limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > len(repos):
        raise ValueError("collection.initial_repo_limit must be an integer from 1 to len(repos).")
    per_page = collection.get("per_page")
    if not isinstance(per_page, int) or isinstance(per_page, bool) or not 1 <= per_page <= 100:
        raise ValueError("collection.per_page must be an integer from 1 to 100.")
    workers = collection.get("workers", 4)
    if not isinstance(workers, int) or isinstance(workers, bool) or not 1 <= workers <= 16:
        raise ValueError("collection.workers must be an integer from 1 to 16.")
    collection["workers"] = workers
    for key, default in (
        ("progress_every", 100),
        ("ci_backfill_sample_size", 200),
        ("ci_backfill_sample_max_shas", 10),
        ("ci_backfill_call_cap_threshold", 3),
        ("ci_backfill_stop_after_misses", 200),
    ):
        value = collection.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"collection.{key} must be an integer >= 1.")
        collection[key] = value

    thresholds = _mapping(cfg, "thresholds")
    for key in ("merged_within_days", "revert_window_days"):
        value = thresholds.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"thresholds.{key} must be an integer >= 1.")
    stage_b = thresholds.get("stage_b_offset_minutes")
    stage_c = thresholds.get("stage_c_offset_minutes")
    if not isinstance(stage_b, int) or isinstance(stage_b, bool) or stage_b < 1:
        raise ValueError("thresholds.stage_b_offset_minutes must be an integer >= 1.")
    if not isinstance(stage_c, int) or isinstance(stage_c, bool) or stage_c <= stage_b:
        raise ValueError("thresholds.stage_c_offset_minutes must be an integer greater than stage B.")

    ci = _mapping(cfg, "ci")
    conclusions = ci.get("failure_conclusions")
    if not isinstance(conclusions, list) or not conclusions:
        raise ValueError("ci.failure_conclusions must be a non-empty list.")
    if not all(isinstance(item, str) and item for item in conclusions):
        raise ValueError("ci.failure_conclusions must be a list of non-empty strings.")
    label_conclusions = ci.get("label_conclusions", ["success", "failure", "timed_out"])
    if not isinstance(label_conclusions, list) or not label_conclusions:
        raise ValueError("ci.label_conclusions must be a non-empty list.")
    if not all(isinstance(item, str) and item for item in label_conclusions):
        raise ValueError("ci.label_conclusions must be a list of non-empty strings.")
    ci["label_conclusions"] = label_conclusions

    split = _mapping(cfg, "split")
    fractions = []
    for key in ("train_fraction", "validation_fraction", "test_fraction"):
        value = split.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"split.{key} must be a number.")
        if value <= 0:
            raise ValueError(f"split.{key} must be > 0.")
        fractions.append(float(value))
    if not math.isclose(sum(fractions), 1.0, abs_tol=1e-9):
        raise ValueError("split fractions must sum to 1.")

    timing = _mapping(cfg, "timing")
    hour = timing.get("friday_evening_start_hour_utc")
    if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
        raise ValueError("timing.friday_evening_start_hour_utc must be an integer from 0 to 23.")

    bots = _mapping(cfg, "bots")
    logins = bots.get("author_logins")
    if not isinstance(logins, list) or not logins:
        raise ValueError("bots.author_logins must be a non-empty list.")
    if not all(isinstance(item, str) and item for item in logins):
        raise ValueError("bots.author_logins must be a list of non-empty strings.")
    extra = bots.get("extra_logins", [])
    if extra is None:
        extra = []
    if not isinstance(extra, list):
        raise ValueError("bots.extra_logins must be a list when present.")
    if not all(isinstance(item, str) and item for item in extra):
        raise ValueError("bots.extra_logins must be a list of non-empty strings.")
    bots["extra_logins"] = extra

    return cfg


def _mapping(cfg: dict[str, Any], key: str) -> dict[str, Any]:
    value = cfg.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping.")
    return value


def _reject_secret_keys(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS):
                where = f"{path}.{key}" if path else str(key)
                raise ValueError(
                    f"config.yaml must not contain secrets (found {where}). Put them in .env."
                )
            _reject_secret_keys(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_keys(child, f"{path}[{index}]")
