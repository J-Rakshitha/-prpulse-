"""Diagnose Stage A merge MedAE vs per-repo median baseline; try fix variants."""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error

from src.features.build import feature_columns_for_stage
from src.models.train import _impute, time_split
from src.settings import load_settings, project_root


def hours_to_bucket(h: np.ndarray) -> np.ndarray:
    """0: <1d, 1: 1-7d, 2: >7d."""
    out = np.zeros(len(h), dtype=int)
    out[(h >= 24) & (h <= 168)] = 1
    out[h > 168] = 2
    return out


def main() -> None:
    cfg = load_settings()
    root = project_root()
    df = pd.read_parquet(root / "data" / "features" / "stage_A.parquet")
    cols = feature_columns_for_stage("A")
    data = df[(df["is_bot"] == 0) & (df["state"] == "merged")].dropna(subset=["merge_hours"])
    data = data[data["merge_hours"] >= 0]
    train, val, test = time_split(data, cfg)

    repo_med = train.groupby("repo_full_name")["merge_hours"].median().to_dict()
    global_med = float(train["merge_hours"].median())
    base = test["repo_full_name"].map(repo_med).fillna(global_med).to_numpy(dtype=float)
    y = test["merge_hours"].to_numpy(dtype=float)
    y_log = np.log1p(y)
    base_log = np.log1p(base)

    X_train, X_val = _impute(train, val, cols)
    _, X_test = _impute(train, test, cols)
    y_train_log = np.log1p(train["merge_hours"].to_numpy())
    y_val_log = np.log1p(val["merge_hours"].to_numpy())
    y_train_h = train["merge_hours"].to_numpy()
    y_val_h = val["merge_hours"].to_numpy()

    rows = []

    def add_row(name: str, pred_h: np.ndarray, extra: dict | None = None) -> None:
        pred_h = np.asarray(pred_h, dtype=float)
        row = {
            "variant": name,
            "mae_hours": float(mean_absolute_error(y, pred_h)),
            "medae_hours": float(np.median(np.abs(y - pred_h))),
            "mae_log1p": float(mean_absolute_error(y_log, np.log1p(np.clip(pred_h, 0, None)))),
            "beats_baseline_medae": float(np.median(np.abs(y - pred_h))) < float(np.median(np.abs(y - base))),
        }
        if extra:
            row.update(extra)
        rows.append(row)
        print(
            f"{name}: MAE={row['mae_hours']:.2f} MedAE={row['medae_hours']:.2f} "
            f"logMAE={row['mae_log1p']:.3f} beats_MedAE={row['beats_baseline_medae']}"
        )

    add_row(
        "baseline_per_repo_median",
        base,
        {"mae_log1p": float(mean_absolute_error(y_log, base_log))},
    )

    # (current) L2 on log1p hours
    reg_l2 = lgb.LGBMRegressor(
        n_estimators=300, learning_rate=0.05, num_leaves=31, subsample=0.8,
        colsample_bytree=0.8, random_state=42, verbosity=-1,
    )
    reg_l2.fit(X_train, y_train_log, eval_set=[(X_val, y_val_log)], callbacks=[lgb.early_stopping(40, verbose=False)])
    pred_l2 = np.expm1(reg_l2.predict(X_test))
    add_row("l2_on_log1p_expm1", pred_l2)

    # (a) quantile alpha=0.5 on log1p
    reg_q = lgb.LGBMRegressor(
        objective="quantile", alpha=0.5, n_estimators=300, learning_rate=0.05,
        num_leaves=31, random_state=42, verbosity=-1,
    )
    reg_q.fit(X_train, y_train_log, eval_set=[(X_val, y_val_log)], callbacks=[lgb.early_stopping(40, verbose=False)])
    pred_q = np.expm1(reg_q.predict(X_test))
    add_row("quantile_p50_on_log1p_expm1", pred_q)

    # quantile p50 on raw hours (no log)
    reg_q_raw = lgb.LGBMRegressor(
        objective="quantile", alpha=0.5, n_estimators=300, learning_rate=0.05,
        num_leaves=31, random_state=42, verbosity=-1,
    )
    reg_q_raw.fit(X_train, y_train_h, eval_set=[(X_val, y_val_h)], callbacks=[lgb.early_stopping(40, verbose=False)])
    pred_q_raw = reg_q_raw.predict(X_test)
    add_row("quantile_p50_on_raw_hours", pred_q_raw)

    # (c) bucket classifier
    yb_train = hours_to_bucket(y_train_h)
    yb_val = hours_to_bucket(y_val_h)
    yb_test = hours_to_bucket(y)
    clf = lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.05, num_leaves=31, random_state=42, verbosity=-1,
    )
    clf.fit(X_train, yb_train, eval_set=[(X_val, yb_val)], callbacks=[lgb.early_stopping(40, verbose=False)])
    pred_b = clf.predict(X_test)
    # map bucket -> representative hours (train medians within bucket)
    bucket_med = {}
    for b in (0, 1, 2):
        mask = yb_train == b
        bucket_med[b] = float(np.median(y_train_h[mask])) if mask.any() else float(global_med)
    pred_from_bucket = np.array([bucket_med[int(b)] for b in pred_b], dtype=float)
    base_bucket = hours_to_bucket(base)
    add_row(
        "bucket_clf_then_bucket_median_hours",
        pred_from_bucket,
        {
            "bucket_accuracy": float(accuracy_score(yb_test, pred_b)),
            "bucket_macro_f1": float(f1_score(yb_test, pred_b, average="macro")),
            "baseline_bucket_accuracy": float(accuracy_score(yb_test, base_bucket)),
            "baseline_bucket_macro_f1": float(f1_score(yb_test, base_bucket, average="macro")),
        },
    )

    # Diagnosis stats
    abs_l2 = np.abs(y - pred_l2)
    abs_base = np.abs(y - base)
    diagnosis = {
        "n_test": int(len(y)),
        "y_median": float(np.median(y)),
        "y_p90": float(np.percentile(y, 90)),
        "y_p99": float(np.percentile(y, 99)),
        "baseline_pred_median": float(np.median(base)),
        "l2_pred_median": float(np.median(pred_l2)),
        "q50_log_pred_median": float(np.median(pred_q)),
        "share_y_gt_168h": float(np.mean(y > 168)),
        "l2_medae_on_y_le_168": float(np.median(abs_l2[y <= 168])) if np.any(y <= 168) else None,
        "baseline_medae_on_y_le_168": float(np.median(abs_base[y <= 168])) if np.any(y <= 168) else None,
        "note": (
            "L2 on log1p optimizes mean squared error in log space, which after expm1 "
            "targets something closer to a conditional mean on a skewed scale; the "
            "per-repo median baseline directly optimizes absolute error at the median. "
            "Heavy right tail (long-lived PRs) inflates squared/log losses toward larger predictions."
        ),
    }

    beaters = [r for r in rows if r["variant"] != "baseline_per_repo_median" and r["beats_baseline_medae"]]
    recommendation = {
        "any_beats_baseline_medae": bool(beaters),
        "best_by_medae": min(rows, key=lambda r: r["medae_hours"])["variant"],
        "keep": (
            beaters[0]["variant"]
            if beaters
            else "none — keep reporting baseline as the MedAE winner; use L2/quantile models for MAE/intervals only"
        ),
    }

    out = {"diagnosis": diagnosis, "variants": rows, "recommendation": recommendation}
    (root / "reports" / "merge_medae_diagnosis.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    md = [
        "# Stage A merge MedAE diagnosis",
        "",
        diagnosis["note"],
        "",
        f"- test n=`{diagnosis['n_test']}`; y median=`{diagnosis['y_median']:.2f}`h; "
        f"P90=`{diagnosis['y_p90']:.2f}`h; P99=`{diagnosis['y_p99']:.2f}`h; "
        f"share >7d=`{diagnosis['share_y_gt_168h']:.3f}`",
        f"- L2 pred median=`{diagnosis['l2_pred_median']:.2f}`h vs baseline pred median=`{diagnosis['baseline_pred_median']:.2f}`h",
        f"- MedAE on y≤7d only: L2=`{diagnosis['l2_medae_on_y_le_168']}` vs baseline=`{diagnosis['baseline_medae_on_y_le_168']}`",
        "",
        "| Variant | MAE (h) | MedAE (h) | MAE(log1p h) | Beats baseline MedAE? |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for r in rows:
        md.append(
            f"| `{r['variant']}` | {r['mae_hours']:.2f} | {r['medae_hours']:.2f} | "
            f"{r['mae_log1p']:.3f} | {'yes' if r['beats_baseline_medae'] else 'no'} |"
        )
    bucket = next(r for r in rows if r["variant"].startswith("bucket_"))
    md += [
        "",
        "### Bucket task (<1d / 1–7d / >7d)",
        f"- model accuracy=`{bucket['bucket_accuracy']:.3f}` macro-F1=`{bucket['bucket_macro_f1']:.3f}`",
        f"- baseline (bucket of per-repo median hours) accuracy=`{bucket['baseline_bucket_accuracy']:.3f}` "
        f"macro-F1=`{bucket['baseline_bucket_macro_f1']:.3f}`",
        "",
        f"**Recommendation:** {recommendation['keep']}",
        "",
    ]
    (root / "reports" / "merge_medae_diagnosis.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(recommendation, indent=2))


if __name__ == "__main__":
    main()
