"""Population stability index (PSI) drift check.

Reference = the oldest ``train_fraction`` of PRs (by created_at), the same
period the model trains on. Current = the newest ``test_fraction``.

Rule-of-thumb reading of PSI (a common convention, not a measured threshold):
below 0.1 stable, 0.1 to 0.25 moderate shift, above 0.25 large shift.

    python -m src.live.drift [--stage A]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.features.build import feature_columns_for_stage
from src.settings import load_settings, project_root

EPS = 1e-4
MODERATE = 0.1
LARGE = 0.25
MAX_BINS = 10


def _bucket_shares(reference: pd.Series, current: pd.Series, bins: int) -> tuple[np.ndarray, np.ndarray]:
    """Shares per bucket for both samples. NaN is its own bucket."""
    ref_nan = reference.isna().mean()
    cur_nan = current.isna().mean()
    ref = reference.dropna().astype(float)
    cur = current.dropna().astype(float)
    if ref.empty or cur.empty:
        return np.array([1.0]), np.array([1.0])
    if ref.nunique() <= bins:
        levels = np.sort(ref.unique())
        ref_counts = np.array([(ref == v).sum() for v in levels], dtype=float)
        cur_counts = np.array([(cur == v).sum() for v in levels], dtype=float)
        cur_other = float(len(cur) - cur_counts.sum())  # values never seen in reference
        ref_counts = np.append(ref_counts, 0.0)
        cur_counts = np.append(cur_counts, cur_other)
    else:
        edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
        edges[0], edges[-1] = -np.inf, np.inf
        ref_counts = np.histogram(ref, bins=edges)[0].astype(float)
        cur_counts = np.histogram(cur, bins=edges)[0].astype(float)
    ref_share = ref_counts / max(ref_counts.sum(), 1.0) * (1 - ref_nan)
    cur_share = cur_counts / max(cur_counts.sum(), 1.0) * (1 - cur_nan)
    return np.append(ref_share, ref_nan), np.append(cur_share, cur_nan)


def psi(reference: pd.Series, current: pd.Series, bins: int = MAX_BINS) -> float:
    """PSI between two samples of one feature. 0 means identical distributions."""
    p, q = _bucket_shares(reference, current, bins)
    p = np.clip(p, EPS, None)
    q = np.clip(q, EPS, None)
    return float(np.sum((q - p) * np.log(q / p)))


def status_for(value: float) -> str:
    if value >= LARGE:
        return "large"
    if value >= MODERATE:
        return "moderate"
    return "stable"


def compute_drift(reference: pd.DataFrame, current: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    rows = []
    for col in columns:
        if col not in reference.columns or col not in current.columns:
            continue
        value = psi(reference[col], current[col])
        rows.append({"feature": col, "psi": value, "status": status_for(value)})
    rows.sort(key=lambda r: r["psi"], reverse=True)
    return rows


def split_reference_current(df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = df[df["is_bot"] == 0].sort_values("created_at").reset_index(drop=True) if "is_bot" in df.columns else df.sort_values("created_at")
    n = len(data)
    n_ref = int(n * float(cfg["split"]["train_fraction"]))
    n_cur = int(n * float(cfg["split"]["test_fraction"]))
    return data.iloc[:n_ref], data.iloc[n - n_cur :] if n_cur else data.iloc[0:0]


def render_report(
    stage: str,
    rows: list[dict[str, Any]],
    n_ref: int,
    n_cur: int,
    retrain: dict[str, Any] | None = None,
    note: str | None = None,
) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = ["# Drift report", "", f"Generated at: `{generated}` (UTC). Stage `{stage}`.", ""]
    if note:
        lines += [note, ""]
    else:
        lines += [
            f"Reference PRs: `{n_ref}` (oldest, training period). Current PRs: `{n_cur}` (newest, holdout period).",
            "",
            "PSI reading (convention): below 0.1 stable, 0.1 to 0.25 moderate, above 0.25 large.",
            "",
            "| Feature | PSI | Status |",
            "| --- | ---: | --- |",
        ]
        lines += [f"| `{r['feature']}` | {r['psi']:.4f} | {r['status']} |" for r in rows]
        flagged = [r["feature"] for r in rows if r["status"] == "large"]
        lines += ["", "Features with large shift: " + (", ".join(f"`{f}`" for f in flagged) if flagged else "none"), ""]
    if retrain:
        lines += ["## Retrain decision", ""]
        lines += retrain.get("report_lines", [])
        lines.append("")
    return "\n".join(lines) + "\n"


def write_report(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def run(stage: str = "A", root: Path | None = None, cfg: dict[str, Any] | None = None, retrain: dict[str, Any] | None = None) -> Path:
    root = root or project_root()
    cfg = cfg if cfg is not None else load_settings()
    report_path = root / "reports" / "drift.md"
    parquet = root / "data" / "features" / f"stage_{stage}.parquet"
    if not parquet.is_file():
        return write_report(report_path, render_report(stage, [], 0, 0, retrain, note=f"No feature matrix at `{parquet.name}`; run the features step first. No drift was computed."))
    df = pd.read_parquet(parquet)
    ref, cur = split_reference_current(df, cfg)
    if len(ref) == 0 or len(cur) == 0:
        return write_report(report_path, render_report(stage, [], len(ref), len(cur), retrain, note="Not enough rows to compare two periods."))
    rows = compute_drift(ref, cur, feature_columns_for_stage(stage))
    return write_report(report_path, render_report(stage, rows, len(ref), len(cur), retrain))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="PSI drift report")
    parser.add_argument("--stage", default="A", choices=["A", "B", "C"])
    args = parser.parse_args(argv)
    print(f"Wrote {run(args.stage)}")


if __name__ == "__main__":
    main()
