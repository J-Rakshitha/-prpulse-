"""Tests for config validation. No network and no secrets."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.settings import load_settings, require_env, validate_settings


def _valid() -> dict:
    return {
        "lookback_years": 2,
        "database": {"sqlite_path": "data/prpulse.db"},
        "logging": {"level": "INFO", "file": "logs/prpulse.log"},
        "repos": ["psf/requests", "pallets/flask"],
        "collection": {
            "initial_repo_limit": 2,
            "per_page": 100,
            "workers": 4,
            "progress_every": 100,
            "ci_backfill_sample_size": 200,
            "ci_backfill_sample_max_shas": 10,
            "ci_backfill_call_cap_threshold": 3,
        },
        "thresholds": {
            "merged_within_days": 7,
            "stage_b_offset_minutes": 30,
            "stage_c_offset_minutes": 120,
            "revert_window_days": 30,
        },
        "ci": {
            "failure_conclusions": ["failure"],
            "label_conclusions": ["success", "failure", "timed_out"],
        },
        "split": {
            "train_fraction": 0.70,
            "validation_fraction": 0.15,
            "test_fraction": 0.15,
        },
        "timing": {"friday_evening_start_hour_utc": 15},
        "bots": {
            "author_logins": ["dependabot[bot]"],
            "extra_logins": ["github-actions"],
        },
    }


def test_repository_config_loads() -> None:
    cfg = load_settings()
    assert cfg["lookback_years"] == 2
    assert cfg["thresholds"]["merged_within_days"] == 7
    assert cfg["split"]["train_fraction"] == 0.70


def test_split_must_sum_to_one() -> None:
    cfg = _valid()
    cfg["split"]["test_fraction"] = 0.10
    with pytest.raises(ValueError, match="sum to 1"):
        validate_settings(cfg)


def test_random_split_is_not_a_setting() -> None:
    """The config has no random-split switch. Time order is the only split."""
    cfg = load_settings()
    assert "random" not in cfg["split"]
    assert "shuffle" not in cfg["split"]


def test_secret_keys_are_rejected() -> None:
    cfg = _valid()
    cfg["github_token"] = "should-not-be-here"
    with pytest.raises(ValueError, match="must not contain secrets"):
        validate_settings(cfg)


def test_repo_name_must_be_owner_slash_name() -> None:
    cfg = _valid()
    cfg["repos"] = ["not-a-repo"]
    with pytest.raises(ValueError, match="owner/name"):
        validate_settings(cfg)


def test_stage_c_must_be_after_stage_b() -> None:
    cfg = _valid()
    cfg["thresholds"]["stage_c_offset_minutes"] = 30
    with pytest.raises(ValueError, match="stage B"):
        validate_settings(cfg)


def test_require_env_refuses_unknown_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "not-a-secret-we-should-read")
    with pytest.raises(ValueError, match="not a known secret"):
        require_env("PATH")


def test_require_env_reports_a_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        require_env("GITHUB_TOKEN")


def test_load_settings_ignores_a_missing_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 0 must boot before the user has created .env."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """
lookback_years: 1
database: {sqlite_path: data/x.db}
logging: {level: INFO, file: logs/x.log}
repos: ["octocat/hello"]
collection: {initial_repo_limit: 1, per_page: 50}
thresholds:
  merged_within_days: 7
  stage_b_offset_minutes: 30
  stage_c_offset_minutes: 120
  revert_window_days: 30
ci: {failure_conclusions: ["failure"]}
split: {train_fraction: 0.7, validation_fraction: 0.15, test_fraction: 0.15}
timing: {friday_evening_start_hour_utc: 15}
bots: {author_logins: ["dependabot[bot]"]}
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    loaded = load_settings(config)
    assert loaded["repos"] == ["octocat/hello"]
