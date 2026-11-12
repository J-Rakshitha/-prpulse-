"""Task commands dispatch to the real entry points (stubbed here so nothing heavy runs)."""

from __future__ import annotations

import importlib

import pytest

from tasks import COMMANDS, cmd_features, cmd_serve, cmd_train


@pytest.mark.parametrize(
    ("command", "module"),
    [
        (cmd_features, "src.features.build"),
        (cmd_train, "src.models.train"),
        (cmd_serve, "src.service.api"),
    ],
)
def test_commands_call_module_main(command, module: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(importlib.import_module(module), "main", lambda: calls.append(module))
    command()
    assert calls == [module]


def test_all_commands_registered() -> None:
    assert set(COMMANDS) == {"init-db", "collect", "features", "train", "serve", "test"}
