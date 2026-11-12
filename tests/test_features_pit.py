"""Point-in-time reconstruction and leakage checks for the feature builder."""

from __future__ import annotations

import math
import random
from pathlib import Path

import pandas as pd
import pytest

from src.data.db import connect, resolve_db_path
from src.features.build import (
    FEATURE_COLUMNS_A,
    FEATURE_COLUMNS_EARLY,
    feature_columns_for_stage,
    rebuild_features_for_pr,
    write_features_catalog,
)
from src.features.leakage import assert_feature_columns_allowed
from src.settings import load_settings, project_root


@pytest.fixture(scope="module")
def cfg():
    return load_settings()


@pytest.fixture(scope="module")
def conn(cfg):
    path = resolve_db_path(cfg)
    if not path.exists():
        pytest.skip("no database")
    with connect(path) as c:
        yield c


def test_feature_columns_not_blocked() -> None:
    assert_feature_columns_allowed(FEATURE_COLUMNS_A)
    assert_feature_columns_allowed(FEATURE_COLUMNS_EARLY)


def test_features_md_lists_blocked_and_approved() -> None:
    write_features_catalog(project_root() / "reports" / "features.md")
    text = (project_root() / "reports" / "features.md").read_text(encoding="utf-8")
    for name in FEATURE_COLUMNS_A + FEATURE_COLUMNS_EARLY:
        assert f"`{name}`" in text


def test_pit_reconstruction_sample(conn, cfg) -> None:
    """Fresh full Stage B rebuild must match the stored matrix on 50 random PRs.

    Single-PR rebuilds are not valid for author/repo/file-history features (they
    need earlier rows). Equality is checked against a second full build.
    """
    from src.features.build import build_stage_matrix

    path = project_root() / "data" / "features" / "stage_B.parquet"
    if not path.exists():
        pytest.skip("features not built")
    stored = pd.read_parquet(path).set_index("pr_id")
    rebuilt = build_stage_matrix(conn, stage="B", cfg=cfg).set_index("pr_id")
    assert len(stored) > 50
    sample_ids = random.Random(42).sample(stored.index.astype(int).tolist(), 50)
    cols = feature_columns_for_stage("B")
    for pr_id in sample_ids:
        for col in cols:
            a = stored.loc[pr_id, col]
            b = rebuilt.loc[pr_id, col]
            if pd.isna(a) and pd.isna(b):
                continue
            assert a == pytest.approx(float(b), nan_ok=True, rel=1e-5, abs=1e-5), (
                pr_id,
                col,
                a,
                b,
            )
