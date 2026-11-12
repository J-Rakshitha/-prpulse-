"""Columns that must never enter a model feature matrix.

Two sets:

* FORBIDDEN_FEATURE_COLUMNS are outcomes or "after the fact" totals. They stay
  forbidden for the life of the project.
* UNAPPROVED_FEATURE_COLUMNS exist in the database but are not known to be
  point-in-time safe yet. Phase 3 may move a name out of this set only after a
  test shows the value was knowable at the stage cutoff, and only after
  reports/features.md says when it becomes known.

The feature builder must call assert_feature_columns_allowed on the matrix
columns before fitting or predicting.
"""

from __future__ import annotations

from collections.abc import Iterable

FORBIDDEN_FEATURE_COLUMNS: frozenset[str] = frozenset(
    {
        "merged_at",
        "closed_at",
        "merged_by",
        "merge_commit_sha",
        "merge_commit_message",
        "hours_to_merge",
        "merge_hours",
        "merged_within_7d",
        "ci_failed_first_run",
        "ci_duration_minutes",
        "comment_count_final",
        "review_count_final",
        "review_rounds_final",
        "commits_final",
        "changed_files_final",
        "additions_final",
        "deletions_final",
        "time_in_review_hours",
        "labels_final",
        # Final state (open/closed/merged) is an outcome. The at-open draft
        # flag, when we approve it, must use a different column name.
        "state",
    }
)

# Stored by the collector from the PR object as it looks *now*.
# GitHub updates these when the author pushes or edits. Using the stored
# value as an at-open feature would leak later commits and edits.
UNAPPROVED_FEATURE_COLUMNS: frozenset[str] = frozenset(
    {
        "additions",
        "deletions",
        "changed_files",
        "commits",
        "title",
        "body",
        "labels_at_collection",
    }
)

BLOCKED_FEATURE_COLUMNS: frozenset[str] = FORBIDDEN_FEATURE_COLUMNS | UNAPPROVED_FEATURE_COLUMNS


class LeakageError(Exception):
    """Raised when a feature column would not be known at prediction time."""


def assert_feature_columns_allowed(columns: Iterable[str]) -> None:
    """Fail if any blocked name is in the feature matrix."""
    found = sorted(set(columns) & BLOCKED_FEATURE_COLUMNS)
    if found:
        raise LeakageError(
            "These columns are not allowed in the feature matrix: "
            + ", ".join(found)
            + ". See reports/features.md."
        )
