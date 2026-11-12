"""Stage A leakage ablation on the same time split.

Reports PR-AUC (merged_within_7d) and MAE / MedAE (merge_hours) for:
1. all features
2. no collection-time PR/file-list columns
3. no author-history
4. no repo-state
5. only author + repo-state
"""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, mean_absolute_error

from src.features.build import FEATURE_COLUMNS_A
from src.models.train import _impute, time_split
from src.settings import load_settings, project_root

# Collection-time approximations from the PR file list / stored draft flag.
# (Live additions/deletions/commits/title are already blocked; these are the
# remaining columns that can still reflect later pushes.)
COLLECTION_TIME = [
    "draft",
    "n_files_approx",
    "additions_approx",
    "deletions_approx",
    "log1p_additions_approx",
    "log1p_deletions_approx",
    "log1p_n_files_approx",
    "touches_tests",
    "touches_docs",
    "touches_ci_config",
    "touches_deps",
    "touches_auth",
    "touches_migration",
    "docs_only",
    "file_hist_fail_mean",
    "file_hist_fail_max",
]

AUTHOR = [
    "prior_opened_count",
    "prior_merged_count",
    "prior_merge_rate",
    "first_time_contributor",
    "median_prior_merge_hours",
    "prior_ci_fail_rate",
]

REPO_STATE = [
    "open_pr_count_at_t0",
    "median_merge_hours_30d",
    "reviewer_activity_14d",
    "repo_is_pandas",
]


def _fit_reg(train, val, test, cols: list[str]) -> dict[str, float]:
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
    reg.fit(
        X_train,
        y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(40, verbose=False)],
    )
    pred = np.expm1(reg.predict(X_test))
    return {
        "mae": float(mean_absolute_error(y_test, pred)),
        "medae": float(np.median(np.abs(y_test - pred))),
        "n_test": int(len(test)),
        "n_features": len(cols),
    }


def _fit_clf(train, val, test, cols: list[str]) -> dict[str, float]:
    X_train, X_val = _impute(train, val, cols)
    _, X_test = _impute(train, test, cols)
    y_train = train["merged_within_7d"].astype(int).to_numpy()
    y_val = val["merged_within_7d"].astype(int).to_numpy()
    y_test = test["merged_within_7d"].astype(int).to_numpy()
    clf = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbosity=-1,
    )
    clf.fit(
        X_train,
        y_train,
        eval_set=[(X_val, y_val)],
        callbacks=[lgb.early_stopping(40, verbose=False)],
    )
    try:
        from sklearn.frozen import FrozenEstimator
        from sklearn.calibration import CalibratedClassifierCV

        cal = CalibratedClassifierCV(FrozenEstimator(clf), method="isotonic")
        cal.fit(X_val, y_val)
        proba = cal.predict_proba(X_test)[:, 1]
    except Exception:
        proba = clf.predict_proba(X_test)[:, 1]
    return {
        "pr_auc": float(average_precision_score(y_test, proba)),
        "n_test": int(len(test)),
        "n_features": len(cols),
        "base_rate_test": float(y_test.mean()),
    }


def main() -> None:
    cfg = load_settings()
    root = project_root()
    df = pd.read_parquet(root / "data" / "features" / "stage_A.parquet")
    df = df[df["is_bot"] == 0].copy()

    all_cols = [c for c in FEATURE_COLUMNS_A if c in df.columns]
    variants = {
        "1_all": all_cols,
        "2_no_collection_time": [c for c in all_cols if c not in COLLECTION_TIME],
        "3_no_author": [c for c in all_cols if c not in AUTHOR],
        "4_no_repo_state": [c for c in all_cols if c not in REPO_STATE],
        "5_author_repo_only": [c for c in all_cols if c in set(AUTHOR) | set(REPO_STATE)],
    }

    merged = df[df["state"] == "merged"].dropna(subset=["merge_hours"])
    merged = merged[merged["merge_hours"] >= 0]
    m_train, m_val, m_test = time_split(merged, cfg)

    cls = df.dropna(subset=["merged_within_7d"])
    c_train, c_val, c_test = time_split(cls, cfg)

    # Baselines on same splits
    repo_med = m_train.groupby("repo_full_name")["merge_hours"].median().to_dict()
    g_med = float(m_train["merge_hours"].median())
    base_pred = m_test["repo_full_name"].map(repo_med).fillna(g_med).to_numpy(dtype=float)
    y_m = m_test["merge_hours"].to_numpy()
    baseline_reg = {
        "mae": float(mean_absolute_error(y_m, base_pred)),
        "medae": float(np.median(np.abs(y_m - base_pred))),
    }
    repo_rate = c_train.groupby("repo_full_name")["merged_within_7d"].mean().to_dict()
    g_rate = float(c_train["merged_within_7d"].mean())
    base_p = c_test["repo_full_name"].map(repo_rate).fillna(g_rate).to_numpy(dtype=float)
    baseline_clf = {
        "pr_auc": float(average_precision_score(c_test["merged_within_7d"].astype(int), base_p)),
    }

    rows = []
    for name, cols in variants.items():
        print(f"=== {name} ({len(cols)} features) ===")
        print(" cols:", ", ".join(cols))
        reg = _fit_reg(m_train, m_val, m_test, cols)
        clf = _fit_clf(c_train, c_val, c_test, cols)
        row = {"variant": name, "features": cols, **{f"reg_{k}": v for k, v in reg.items()}, **{f"clf_{k}": v for k, v in clf.items()}}
        rows.append(row)
        print(
            f"  merge MAE={reg['mae']:.2f} MedAE={reg['medae']:.2f} | "
            f"merged_7d PR-AUC={clf['pr_auc']:.3f}"
        )

    out = {
        "baseline_merge": baseline_reg,
        "baseline_merged_7d": baseline_clf,
        "variants": rows,
        "collection_time_columns": COLLECTION_TIME,
        "author_columns": AUTHOR,
        "repo_state_columns": REPO_STATE,
    }
    path = root / "reports" / "stage_a_leakage_ablation.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("wrote", path)

    # Markdown table
    md = [
        "# Stage A leakage ablation",
        "",
        "Same time split as training. Collection-time columns = draft + `pr_files` approx size/paths/module history.",
        "`commits_at_open` is kept in variant 2 (commits with `committed_at <= created_at`).",
        "",
        f"Merge baseline MAE=`{baseline_reg['mae']:.2f}` MedAE=`{baseline_reg['medae']:.2f}`. "
        f"Classifier baseline PR-AUC=`{baseline_clf['pr_auc']:.3f}`.",
        "",
        "| Variant | n_feat | Merge MAE | Merge MedAE | merged_7d PR-AUC |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        md.append(
            f"| `{r['variant']}` | {r['reg_n_features']} | {r['reg_mae']:.2f} | "
            f"{r['reg_medae']:.2f} | {r['clf_pr_auc']:.3f} |"
        )
    (root / "reports" / "stage_a_leakage_ablation.md").write_text("\n".join(md) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
