"""Per-PR explanations and what-if suggestions for the merge-time model.

Contributions are signed and live in log1p(hours) space (the space the
regressor is trained in). A positive value pushes the predicted merge time up,
a negative value pushes it down. ``plain_english`` converts that into an
approximate percentage change of the predicted time.

Suggestions are model what-ifs: change one input, re-predict, report the
difference. They describe association in the training data, not a causal effect.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.models.registry import (
    features_to_frame,
    load_bundle,
    predict_point_hours,
)

logger = logging.getLogger("prpulse.explain")

ESTIMATE_LABEL = "Estimate only: model what-if on historical patterns, not a guarantee and not causal."

FEATURE_LABELS = {
    "draft": "PR is a draft",
    "commits_at_open": "number of commits",
    "n_files_approx": "number of files changed",
    "additions_approx": "lines added",
    "deletions_approx": "lines deleted",
    "log1p_additions_approx": "size of additions (log scale)",
    "log1p_deletions_approx": "size of deletions (log scale)",
    "log1p_n_files_approx": "number of files (log scale)",
    "touches_tests": "touches test files",
    "touches_docs": "touches documentation",
    "touches_ci_config": "touches CI configuration",
    "touches_deps": "touches dependency files",
    "touches_auth": "touches auth/security paths",
    "touches_migration": "touches migrations",
    "docs_only": "documentation-only change",
    "prior_opened_count": "author's earlier PRs in this repo",
    "prior_merged_count": "author's earlier merged PRs",
    "prior_merge_rate": "author's historical merge rate",
    "first_time_contributor": "first-time contributor",
    "median_prior_merge_hours": "author's typical merge time",
    "prior_ci_fail_rate": "author's historical CI failure rate",
    "file_hist_fail_mean": "average CI failure history of touched files",
    "file_hist_fail_max": "worst CI failure history of touched files",
    "hour_utc": "hour of day opened (UTC)",
    "weekday": "day of week opened",
    "is_weekend": "opened on a weekend",
    "month": "month opened",
    "is_friday_evening": "opened on Friday evening",
    "open_pr_count_at_t0": "open PRs in the repo",
    "median_merge_hours_30d": "recent repo median merge time",
    "reviewer_activity_14d": "recent reviewer activity in the repo",
    "repo_is_pandas": "repository is pandas",
    "reviewer_assigned_by_cutoff": "a reviewer is assigned",
    "n_reviewers_by_cutoff": "number of assigned reviewers",
    "minutes_to_first_comment": "minutes until first comment",
    "minutes_to_first_ci": "minutes until CI started",
    "ci_finished_by_cutoff": "CI has finished",
    "ci_failed_by_cutoff": "CI has failed",
    "review_submitted_by_cutoff": "a review was submitted",
    "n_comments_by_cutoff": "number of comments so far",
    "n_synchronize_by_cutoff": "number of pushes so far",
}

_explainer_cache: dict[int, Any] = {}


def _label(feature: str) -> str:
    return FEATURE_LABELS.get(feature, feature.replace("_", " "))


def _fmt_value(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.3g}"


def plain_english(feature: str, value: float, contribution: float) -> str:
    """One sentence describing a signed log-space contribution."""
    if contribution == 0 or abs(contribution) < 1e-9:
        return f"{_label(feature)} (= {_fmt_value(value)}) has no measurable effect on the predicted merge time."
    if contribution > 0:
        pct = (math.exp(contribution) - 1.0) * 100.0
        verb = "lengthens"
    else:
        pct = (1.0 - math.exp(contribution)) * 100.0
        verb = "shortens"
    return (
        f"{_label(feature)} (= {_fmt_value(value)}) {verb} the predicted merge time "
        f"by roughly {pct:.0f}% relative to a typical PR (estimate)."
    )


def assert_signed_contributions(items: list[dict[str, Any]]) -> None:
    """Fail loudly if contributions are empty, non-finite, unsigned, or all zero."""
    assert items, "no contributions were produced"
    values = [it["contribution"] for it in items]
    assert all(isinstance(v, float) and math.isfinite(v) for v in values), "contributions must be finite floats"
    assert any(v != 0.0 for v in values), "all contributions are zero; the explanation carries no signal"
    mags = [abs(v) for v in values]
    assert mags == sorted(mags, reverse=True), "contributions must be ordered by absolute size"


def _shap_contributions(bundle: dict[str, Any], frame: pd.DataFrame) -> tuple[np.ndarray, float]:
    import shap

    reg = bundle["regressor"]
    explainer = _explainer_cache.get(id(reg))
    if explainer is None:
        explainer = shap.TreeExplainer(reg)
        _explainer_cache[id(reg)] = explainer
    values = np.asarray(explainer.shap_values(frame))
    if values.ndim == 3:  # some versions return (n, features, outputs)
        values = values[..., 0]
    base = np.asarray(explainer.expected_value).reshape(-1)[0]
    return values[0], float(base)


def _difference_contributions(bundle: dict[str, Any], frame: pd.DataFrame) -> tuple[np.ndarray, float]:
    """Fallback: effect of replacing each feature by its training median."""
    reg = bundle["regressor"]
    medians = bundle.get("impute_median", {})
    full = float(reg.predict(frame)[0])
    out = np.zeros(frame.shape[1], dtype=float)
    for i, col in enumerate(frame.columns):
        alt = frame.copy()
        alt.iloc[0, i] = float(medians.get(col, 0.0))
        out[i] = full - float(reg.predict(alt)[0])
    return out, float("nan")


def explain_with_method(
    pr_features: dict[str, Any] | pd.DataFrame,
    stage: str = "A",
    models_dir: Path | None = None,
    top_k: int = 5,
    bundle: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str, float | None]:
    """Return (top contributions, method, base_value). Method is ``shap`` or ``difference``."""
    bundle = bundle if bundle is not None else load_bundle(stage, models_dir)
    frame, _ = features_to_frame(pr_features, bundle)
    method = "shap"
    try:
        contribs, base = _shap_contributions(bundle, frame)
    except Exception as exc:  # noqa: BLE001 - shap is optional and version-sensitive
        logger.warning("SHAP failed (%s); using prediction-difference fallback", exc)
        method = "difference"
        contribs, base = _difference_contributions(bundle, frame)
    order = np.argsort(-np.abs(contribs))[:top_k]
    items = []
    for idx in order:
        feature = str(frame.columns[idx])
        value = float(frame.iloc[0, idx])
        c = float(contribs[idx])
        items.append(
            {
                "feature": feature,
                "contribution": c,
                "value": value,
                "plain_english": plain_english(feature, value, c),
            }
        )
    assert_signed_contributions(items)
    return items, method, (None if math.isnan(base) else base)


def explain(
    pr_features: dict[str, Any] | pd.DataFrame,
    stage: str = "A",
    models_dir: Path | None = None,
    top_k: int = 5,
    bundle: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Top contributions as ``{feature, contribution, plain_english}`` (plus ``value``)."""
    items, _, _ = explain_with_method(pr_features, stage, models_dir, top_k, bundle)
    return items


def _scenarios(features: dict[str, Any], columns: set[str]) -> list[tuple[str, str, dict[str, Any]]]:
    """(id, description, feature overrides) for each applicable what-if."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    if "touches_tests" in columns and not float(features.get("touches_tests") or 0):
        out.append(("add_tests", "Include or update tests (touches_tests = 1)", {"touches_tests": 1.0}))
    additions = features.get("additions_approx")
    if "additions_approx" in columns and additions is not None and not pd.isna(additions) and float(additions) >= 2:
        reduced = float(additions) / 2.0
        out.append(
            (
                "smaller_pr",
                "Split the change so it adds about half as many lines (additions_approx x 0.5)",
                {"additions_approx": reduced, "log1p_additions_approx": math.log1p(reduced)},
            )
        )
    if "reviewer_assigned_by_cutoff" in columns and not float(features.get("reviewer_assigned_by_cutoff") or 0):
        out.append(
            (
                "assign_reviewer",
                "Request a reviewer early (reviewer_assigned_by_cutoff = 1)",
                {
                    "reviewer_assigned_by_cutoff": 1.0,
                    "n_reviewers_by_cutoff": max(float(features.get("n_reviewers_by_cutoff") or 0), 1.0),
                },
            )
        )
    return out


def suggestions(
    pr_features: dict[str, Any] | pd.DataFrame,
    stage: str = "A",
    models_dir: Path | None = None,
    bundle: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """What-if suggestions that lower the predicted merge time, best first.

    Each item is labelled as an estimate. Scenarios whose feature does not exist
    at this stage (for example reviewer assignment at stage A) are skipped.
    """
    bundle = bundle if bundle is not None else load_bundle(stage, models_dir)
    base_frame, _ = features_to_frame(pr_features, bundle)
    current = base_frame.iloc[0].to_dict()
    before = predict_point_hours(bundle, base_frame)
    result = []
    for sid, description, overrides in _scenarios(current, set(base_frame.columns)):
        alt = base_frame.copy()
        for key, val in overrides.items():
            if key in alt.columns:
                alt.loc[alt.index[0], key] = float(val)
        after = predict_point_hours(bundle, alt)
        delta = after - before
        if delta >= 0:
            continue
        result.append(
            {
                "id": sid,
                "action": description,
                "feature_changes": {k: float(v) for k, v in overrides.items() if k in alt.columns},
                "predicted_hours_before": before,
                "predicted_hours_after": after,
                "estimated_change_hours": delta,
                "is_estimate": True,
                "label": ESTIMATE_LABEL,
            }
        )
    result.sort(key=lambda r: r["estimated_change_hours"])
    return result
