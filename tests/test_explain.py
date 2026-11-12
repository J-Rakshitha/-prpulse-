from __future__ import annotations

import math

import numpy as np
import pytest

from src.models import explain as explain_mod
from src.models.explain import ESTIMATE_LABEL, assert_signed_contributions, explain, explain_with_method, suggestions
from src.models.registry import default_models_dir, features_to_frame, load_bundle
from tests.helpers import SAMPLE_FEATURES, synthetic_features


@pytest.fixture(scope="module")
def bundle(tiny_models_dir):
    return load_bundle("A", tiny_models_dir)


def test_explain_shape_and_sign(bundle):
    items = explain(SAMPLE_FEATURES, bundle=bundle)
    assert 1 <= len(items) <= 5
    for it in items:
        assert set(it) >= {"feature", "contribution", "plain_english"}
        assert isinstance(it["contribution"], float) and math.isfinite(it["contribution"])
        assert it["plain_english"]
    mags = [abs(i["contribution"]) for i in items]
    assert mags == sorted(mags, reverse=True)


def test_contributions_are_signed_across_rows(bundle):
    """Both signs appear across rows, so values are not absolute magnitudes."""
    rows = synthetic_features(40, "A", seed=5)
    signs = set()
    for i in range(len(rows)):
        for it in explain(rows.iloc[[i]], bundle=bundle):
            signs.add(np.sign(it["contribution"]))
    assert 1.0 in signs and -1.0 in signs


def test_shap_values_are_additive(bundle):
    items, method, base = explain_with_method(SAMPLE_FEATURES, bundle=bundle, top_k=len(bundle["feature_columns"]))
    assert method == "shap"
    frame, _ = features_to_frame(SAMPLE_FEATURES, bundle)
    predicted = float(bundle["regressor"].predict(frame)[0])
    assert base is not None
    assert base + sum(i["contribution"] for i in items) == pytest.approx(predicted, abs=1e-4)


def test_direction_matches_model(bundle):
    """Large additions should lengthen the forecast relative to small additions."""
    big = dict(SAMPLE_FEATURES, additions_approx=1800)
    small = dict(SAMPLE_FEATURES, additions_approx=5)

    def additions_effect(feats):
        # the raw and log columns carry the same information, so trees may credit either
        items = explain_with_method(feats, bundle=bundle, top_k=40)[0]
        return sum(i["contribution"] for i in items if "additions" in i["feature"])

    assert additions_effect(big) > 0 > additions_effect(small)


def test_fallback_when_shap_fails(bundle, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("shap unavailable")

    monkeypatch.setattr(explain_mod, "_shap_contributions", boom)
    items, method, base = explain_with_method(SAMPLE_FEATURES, bundle=bundle)
    assert method == "difference"
    assert base is None
    assert_signed_contributions(items)


def test_assert_signed_contributions_rejects_bad_input():
    with pytest.raises(AssertionError):
        assert_signed_contributions([])
    with pytest.raises(AssertionError):
        assert_signed_contributions([{"contribution": 0.0}])
    with pytest.raises(AssertionError):
        assert_signed_contributions([{"contribution": float("nan")}])


def test_suggestions_are_labelled_estimates(bundle):
    out = suggestions(SAMPLE_FEATURES, bundle=bundle)
    ids = {s["id"] for s in out}
    assert "add_tests" in ids and "smaller_pr" in ids
    assert "assign_reviewer" not in ids  # not a stage A feature
    for s in out:
        assert s["is_estimate"] is True
        assert s["label"] == ESTIMATE_LABEL
        assert s["estimated_change_hours"] < 0
        assert s["predicted_hours_after"] < s["predicted_hours_before"]
    deltas = [s["estimated_change_hours"] for s in out]
    assert deltas == sorted(deltas)


def test_reviewer_suggestion_at_stage_b(tiny_models_dir):
    b = load_bundle("B", tiny_models_dir)
    feats = dict(SAMPLE_FEATURES, reviewer_assigned_by_cutoff=0)
    out = suggestions(feats, stage="B", bundle=b)
    assert "assign_reviewer" in {s["id"] for s in out}


def test_no_suggestion_for_already_done_actions(bundle):
    feats = dict(SAMPLE_FEATURES, touches_tests=1, additions_approx=1)
    ids = {s["id"] for s in suggestions(feats, bundle=bundle)}
    assert "add_tests" not in ids and "smaller_pr" not in ids


@pytest.mark.skipif(not (default_models_dir() / "merge_stage_A.joblib").exists(), reason="no trained artifacts")
def test_real_model_contributions_sane():
    items = explain(SAMPLE_FEATURES, stage="A")
    assert items
    assert_signed_contributions(items)
