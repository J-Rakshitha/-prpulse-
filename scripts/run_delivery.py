"""Phase 11 delivery-risk metrics appended into reports/results.md."""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from src.data.db import connect, resolve_db_path
from src.features.build import feature_columns_for_stage
from src.models.delivery import attach_revert_target, train_revert_model, validate_deploy_risk_on_test
from src.models.train import _impute, time_split, write_results
from src.settings import load_settings, project_root


def main() -> None:
    cfg = load_settings()
    root = project_root()
    df = pd.read_parquet(root / "data" / "features" / "stage_A.parquet")
    with connect(resolve_db_path(cfg)) as conn:
        raw = pd.read_sql_query(
            """
            SELECT p.id AS pr_id, p.number, p.title, p.merged_at, p.created_at,
                   p.state, p.is_bot, r.full_name AS repo_full_name
            FROM prs p
            JOIN repos r ON r.id = p.repo_id
            WHERE r.full_name IN ('pandas-dev/pandas', 'scikit-learn/scikit-learn')
            """,
            conn,
        )
    labeled = attach_revert_target(df, raw, window_days=int(cfg["thresholds"]["revert_window_days"]))
    rev = train_revert_model(labeled, cfg)
    print("revert", rev["metrics"])

    cols = feature_columns_for_stage("A")
    data = labeled[labeled["is_bot"] == 0].dropna(subset=["merged_within_7d"])
    train, _, test = time_split(data, cfg)
    m7 = joblib.load(root / "artifacts" / "models" / "merged_within_7d_stage_A.joblib")
    ci_bundle = joblib.load(root / "artifacts" / "models" / "ci_failed_first_run_stage_A.joblib")
    _, X_test = _impute(train, test, m7["feature_columns"])
    p_merge = m7["classifier"].predict_proba(X_test)[:, 1]
    p_delay = 1.0 - p_merge
    p_ci = np.full(len(test), float(ci_bundle["global_rate"]))
    mask = test["ci_label_available"] == 1
    if mask.any():
        Xc = test.loc[mask, ci_bundle["feature_columns"]].fillna(pd.Series(ci_bundle["impute_median"]))
        p_ci[mask.to_numpy()] = ci_bundle["classifier"].predict_proba(Xc)[:, 1]
    if "model" in rev:
        Xr = test[rev["feature_columns"]].fillna(pd.Series(rev["impute_median"]))
        p_rev = rev["model"].predict_proba(Xr)[:, 1]
    else:
        p_rev = np.full(len(test), float(rev["metrics"].get("base_rate") or 0.0))

    lines = validate_deploy_risk_on_test(test, p_delay, p_ci, p_rev)
    results = json.loads((root / "reports" / "results.json").read_text(encoding="utf-8"))
    n_lab = int(labeled["reverted_within_30d"].notna().sum())
    n_pos = int(labeled["reverted_within_30d"].fillna(0).sum())
    results["delivery"] = {
        "revert": rev["metrics"],
        "lines": [
            "### Revert risk (30d, title `Revert` + `#N` detection)",
            (
                f"- labeled rows: `{n_lab}`; positives: `{n_pos}`; "
                f"base rate: `{rev['metrics'].get('base_rate', float('nan'))}`"
            ),
            (
                f"- test PR-AUC: `{rev['metrics'].get('model_pr_auc', float('nan'))}`; "
                f"ROC-AUC: `{rev['metrics'].get('model_roc_auc', float('nan'))}`"
            ),
            "",
            "### Deploy Risk Score validation (test period)",
            *lines,
            "",
            "### Notes",
            "- File-history ablation for merge MAE is in the Stage A ablations table (`no_file_hist`).",
            "- CI-failure class is extremely rare in the labeled window; PR-AUC near zero is expected.",
        ],
    }
    write_results(results, root / "reports" / "results.md")
    print("updated results.md")


if __name__ == "__main__":
    main()
