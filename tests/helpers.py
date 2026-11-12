"""Synthetic data and tiny model bundles so service tests run without real artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from src.features.build import feature_columns_for_stage


def synthetic_features(n: int, stage: str = "A", seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    cols = feature_columns_for_stage(stage)
    df = pd.DataFrame(0.0, index=range(n), columns=cols)
    additions = rng.integers(1, 2000, n).astype(float)
    deletions = rng.integers(0, 500, n).astype(float)
    files = rng.integers(1, 40, n).astype(float)
    df["additions_approx"] = additions
    df["deletions_approx"] = deletions
    df["n_files_approx"] = files
    df["log1p_additions_approx"] = np.log1p(additions)
    df["log1p_deletions_approx"] = np.log1p(deletions)
    df["log1p_n_files_approx"] = np.log1p(files)
    df["commits_at_open"] = rng.integers(1, 10, n)
    for flag in ("draft", "touches_tests", "touches_docs", "touches_ci_config", "touches_deps", "first_time_contributor", "repo_is_pandas"):
        df[flag] = rng.integers(0, 2, n)
    df["hour_utc"] = rng.integers(0, 24, n)
    df["weekday"] = rng.integers(0, 7, n)
    df["is_weekend"] = (df["weekday"] >= 5).astype(int)
    df["month"] = rng.integers(1, 13, n)
    df["open_pr_count_at_t0"] = rng.integers(10, 400, n)
    df["median_merge_hours_30d"] = rng.uniform(5, 200, n)
    df["prior_opened_count"] = rng.integers(0, 50, n)
    df["prior_merged_count"] = (df["prior_opened_count"] * rng.uniform(0, 1, n)).astype(int)
    if "reviewer_assigned_by_cutoff" in df.columns:
        df["reviewer_assigned_by_cutoff"] = rng.integers(0, 2, n)
        df["n_reviewers_by_cutoff"] = df["reviewer_assigned_by_cutoff"] * rng.integers(1, 3, n)
    return df


def synthetic_hours(df: pd.DataFrame, signal: bool = True, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if not signal:
        return np.exp(rng.normal(3.0, 1.5, len(df)))
    log_h = 1.5 + 0.45 * df["log1p_additions_approx"] - 0.9 * df["touches_tests"] + 0.15 * df["draft"]
    if "reviewer_assigned_by_cutoff" in df.columns:
        log_h = log_h - 0.8 * df["reviewer_assigned_by_cutoff"]
    return np.exp(log_h + rng.normal(0, 0.1, len(df))).to_numpy()


def make_bundles(stage: str = "A", n: int = 400, signal: bool = True, seed: int = 0) -> dict[str, dict[str, Any]]:
    X = synthetic_features(n, stage, seed)
    hours = synthetic_hours(X, signal, seed + 1)
    y = np.log1p(hours)
    kw = dict(n_estimators=60, learning_rate=0.1, num_leaves=15, min_child_samples=5, random_state=0, verbosity=-1)
    reg = lgb.LGBMRegressor(**kw).fit(X, y)
    quantiles = {q: lgb.LGBMRegressor(objective="quantile", alpha=q, **kw).fit(X, y) for q in (0.1, 0.5, 0.9)}
    merged_7d = (hours <= 168).astype(int)
    clf = lgb.LGBMClassifier(**kw).fit(X, merged_7d)
    cols = list(X.columns)
    medians = X.median().to_dict()
    merge_bundle = {
        "stage": stage,
        "feature_columns": cols,
        "regressor": reg,
        "quantiles": quantiles,
        "repo_median": {},
        "global_median": float(np.median(hours)),
        "impute_median": medians,
        "task": "merge_hours",
    }
    clf_bundle = {
        "stage": stage,
        "feature_columns": cols,
        "classifier": clf,
        "repo_rate": {},
        "global_rate": float(merged_7d.mean()),
        "impute_median": medians,
        "task": "merged_within_7d",
    }
    return {"merge_hours": merge_bundle, "merged_within_7d": clf_bundle}


def write_tiny_models(models_dir: Path, stages: tuple[str, ...] = ("A", "B"), signal: bool = True) -> Path:
    models_dir.mkdir(parents=True, exist_ok=True)
    for stage in stages:
        bundles = make_bundles(stage, signal=signal)
        joblib.dump(bundles["merge_hours"], models_dir / f"merge_stage_{stage}.joblib")
        joblib.dump(bundles["merged_within_7d"], models_dir / f"merged_within_7d_stage_{stage}.joblib")
    return models_dir


SAMPLE_FEATURES: dict[str, Any] = {
    "draft": 0,
    "commits_at_open": 3,
    "n_files_approx": 8,
    "additions_approx": 900,
    "deletions_approx": 40,
    "touches_tests": 0,
    "touches_docs": 0,
    "first_time_contributor": 1,
    "hour_utc": 14,
    "weekday": 2,
}
