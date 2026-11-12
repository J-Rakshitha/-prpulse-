"""Collector helpers that do not call the network."""

from __future__ import annotations

from datetime import datetime, timezone

from src.data.collect import derive_pr_state, lookback_cutoff, target_repos
from src.data.github_client import normalize_github_timestamp
from src.settings import load_settings


def test_lookback_cutoff_is_timezone_aware() -> None:
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    cutoff = lookback_cutoff(2, now=now)
    assert cutoff.tzinfo is not None
    assert cutoff.year == 2024


def test_target_repos_respects_initial_limit() -> None:
    cfg = load_settings()
    assert target_repos(cfg) == ["pandas-dev/pandas", "scikit-learn/scikit-learn"]


def test_derive_pr_state() -> None:
    assert derive_pr_state({"merged_at": "2024-01-01T00:00:00Z", "state": "closed"}) == "merged"
    assert derive_pr_state({"merged_at": None, "state": "open"}) == "open"
    assert derive_pr_state({"merged_at": None, "state": "closed"}) == "closed"


def test_normalize_github_timestamp() -> None:
    assert normalize_github_timestamp("2024-06-01T12:00:00Z") == "2024-06-01T12:00:00Z"
    assert normalize_github_timestamp("2024-06-01T12:00:00.123Z") == "2024-06-01T12:00:00Z"
    assert normalize_github_timestamp(None) is None
