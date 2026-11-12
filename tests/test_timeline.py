"""Timeline event mapping."""

from __future__ import annotations

from src.data.timeline import map_timeline_event


def test_review_requested_maps_to_reviewer_assigned() -> None:
    mapped = map_timeline_event(
        {
            "event": "review_requested",
            "id": 11,
            "created_at": "2024-06-01T12:00:00Z",
            "actor": {"login": "maintainer"},
            "requested_reviewer": {"login": "reviewer"},
        }
    )
    assert mapped is not None
    assert mapped["event_type"] == "reviewer_assigned"
    assert mapped["occurred_at"] == "2024-06-01T12:00:00Z"
    assert mapped["actor_login"] == "maintainer"


def test_committed_uses_author_date() -> None:
    mapped = map_timeline_event(
        {
            "event": "committed",
            "sha": "abc123",
            "author": {"name": "Ada", "email": "ada@example.com", "date": "2024-06-01T12:00:00.123Z"},
        }
    )
    assert mapped is not None
    assert mapped["event_type"] == "synchronize"
    assert mapped["occurred_at"] == "2024-06-01T12:00:00Z"
    assert mapped["source_key"] == "committed:abc123"


def test_unmapped_event_is_skipped() -> None:
    assert map_timeline_event({"event": "subscribed", "id": 1, "created_at": "2024-06-01T12:00:00Z"}) is None


def test_reviewed_uses_submitted_at() -> None:
    mapped = map_timeline_event(
        {
            "event": "reviewed",
            "id": 9,
            "submitted_at": "2024-06-01T13:00:00Z",
            "user": {"login": "reviewer"},
            "state": "approved",
        }
    )
    assert mapped is not None
    assert mapped["event_type"] == "review_submitted"
    assert mapped["occurred_at"] == "2024-06-01T13:00:00Z"
