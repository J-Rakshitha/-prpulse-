"""Shared fixtures. Set PRPULSE_TEST_DATABASE_URL to run the suite on Postgres."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from tests.helpers import write_tiny_models


@pytest.fixture(scope="session")
def tiny_models_dir(tmp_path_factory) -> Path:
    """Small LightGBM bundles for stages A and B, built once per test session."""
    return write_tiny_models(tmp_path_factory.mktemp("models") / "models")


@pytest.fixture(autouse=True)
def _postgres_test_db():
    """Route Path/*.db init/connect to a per-test Postgres schema when configured."""
    base = os.environ.get("PRPULSE_TEST_DATABASE_URL", "").strip()
    if not base:
        yield
        return

    from src.data import database as database_mod

    schema = f"t_{uuid.uuid4().hex[:12]}"
    admin = database_mod.connect(base)
    try:
        admin.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        admin.commit()
    finally:
        admin.close()

    sep = "&" if "?" in base else "?"
    scoped = f"{base}{sep}options=-csearch_path%3D{schema}"
    token = database_mod.set_test_database_url(scoped)
    try:
        yield
    finally:
        database_mod.reset_test_database_url(token)
        cleanup = database_mod.connect(base)
        try:
            cleanup.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            cleanup.commit()
        except Exception:  # noqa: BLE001
            pass
        finally:
            cleanup.close()
