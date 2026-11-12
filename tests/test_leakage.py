"""The feature matrix must reject outcome columns and unapproved raw fields."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.features.leakage import (
    BLOCKED_FEATURE_COLUMNS,
    FORBIDDEN_FEATURE_COLUMNS,
    UNAPPROVED_FEATURE_COLUMNS,
    LeakageError,
    assert_feature_columns_allowed,
)
from src.settings import project_root


def test_allowed_columns_pass() -> None:
    assert_feature_columns_allowed(
        ["is_draft", "author_prior_merged_count", "created_hour_utc", "is_bot"]
    )


def test_forbidden_column_fails() -> None:
    with pytest.raises(LeakageError, match="merged_at"):
        assert_feature_columns_allowed(["is_draft", "merged_at", "additions"])


def test_every_blocked_name_fails_on_its_own() -> None:
    for name in sorted(BLOCKED_FEATURE_COLUMNS):
        with pytest.raises(LeakageError):
            assert_feature_columns_allowed([name])


def test_targets_are_forbidden_as_inputs() -> None:
    for target in ("merge_hours", "merged_within_7d", "ci_failed_first_run", "ci_duration_minutes"):
        assert target in FORBIDDEN_FEATURE_COLUMNS


def test_current_size_and_text_are_not_approved_yet() -> None:
    for name in ("additions", "deletions", "changed_files", "commits", "title", "body"):
        assert name in UNAPPROVED_FEATURE_COLUMNS


def test_feature_report_names_every_blocked_column() -> None:
    """reports/features.md is the human-readable copy of the leakage sets.

    Each blocked name must appear in backticks so a mention in passing prose
    cannot satisfy the test by accident.
    """
    text = (project_root() / "reports" / "features.md").read_text(encoding="utf-8")
    missing = [name for name in sorted(BLOCKED_FEATURE_COLUMNS) if f"`{name}`" not in text]
    assert missing == []


def test_report_file_lives_where_the_brief_requires() -> None:
    assert Path(project_root() / "reports" / "features.md").is_file()
