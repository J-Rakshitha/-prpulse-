"""Phase 11 delivery-risk extension: revert labels, deploy-risk score, metrics lines."""

from __future__ import annotations

import re
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from src.features.build import feature_columns_for_stage
from src.models.train import _impute, time_split

REVERT_TITLE = re.compile(r"^\s*Revert\b", re.I)


def label_reverts(prs: pd.DataFrame, *, window_days: int = 30) -> pd.DataFrame:
    """Label merged PRs reverted within window_days; require enough post-merge history."""
    merged = prs[(prs["state"] == "merged") & (prs["is_bot"] == 0) & prs["merged_at"].notna()].copy()
    merged["merged_at_ts"] = pd.to_datetime(merged["merged_at"], utc=True)
    now = pd.Timestamp.now(tz="UTC")
    merged["has_history"] = (now - merged["merged_at_ts"]).dt.days >= window_days

    reverts = merged[merged["title"].fillna("").str.contains(REVERT_TITLE)].copy() if "title" in merged.columns else merged.iloc[0:0]
    # titles may not be in feature parquet — load from pattern on number mentions in later code path
    return merged


def attach_revert_target(stage_df: pd.DataFrame, prs_raw: pd.DataFrame, window_days: int = 30) -> pd.DataFrame:
    raw = prs_raw.copy()
    raw["merged_at_ts"] = pd.to_datetime(raw["merged_at"], utc=True)
    raw["created_at_ts"] = pd.to_datetime(raw["created_at"], utc=True)
    now = pd.Timestamp.now(tz="UTC")
    revert_prs = raw[raw["title"].fillna("").str.match(REVERT_TITLE)]
    # Map reverted PR numbers mentioned in title like 'Revert "something (#1234)"' or Revert #1234
    num_re = re.compile(r"#(\d+)")
    reverted_numbers: dict[str, set[int]] = {}
    for _, row in revert_prs.iterrows():
        repo = row["repo_full_name"]
        for m in num_re.finditer(row["title"] or ""):
            reverted_numbers.setdefault(repo, set()).add(int(m.group(1)))

    out = stage_df.copy()
    labels = []
    for _, row in out.iterrows():
        if row.get("state") != "merged" or int(row.get("is_bot", 0)) == 1:
            labels.append(np.nan)
            continue
        merged_at = pd.to_datetime(row["created_at"], utc=True)  # placeholder replaced below
        # use merge_hours + created_at
        if pd.isna(row.get("merge_hours")):
            labels.append(np.nan)
            continue
        merged_at = pd.to_datetime(row["created_at"], utc=True) + pd.Timedelta(hours=float(row["merge_hours"]))
        if (now - merged_at).days < window_days:
            labels.append(np.nan)
            continue
        repo = row["repo_full_name"]
        num = int(row["number"])
        # also check later revert PRs within window by number
        hit = num in reverted_numbers.get(repo, set())
        if not hit:
            # time-windowed revert titles that reference this number
            cand = revert_prs[
                (revert_prs["repo_full_name"] == repo)
                & (pd.to_datetime(revert_prs["created_at"], utc=True) >= merged_at)
                & (pd.to_datetime(revert_prs["created_at"], utc=True) <= merged_at + pd.Timedelta(days=window_days))
            ]
            hit = any(num_re.search(t or "") and num in [int(x) for x in num_re.findall(t or "")] for t in cand["title"].fillna(""))
        labels.append(1.0 if hit else 0.0)
    out["reverted_within_30d"] = labels
    return out


def train_revert_model(df: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    cols = feature_columns_for_stage("A")
    data = df.dropna(subset=["reverted_within_30d"])
    base_rate = float(data["reverted_within_30d"].mean()) if len(data) else float("nan")
    if len(data) < 200 or data["reverted_within_30d"].sum() < 5:
        return {
            "metrics": {
                "n": int(len(data)),
                "base_rate": base_rate,
                "model_pr_auc": float("nan"),
                "model_roc_auc": float("nan"),
                "note": "too few positives or rows for a reliable revert model",
            }
        }
    train, val, test = time_split(data, cfg)
    X_train, X_val = _impute(train, val, cols)
    _, X_test = _impute(train, test, cols)
    y_train = train["reverted_within_30d"].astype(int).to_numpy()
    y_test = test["reverted_within_30d"].astype(int).to_numpy()
    clf = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.05, num_leaves=23, random_state=42, verbosity=-1)
    clf.fit(X_train, y_train)
    proba = clf.predict_proba(X_test)[:, 1]
    pr = float(average_precision_score(y_test, proba)) if len(np.unique(y_test)) > 1 else float("nan")
    roc = float(roc_auc_score(y_test, proba)) if len(np.unique(y_test)) > 1 else float("nan")
    return {
        "metrics": {
            "n_train": int(len(train)),
            "n_test": int(len(test)),
            "base_rate": float(train["reverted_within_30d"].mean()),
            "positives_test": int(y_test.sum()),
            "model_pr_auc": pr,
            "model_roc_auc": roc,
        },
        "model": clf,
        "feature_columns": cols,
        "impute_median": X_train.median().to_dict(),
    }


def deploy_risk_score(
    p_delay: float,
    p_ci_fail: float,
    p_revert: float,
) -> tuple[str, list[str]]:
    """Transparent Low/Medium/High rule (not a black box)."""
    reasons = []
    score = 0.0
    if p_delay >= 0.55:
        score += 2
        reasons.append("elevated merge-delay probability")
    elif p_delay >= 0.35:
        score += 1
        reasons.append("moderate merge-delay probability")
    if p_ci_fail >= 0.35:
        score += 2
        reasons.append("elevated CI-fail probability")
    elif p_ci_fail >= 0.2:
        score += 1
        reasons.append("moderate CI-fail probability")
    if p_revert >= 0.05:
        score += 2
        reasons.append("elevated revert probability")
    elif p_revert >= 0.02:
        score += 1
        reasons.append("moderate revert probability")
    if score >= 4:
        level = "High"
    elif score >= 2:
        level = "Medium"
    else:
        level = "Low"
    return level, reasons


def validate_deploy_risk_on_test(
    test: pd.DataFrame,
    delay_proba: np.ndarray,
    ci_proba: np.ndarray,
    revert_proba: np.ndarray,
) -> list[str]:
    levels = []
    for i in range(len(test)):
        level, _ = deploy_risk_score(float(delay_proba[i]), float(ci_proba[i]), float(revert_proba[i]))
        levels.append(level)
    frame = test.copy()
    frame["risk"] = levels
    frame["ci_fail"] = frame.get("ci_failed_first_run")
    frame["reverted"] = frame.get("reverted_within_30d")
    lines = [
        "| Risk | n | CI fail rate | Revert rate |",
        "| --- | ---: | ---: | ---: |",
    ]
    for level in ("Low", "Medium", "High"):
        sub = frame[frame["risk"] == level]
        n = len(sub)
        if n == 0:
            lines.append(f"| {level} | 0 | n/a | n/a |")
            continue
        ci_rate = float(sub["ci_fail"].dropna().mean()) if sub["ci_fail"].notna().any() else float("nan")
        rev_rate = float(sub["reverted"].dropna().mean()) if sub["reverted"].notna().any() else float("nan")
        lines.append(f"| {level} | {n} | {ci_rate:.3f} | {rev_rate:.3f} |")
    lines.append("")
    lines.append("Time-to-deploy: **skipped** — no reliable release/deploy workflow linkage in the collected Actions data for these two repos.")
    return lines
