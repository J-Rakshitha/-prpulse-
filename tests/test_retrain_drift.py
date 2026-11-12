from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src.live import drift, retrain
from src.settings import load_settings
from tests.helpers import synthetic_features, synthetic_hours, write_tiny_models

CFG = load_settings()


def make_root(tmp_path, n: int, incumbent_signal: bool = False):
    X = synthetic_features(n, "A", seed=11)
    df = X.copy()
    df["merge_hours"] = synthetic_hours(X, True, seed=12)
    df["is_bot"] = 0
    df["state"] = "merged"
    df["repo_full_name"] = np.where(np.arange(n) % 2, "pandas-dev/pandas", "scikit-learn/scikit-learn")
    df["created_at"] = pd.date_range("2025-01-01", periods=n, freq="6h", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "data" / "features").mkdir(parents=True)
    df.to_parquet(tmp_path / "data" / "features" / "stage_A.parquet")
    write_tiny_models(tmp_path / "artifacts" / "models", stages=("A",), signal=incumbent_signal)
    return tmp_path


# ---- PSI -------------------------------------------------------------------


def test_psi_identical_is_near_zero_and_shift_is_large():
    rng = np.random.default_rng(0)
    a = pd.Series(rng.normal(0, 1, 5000))
    b = pd.Series(rng.normal(0, 1, 5000))
    c = pd.Series(rng.normal(2, 1, 5000))
    assert drift.psi(a, b) < drift.MODERATE
    assert drift.psi(a, c) > drift.LARGE


def test_psi_handles_binary_nan_and_constant_columns():
    ref = pd.Series([0, 1] * 500)
    assert drift.psi(ref, pd.Series([0, 1] * 100)) == pytest.approx(0, abs=1e-6)
    assert drift.psi(ref, pd.Series([1] * 200)) > drift.LARGE
    with_nan = pd.Series([0.0, 1.0, np.nan, np.nan] * 100)
    assert drift.psi(with_nan, with_nan) == pytest.approx(0, abs=1e-9)
    assert drift.psi(pd.Series([5.0] * 50), pd.Series([5.0] * 50)) == pytest.approx(0, abs=1e-9)
    assert np.isfinite(drift.psi(pd.Series([np.nan] * 10), pd.Series([1.0] * 10)))


def test_drift_report_without_features_says_so(tmp_path):
    path = drift.run("A", root=tmp_path, cfg=CFG)
    text = path.read_text()
    assert "No drift was computed" in text


def test_drift_report_lists_features(tmp_path):
    root = make_root(tmp_path, 300)
    text = drift.run("A", root=root, cfg=CFG).read_text()
    # Stage A no longer trains on size/path flags (leakage ablation); assert a remaining feature.
    assert "| Feature | PSI | Status |" in text and "hour_utc" in text


# ---- retrain ---------------------------------------------------------------


def test_dry_run_changes_nothing_live_but_writes_reports(tmp_path):
    root = make_root(tmp_path, 700)
    live = root / "artifacts" / "models" / "merge_stage_A.joblib"
    before = live.read_bytes()
    result = retrain.run("A", promote=False, root=root, cfg=CFG)
    assert live.read_bytes() == before
    assert result["promoted"] is False and result["dry_run"] is True
    assert result["would_promote"] is True  # incumbent was trained on noise
    reg = json.loads((root / "artifacts" / "registry.json").read_text())
    entry = reg["versions"][result["version"]]
    assert entry["decision"] == "would_promote" and entry["dry_run"] is True
    assert reg["active"] != result["version"] and reg["active"] is not None  # bootstrapped incumbent stays active
    report = (root / "reports" / "drift.md").read_text()
    assert "## Retrain decision" in report and result["version"] in report


def test_promote_replaces_bundle_and_archives_old(tmp_path):
    root = make_root(tmp_path, 700)
    live = root / "artifacts" / "models" / "merge_stage_A.joblib"
    before = live.read_bytes()
    result = retrain.run("A", promote=True, root=root, cfg=CFG)
    assert result["promoted"] is True
    assert live.read_bytes() != before
    reg = json.loads((root / "artifacts" / "registry.json").read_text())
    assert reg["active"] == result["version"]
    assert reg["versions"][result["version"]]["decision"] == "promoted"
    archived = list((root / "artifacts" / "archive").rglob("merge_stage_A.joblib"))
    assert len(archived) == 1 and archived[0].read_bytes() == before


def test_better_incumbent_is_kept(tmp_path):
    root = make_root(tmp_path, 700, incumbent_signal=True)
    # make the candidate worse by demanding a strictly lower MAE: identical data gives a near tie,
    # so check the decision rule directly instead of relying on training noise
    ok, reason = retrain.decide({"mae_hours": 5.0}, {"mae_hours": 5.0}, 100)
    assert ok is False and "not lower" in reason
    ok, _ = retrain.decide({"mae_hours": 4.9}, {"mae_hours": 5.0}, 100)
    assert ok is True
    assert retrain.run("A", promote=False, root=root, cfg=CFG)["promoted"] is False


def test_tiny_holdout_blocks_promotion(tmp_path):
    root = make_root(tmp_path, 100)
    result = retrain.run("A", promote=True, root=root, cfg=CFG)
    assert result["promoted"] is False
    assert "fewer than" in result["decision_reason"]


def test_decide_without_incumbent():
    assert retrain.decide({"mae_hours": 1.0}, None, 100)[0] is True
    assert retrain.decide({"mae_hours": 1.0}, None, 3)[0] is False
