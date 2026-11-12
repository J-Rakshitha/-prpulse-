"""Bot login rules."""

from __future__ import annotations

from src.data.bots import is_bot_author


def test_bracket_bot_suffix() -> None:
    assert is_bot_author("dependabot[bot]")
    assert is_bot_author("Something[Bot]")


def test_api_user_type_bot() -> None:
    assert is_bot_author("unusual-name", user_type="Bot")


def test_configured_extra_logins() -> None:
    assert is_bot_author("github-actions", configured_logins=["github-actions"])
    assert is_bot_author("pre-commit-ci[bot]")


def test_human_author() -> None:
    assert not is_bot_author("octocat", user_type="User")
