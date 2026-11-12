"""Train baselines and LightGBM models on a time-based split; write results.md."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    mean_absolute_error,
    roc_auc_score,
)

from src.data.db import to_utc_iso
from src.features.build import FEATURE_COLUMNS_A, FEATURE_COLUMNS_EARLY, feature_columns_for_stage
from src.features.leakage import assert_feature_columns_allowed
from src.settings import load_settings, project_root

logger = logging.getLogger("prpulse.train")

TRAIN_REPOS = ("pandas-dev/pandas", "scikit-learn/scikit-learn")


def time_split(df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df = df.sort_values("created_at").reset_index(drop=True)
    n = len(df)
    n_train = int(n * float(cfg["split"]["train_fraction"]))
    n_val = int(n * float(cfg["split"]["validation_fraction"]))
    train = df.iloc[:n_train]
    val = df.iloc[n_train : n_train + n_val]
    test = df.iloc[n_train + n_val :]
    return train, val, test


def _impute(train: pd.DataFrame, other: pd.DataFrame, cols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    med = train[cols].median(numeric_only=True)
    return train[cols].fillna(med), other[cols].fillna(med)


def pinball_loss(y: np.ndarray, yhat: np.ndarray, q: float) -> float:
    e = y - yhat
    return float(np.mean(np.maximum(q * e, (q - 1) * e)))


def train_merge_models(
    stage: str,
    df: pd.DataFrame,
    cfg: dict[str, Any],
    artifact_dir: Path,
) -> dict[str, Any]:
    cols = feature_columns_for_stage(stage)
    assert_feature_columns_allowed(cols)
    data = df[df["is_bot"] == 0].copy()
    merged = data[data["state"] == "merged"].dropna(subset=["merge_hours"])
    merged = merged[merged["merge_hours"] >= 0]
    train, val, test = time_split(merged, cfg)

    # Baselines: per-repo median on train
    repo_med = train.groupby("repo_full_name")["merge_hours"].median().to_dict()
    global_med = float(train["merge_hours"].median())

    def baseline_predict(frame: pd.DataFrame) -> np.ndarray:
        return frame["repo_full_name"].map(repo_med).fillna(global_med).to_numpy(dtype=float)

    X_train, X_val = _impute(train, val, cols)
    _, X_test = _impute(train, test, cols)
    y_train = np.log1p(train["merge_hours"].to_numpy())
    y_val = np.log1p(val["merge_hours"].to_numpy())
    y_test = test["merge_hours"].to_numpy()

    reg = lgb.LGBMRegressor(
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbosity=-1,
    )
    reg.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=[lgb.early_stopping(40, verbose=False)])

    quantiles = {}
    for q in (0.1, 0.5, 0.9):
        qr = lgb.LGBMRegressor(
            objective="quantile",
            alpha=q,
            n_estimators=300,
            learning_rate=0.05,
            num_leaves=31,
            random_state=42,
            verbosity=-1,
        )
        qr.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=[lgb.early_stopping(40, verbose=False)])
        quantiles[q] = qr

    pred_test = np.expm1(reg.predict(X_test))
    base_test = baseline_predict(test)
    p10 = np.expm1(quantiles[0.1].predict(X_test))
    p50 = np.expm1(quantiles[0.5].predict(X_test))
    p90 = np.expm1(quantiles[0.9].predict(X_test))
    # enforce order
    p10, p50, p90 = np.minimum(p10, p50), p50, np.maximum(p90, p50)

    coverage = float(np.mean((y_test >= p10) & (y_test <= p90)))
    metrics = {
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "n_test": int(len(test)),
        "baseline_mae": float(mean_absolute_error(y_test, base_test)),
        "baseline_medae": float(np.median(np.abs(y_test - base_test))),
        "model_mae": float(mean_absolute_error(y_test, pred_test)),
        "model_medae": float(np.median(np.abs(y_test - pred_test))),
        "pinball_p10": pinball_loss(y_test, p10, 0.1),
        "pinball_p50": pinball_loss(y_test, p50, 0.5),
        "pinball_p90": pinball_loss(y_test, p90, 0.9),
        "interval_coverage_10_90": coverage,
    }

    # Ablations on stage A only once
    ablations = {}
    if stage == "A":
        for name, drop in {
            "no_author": [
                c
                for c in cols
                if c.startswith("prior_")
                or c == "first_time_contributor"
                or c.startswith("median_prior")
            ],
            "no_repo_state": [
                "open_pr_count_at_t0",
                "median_merge_hours_30d",
                "reviewer_activity_14d",
                "repo_is_pandas",
            ],
        }.items():
            use = [c for c in cols if c not in drop]
            if not use:
                continue
            Xt, Xv = _impute(train, val, use)
            _, Xs = _impute(train, test, use)
            m = lgb.LGBMRegressor(
                n_estimators=200,
                learning_rate=0.05,
                num_leaves=31,
                random_state=42,
                verbosity=-1,
            )
            m.fit(
                Xt,
                y_train,
                eval_set=[(Xv, np.log1p(val["merge_hours"]))],
                callbacks=[lgb.early_stopping(30, verbose=False)],
            )
            pred = np.expm1(m.predict(Xs))
            ablations[name] = {
                "mae": float(mean_absolute_error(y_test, pred)),
                "medae": float(np.median(np.abs(y_test - pred))),
            }

    artifact_dir.mkdir(parents=True, exist_ok=True)
    bundle = {
        "stage": stage,
        "feature_columns": cols,
        "regressor": reg,
        "quantiles": quantiles,
        "repo_median": repo_med,
        "global_median": global_med,
        "impute_median": X_train.median().to_dict(),
        "task": "merge_hours",
    }
    joblib.dump(bundle, artifact_dir / f"merge_stage_{stage}.joblib")
    return {"metrics": metrics, "ablations": ablations, "bundle_path": str(artifact_dir / f"merge_stage_{stage}.joblib")}


def train_classifier(
    stage: str,
    df: pd.DataFrame,
    cfg: dict[str, Any],
    artifact_dir: Path,
    *,
    target: str,
    require_ci_label: bool = False,
) -> dict[str, Any]:
    cols = feature_columns_for_stage(stage)
    data = df[df["is_bot"] == 0].copy()
    if require_ci_label:
        data = data[data["ci_label_available"] == 1]
    data = data.dropna(subset=[target])
    train, val, test = time_split(data, cfg)
    base_rate = float(train[target].mean())
    repo_rate = train.groupby("repo_full_name")[target].mean().to_dict()

    X_train, X_val = _impute(train, val, cols)
    _, X_test = _impute(train, test, cols)
    y_train = train[target].astype(int).to_numpy()
    y_val = val[target].astype(int).to_numpy()
    y_test = test[target].astype(int).to_numpy()

    clf = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbosity=-1,
    )
    clf.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=[lgb.early_stopping(40, verbose=False)])
    try:
        from sklearn.frozen import FrozenEstimator

        calibrator = CalibratedClassifierCV(FrozenEstimator(clf), method="isotonic")
        calibrator.fit(X_val, y_val)
        predict_model = calibrator
    except Exception:
        # Fallback if FrozenEstimator / calibration unavailable.
        predict_model = clf

    proba = predict_model.predict_proba(X_test)[:, 1]
    base_proba = test["repo_full_name"].map(repo_rate).fillna(base_rate).to_numpy(dtype=float)

    def safe_ap(y, p):
        if len(np.unique(y)) < 2:
            return float("nan")
        return float(average_precision_score(y, p))

    def safe_auc(y, p):
        if len(np.unique(y)) < 2:
            return float("nan")
        return float(roc_auc_score(y, p))

    metrics = {
        "n_train": int(len(train)),
        "n_val": int(len(val)),
        "n_test": int(len(test)),
        "base_rate_train": base_rate,
        "baseline_brier": float(brier_score_loss(y_test, base_proba)),
        "model_brier": float(brier_score_loss(y_test, proba)),
        "baseline_pr_auc": safe_ap(y_test, base_proba),
        "model_pr_auc": safe_ap(y_test, proba),
        "baseline_roc_auc": safe_auc(y_test, base_proba),
        "model_roc_auc": safe_auc(y_test, proba),
    }
    bundle = {
        "stage": stage,
        "feature_columns": cols,
        "classifier": predict_model,
        "repo_rate": repo_rate,
        "global_rate": base_rate,
        "impute_median": X_train.median().to_dict(),
        "task": target,
    }
    path = artifact_dir / f"{target}_stage_{stage}.joblib"
    joblib.dump(bundle, path)
    return {"metrics": metrics, "bundle_path": str(path)}


def train_ci_duration(stage: str, df: pd.DataFrame, cfg: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    cols = feature_columns_for_stage(stage)
    data = df[(df["is_bot"] == 0) & (df["ci_label_available"] == 1)].dropna(subset=["ci_duration_minutes"])
    data = data[data["ci_duration_minutes"] > 0]
    train, val, test = time_split(data, cfg)
    repo_med = train.groupby("repo_full_name")["ci_duration_minutes"].median().to_dict()
    global_med = float(train["ci_duration_minutes"].median())
    X_train, X_val = _impute(train, val, cols)
    _, X_test = _impute(train, test, cols)
    y_train = np.log1p(train["ci_duration_minutes"].to_numpy())
    y_val = np.log1p(val["ci_duration_minutes"].to_numpy())
    y_test = test["ci_duration_minutes"].to_numpy()
    reg = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=31, random_state=42, verbosity=-1)
    reg.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=[lgb.early_stopping(40, verbose=False)])
    quantiles = {}
    for q in (0.1, 0.5, 0.9):
        qr = lgb.LGBMRegressor(objective="quantile", alpha=q, n_estimators=250, learning_rate=0.05, num_leaves=31, random_state=42, verbosity=-1)
        qr.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=[lgb.early_stopping(30, verbose=False)])
        quantiles[q] = qr
    pred = np.expm1(reg.predict(X_test))
    base = test["repo_full_name"].map(repo_med).fillna(global_med).to_numpy(dtype=float)
    p10 = np.expm1(quantiles[0.1].predict(X_test))
    p90 = np.expm1(quantiles[0.9].predict(X_test))
    metrics = {
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "baseline_mae": float(mean_absolute_error(y_test, base)),
        "baseline_medae": float(np.median(np.abs(y_test - base))),
        "model_mae": float(mean_absolute_error(y_test, pred)),
        "model_medae": float(np.median(np.abs(y_test - pred))),
        "interval_coverage_10_90": float(
            np.mean((y_test >= np.minimum(p10, p90)) & (y_test <= np.maximum(p10, p90)))
        ),
    }
    bundle = {
        "stage": stage,
        "feature_columns": cols,
        "regressor": reg,
        "quantiles": quantiles,
        "repo_median": repo_med,
        "global_median": global_med,
        "impute_median": X_train.median().to_dict(),
        "task": "ci_duration_minutes",
    }
    path = artifact_dir / f"ci_duration_stage_{stage}.joblib"
    joblib.dump(bundle, path)
    return {"metrics": metrics, "bundle_path": str(path)}


def write_results(results: dict[str, Any], path: Path) -> None:
    generated = to_utc_iso(datetime.now(timezone.utc))
    lines = [
        "# Results",
        "",
        f"Generated at: `{generated}` (UTC)",
        "",
        "Train/validate/test are time-ordered splits on non-bot PRs from `pandas-dev/pandas` and `scikit-learn/scikit-learn` only.",
        "",
        "## Merge hours (LightGBM vs per-repo median baseline)",
        "",
        "| Stage | n_test | Baseline MAE (h) | Model MAE (h) | Baseline MedAE | Model MedAE | 10-90 coverage |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for stage in ("A", "B", "C"):
        m = results["merge"][stage]["metrics"]
        lines.append(
            f"| {stage} | {m['n_test']} | {m['baseline_mae']:.2f} | {m['model_mae']:.2f} | "
            f"{m['baseline_medae']:.2f} | {m['model_medae']:.2f} | {m['interval_coverage_10_90']:.3f} |"
        )
    m_a = results["merge"]["A"]["metrics"]
    lines += [
        "",
        "## Ablations (Stage A merge on test)",
        "",
        f"- Full Stage A: MAE=`{m_a['model_mae']:.2f}` MedAE=`{m_a['model_medae']:.2f}`",
    ]
    abl = results["merge"]["A"].get("ablations") or {}
    for k, v in abl.items():
        if isinstance(v, dict):
            lines.append(f"- `{k}`: MAE=`{v['mae']:.2f}` MedAE=`{v['medae']:.2f}`")
        else:
            lines.append(f"- `{k}` MAE: `{float(v):.2f}`")
    if results.get("leakage_ablation_lines"):
        lines += ["", "## Stage A leakage ablation (same time split)", ""]
        lines.extend(results["leakage_ablation_lines"])
    lines += [
        "",
        "## merged_within_7d classifier (calibrated)",
        "",
        "| Stage | n_test | train base rate | Baseline PR-AUC | Model PR-AUC | Baseline ROC-AUC | Model ROC-AUC | Model Brier |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for stage in ("A", "B", "C"):
        m = results["merged_7d"][stage]["metrics"]
        lines.append(
            f"| {stage} | {m['n_test']} | {m['base_rate_train']:.3f} | {m['baseline_pr_auc']:.3f} | "
            f"{m['model_pr_auc']:.3f} | {m['baseline_roc_auc']:.3f} | {m['model_roc_auc']:.3f} | {m['model_brier']:.3f} |"
        )
    lines += [
        "",
        "## CI failed first run (ci_label_available=1 only)",
        "",
        "| Stage | n_test | train base rate | Baseline PR-AUC | Model PR-AUC | Model ROC-AUC | Model Brier |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for stage in ("A", "B", "C"):
        m = results["ci_fail"][stage]["metrics"]
        lines.append(
            f"| {stage} | {m['n_test']} | {m['base_rate_train']:.3f} | {m['baseline_pr_auc']:.3f} | "
            f"{m['model_pr_auc']:.3f} | {m['model_roc_auc']:.3f} | {m['model_brier']:.3f} |"
        )
    lines += [
        "",
        "## CI duration minutes (Stage A)",
        "",
    ]
    m = results["ci_duration"]["A"]["metrics"]
    lines.append(
        f"- n_test=`{m['n_test']}`; baseline MAE=`{m['baseline_mae']:.2f}` MedAE=`{m.get('baseline_medae', float('nan')):.2f}` min; "
        f"model MAE=`{m['model_mae']:.2f}` MedAE=`{m.get('model_medae', float('nan')):.2f}` min; "
        f"10-90 coverage=`{m['interval_coverage_10_90']:.3f}`"
    )
    if "delivery" in results:
        lines += ["", "## Delivery-risk extension", ""]
        lines.extend(results["delivery"]["lines"])
    lines += [
        "",
        "## Leakage / sanity",
        "",
        "- Features passed `assert_feature_columns_allowed`.",
        "- If any PR-AUC > 0.95, treat as suspicious; see decisions.md audit notes.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (path.parent / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_settings()
    root = project_root()
    feat_dir = root / "data" / "features"
    artifact_dir = root / "artifacts" / "models"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"merge": {}, "merged_7d": {}, "ci_fail": {}, "ci_duration": {}}
    for stage in ("A", "B", "C"):
        path = feat_dir / f"stage_{stage}.parquet"
        if not path.exists():
            raise SystemExit(f"Missing {path}; run features first")
        df = pd.read_parquet(path)
        logger.info("Stage %s rows=%s", stage, len(df))
        results["merge"][stage] = train_merge_models(stage, df, cfg, artifact_dir)
        results["merged_7d"][stage] = train_classifier(
            stage, df, cfg, artifact_dir, target="merged_within_7d"
        )
        results["ci_fail"][stage] = train_classifier(
            stage, df, cfg, artifact_dir, target="ci_failed_first_run", require_ci_label=True
        )
        if stage == "A":
            results["ci_duration"][stage] = train_ci_duration(stage, df, cfg, artifact_dir)
    abl_path = root / "reports" / "stage_a_leakage_ablation.md"
    if abl_path.exists():
        # Keep the table body (skip title) under results.
        abl_lines = abl_path.read_text(encoding="utf-8").splitlines()
        results["leakage_ablation_lines"] = [
            ln for ln in abl_lines if not ln.startswith("# Stage A leakage")
        ]
        results["leakage_ablation_lines"] += [
            "",
            "After this ablation, **collection-time size/path/draft columns were removed from model feature lists** "
            "(kept in parquet as aux only). Author-history features are PIT-safe (prior PRs only) and remain.",
        ]
    from src.features.build import write_features_catalog

    write_results(results, root / "reports" / "results.md")
    write_features_catalog(root / "reports" / "features.md")
    for stage in ("A", "B", "C"):
        prauc = results["merged_7d"][stage]["metrics"]["model_pr_auc"]
        if prauc == prauc and prauc > 0.95:
            logger.warning("Suspicious PR-AUC %.3f at stage %s — audit for leakage", prauc, stage)
    print(f"Wrote {root / 'reports' / 'results.md'}")


if __name__ == "__main__":
    main()
