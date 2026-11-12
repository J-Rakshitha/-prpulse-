"""Task runner for PR Pulse.

GNU make is not required. From the repository root:

    python tasks.py init-db
    python tasks.py test
    python tasks.py collect
    python tasks.py features
    python tasks.py train
    python tasks.py serve

collect, features, train, and serve are wired and intentionally stop until
their phase is built.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_init_db() -> None:
    from src import setup_logging
    from src.data.db import SCHEMA_VERSION, init_db, resolve_db_path
    from src.settings import load_settings, project_root

    cfg = load_settings()
    logger = setup_logging(cfg["logging"]["level"], project_root() / cfg["logging"]["file"])
    path = resolve_db_path(cfg)
    init_db(path)
    logger.info("SQLite database ready at %s (schema version %s)", path, SCHEMA_VERSION)
    print(f"SQLite database ready at {path}")


def cmd_collect() -> None:
    from src.data.collect import main

    main()


def cmd_features() -> None:
    from src.features.build import main

    main()


def cmd_train() -> None:
    from src.models.train import main

    main()


def cmd_serve() -> None:
    from src.service.api import main

    main()


def cmd_test() -> None:
    import pytest

    raise SystemExit(pytest.main(["-q", str(Path(__file__).resolve().parent / "tests")]))


COMMANDS = {
    "init-db": cmd_init_db,
    "collect": cmd_collect,
    "features": cmd_features,
    "train": cmd_train,
    "serve": cmd_serve,
    "test": cmd_test,
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="PR Pulse tasks")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    COMMANDS[args.command]()


if __name__ == "__main__":
    main(sys.argv[1:])
