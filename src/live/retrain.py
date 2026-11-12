"""Weekly retrain job for the merge-hours model.

    python -m src.live.retrain                 # dry run (default): train, compare, report, change nothing live
    python -m src.live.retrain --promote       # also replace the live bundle when the candidate wins

The newest ``test_fraction`` of merged non-bot PRs (by created_at) is the
holdout. The candidate trains on everything older. The incumbent bundle is
scored on the same holdout. The candidate is promoted only if its MAE is
strictly lower and the holdout has at least ``MIN_HOLDOUT`` PRs.

If the incumbent was trained on data that overlaps the holdout, the comparison
favours the incumbent. That makes promotion conservative, never optimistic.

This job retrains the merge_hours bundle for one stage only. Classifier
bundles are not touched.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from src.live import drift
from src.models.registry import (
    bundle_path,
    default_models_dir,
    local_version,
    read_registry,
    write_registry,
)
from src.settings import load_settings, project_root

logger = logging.getLogger("prpulse.retrain")

MIN_HOLDOUT = 30


def newest_holdout(df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split merged non-bot PRs into (older, newest holdout) by created_at."""
    data = df[(df["is_bot"] == 0) & (df["state"] == "merged")].dropna(subset=["merge_hours"])
    data = data[data["merge_hours"] >= 0].sort_values("created_at").reset_index(drop=True)
    n_hold = int(len(data) * float(cfg["split"]["test_fraction"]))
    if n_hold == 0:
        return data, data.iloc[0:0]
    return data.iloc[:-n_hold], data.iloc[-n_hold:]


def evaluate_bundle(bundle: dict[str, Any], holdout: pd.DataFrame) -> dict[str, Any]:
    cols = bundle["feature_columns"]
    medians = bundle.get("impute_median", {})
    X = holdout[cols].fillna(pd.Series(medians))
    X = X.fillna(0.0)
    y = holdout["merge_hours"].to_numpy(dtype=float)
    pred = np.maximum(np.expm1(bundle["regressor"].predict(X)), 0.0)
    q = bundle["quantiles"]
    p50 = np.maximum(np.expm1(q[0.5].predict(X)), 0.0)
    p10 = np.minimum(np.maximum(np.expm1(q[0.1].predict(X)), 0.0), p50)
    p90 = np.maximum(np.expm1(q[0.9].predict(X)), p50)
    return {
        "n": int(len(y)),
        "mae_hours": float(np.mean(np.abs(y - pred))),
        "median_ae_hours": float(np.median(np.abs(y - pred))),
        "interval_coverage_10_90": float(np.mean((y >= p10) & (y <= p90))),
    }


def decide(candidate: dict[str, Any], incumbent: dict[str, Any] | None, n_holdout: int) -> tuple[bool, str]:
    if n_holdout < MIN_HOLDOUT:
        return False, f"holdout has {n_holdout} PRs, fewer than the required {MIN_HOLDOUT}"
    if incumbent is None:
        return True, "no incumbent bundle to compare against"
    if candidate["mae_hours"] < incumbent["mae_hours"]:
        return True, "candidate MAE is lower than incumbent MAE on the newest holdout"
    return False, "candidate MAE is not lower than incumbent MAE on the newest holdout"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def run(
    stage: str = "A",
    promote: bool = False,
    root: Path | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from src.models.train import train_merge_models

    root = root or project_root()
    cfg = cfg if cfg is not None else load_settings()
    models_dir = default_models_dir(root)
    registry_file = root / "artifacts" / "registry.json"
    parquet = root / "data" / "features" / f"stage_{stage}.parquet"
    if not parquet.is_file():
        raise SystemExit(f"Missing {parquet}; run the features step first.")

    df = pd.read_parquet(parquet)
    older, holdout = newest_holdout(df, cfg)
    version = f"v{_utc_stamp()}-{stage}"

    incumbent_file = bundle_path(models_dir, "merge_hours", stage)
    incumbent_bundle = joblib.load(incumbent_file) if incumbent_file.is_file() else None
    incumbent_metrics = evaluate_bundle(incumbent_bundle, holdout) if incumbent_bundle and len(holdout) else None

    result: dict[str, Any] = {
        "version": version,
        "stage": stage,
        "n_train_pool": int(len(older)),
        "n_holdout": int(len(holdout)),
        "holdout_start": str(holdout["created_at"].iloc[0]) if len(holdout) else None,
        "incumbent": incumbent_metrics,
        "candidate": None,
        "promoted": False,
        "dry_run": not promote,
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        trained = train_merge_models(stage, older, cfg, tmp_dir)
        candidate_file = Path(trained["bundle_path"])
        candidate_bundle = joblib.load(candidate_file)
        candidate_metrics = evaluate_bundle(candidate_bundle, holdout) if len(holdout) else None
        result["candidate"] = candidate_metrics
        if candidate_metrics is None:
            should, reason = False, "no holdout rows"
        else:
            should, reason = decide(candidate_metrics, incumbent_metrics, len(holdout))
        result["decision_reason"] = reason
        result["would_promote"] = should

        registry = read_registry(registry_file)
        if registry["active"] is None and incumbent_bundle is not None:
            boot = local_version(models_dir)
            registry["active"] = boot
            registry["versions"].setdefault(boot, {"bootstrap": True, "stage": stage, "metrics_on_holdout": incumbent_metrics})
        registry["versions"][version] = {
            "stage": stage,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "metrics_on_holdout": candidate_metrics,
            "incumbent_metrics_on_holdout": incumbent_metrics,
            "n_holdout": int(len(holdout)),
            "decision": "promoted" if (should and promote) else ("would_promote" if should else "rejected"),
            "reason": reason,
            "dry_run": not promote,
        }
        if should and promote:
            models_dir.mkdir(parents=True, exist_ok=True)
            if incumbent_file.is_file():
                archive = root / "artifacts" / "archive" / (registry["active"] or "previous")
                archive.mkdir(parents=True, exist_ok=True)
                shutil.copy2(incumbent_file, archive / incumbent_file.name)
            shutil.copy2(candidate_file, incumbent_file)
            registry["active"] = version
            result["promoted"] = True
        registry["history"].append(
            {"version": version, "at": registry["versions"][version]["created_at"], "decision": registry["versions"][version]["decision"]}
        )
        write_registry(registry, registry_file)

    result["report_lines"] = _report_lines(result)
    drift.run(stage, root=root, cfg=cfg, retrain=result)
    return result


def _fmt(metrics: dict[str, Any] | None) -> str:
    if not metrics:
        return "not available"
    return f"MAE {metrics['mae_hours']:.2f} h, median AE {metrics['median_ae_hours']:.2f} h, 10-90 coverage {metrics['interval_coverage_10_90']:.3f} (n={metrics['n']})"


def _report_lines(result: dict[str, Any]) -> list[str]:
    mode = "dry run" if result["dry_run"] else "promotion enabled"
    return [
        f"- Mode: {mode}. Candidate version `{result['version']}`.",
        f"- Holdout: newest `{result['n_holdout']}` merged PRs (from `{result['holdout_start']}`); candidate trained on the older `{result['n_train_pool']}`.",
        f"- Incumbent on holdout: {_fmt(result['incumbent'])}",
        f"- Candidate on holdout: {_fmt(result['candidate'])}",
        f"- Decision: {'promote' if result['would_promote'] else 'keep incumbent'} ({result['decision_reason']}).",
        f"- Promoted this run: {'yes' if result['promoted'] else 'no'}.",
    ]


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Weekly retrain (dry run unless --promote)")
    parser.add_argument("--stage", default="A", choices=["A", "B", "C"])
    parser.add_argument("--dry-run", action="store_true", help="default behaviour; accepted for clarity")
    parser.add_argument("--promote", action="store_true", help="replace the live bundle if the candidate wins")
    args = parser.parse_args(argv)
    if args.dry_run and args.promote:
        parser.error("--dry-run and --promote are mutually exclusive")
    result = run(args.stage, promote=args.promote)
    print("\n".join(result["report_lines"]))
    print(f"Wrote {project_root() / 'artifacts' / 'registry.json'} and {project_root() / 'reports' / 'drift.md'}")


if __name__ == "__main__":
    main()
