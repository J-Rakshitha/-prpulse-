"""PR Pulse package.

Phase 0 provides logging, settings, and the database. Later phases fill in
collection, features, models, the API, and the bot.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.logging_filter import install as install_redaction, register_env_secrets

LOGGER_NAME = "prpulse"


def setup_logging(level: str, log_file: str | Path | None = None) -> logging.Logger:
    """Configure the project logger.

    Logs go to stderr and, when log_file is set, to that path. The logger is
    named ``prpulse`` and does not propagate to the root logger, so secrets
    logged by accident in a dependency are not duplicated here. Callers must
    still avoid logging tokens, webhook secrets, and request signatures.
    """
    register_env_secrets()
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(level.upper())
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    install_redaction(stream)
    logger.addHandler(stream)

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        install_redaction(file_handler)
        logger.addHandler(file_handler)

    return logger
