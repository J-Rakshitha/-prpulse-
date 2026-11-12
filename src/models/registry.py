"""Model bundle loading, prediction, and the version registry.

Bundles are the joblib dicts written by ``src/models/train.py``. The registry
file ``artifacts/registry.json`` records which version is active and the
evaluation history written by ``src/live/retrain.py``.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from src.settings import project_root

logger = logging.getLogger("prpulse.registry")

STAGES = ("A", "B", "C")

# task name -> bundle file stem pattern written by train.py
TASK_FILES = {
    "merge_hours": "merge_stage_{stage}",
    "merged_within_7d": "merged_within_7d_stage_{stage}",
    "ci_failed_first_run": "ci_failed_first_run_stage_{stage}",
    "ci_duration_minutes": "ci_duration_stage_{stage}",
}


def default_models_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "artifacts" / "models"


def registry_path(root: Path | None = None) -> Path:
    return (root or project_root()) / "artifacts" / "registry.json"


def bundle_path(models_dir: Path, task: str, stage: str) -> Path:
    return Path(models_dir) / f"{TASK_FILES[task].format(stage=stage)}.joblib"


def load_bundle(stage: str = "A", models_dir: Path | None = None, task: str = "merge_hours") -> dict[str, Any]:
    """Load one joblib bundle. Raises FileNotFoundError when it is missing."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}, got {stage!r}")
    path = bundle_path(models_dir or default_models_dir(), task, stage)
    if not path.is_file():
        raise FileNotFoundError(f"Model bundle not found: {path}")
    return joblib.load(path)


# --------------------------------------------------------------------------- features


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def fill_derived(features: dict[str, Any]) -> dict[str, Any]:
    """Add log1p columns when the raw size column is present and the log column is not."""
    out = dict(features)
    for raw, logged in (
        ("additions_approx", "log1p_additions_approx"),
        ("deletions_approx", "log1p_deletions_approx"),
        ("n_files_approx", "log1p_n_files_approx"),
    ):
        if not _is_missing(out.get(raw)) and _is_missing(out.get(logged)):
            out[logged] = math.log1p(max(float(out[raw]), 0.0))
    return out


def features_to_frame(pr_features: dict[str, Any] | pd.DataFrame | pd.Series, bundle: dict[str, Any]) -> tuple[pd.DataFrame, list[str]]:
    """Return a one-row frame in model column order plus the names that were imputed.

    Missing or NaN values are filled with the training medians stored in the bundle.
    """
    if isinstance(pr_features, pd.DataFrame):
        if len(pr_features) != 1:
            raise ValueError("Pass exactly one PR row.")
        row = pr_features.iloc[0].to_dict()
    elif isinstance(pr_features, pd.Series):
        row = pr_features.to_dict()
    else:
        row = dict(pr_features)
    row = fill_derived(row)
    cols = list(bundle["feature_columns"])
    medians = bundle.get("impute_median", {})
    values: dict[str, float] = {}
    imputed: list[str] = []
    for col in cols:
        value = row.get(col)
        if _is_missing(value):
            fallback = medians.get(col, 0.0)
            values[col] = float(fallback) if not _is_missing(fallback) else 0.0
            imputed.append(col)
        else:
            values[col] = float(value)
    return pd.DataFrame([values], columns=cols), imputed


def predict_merge_quantiles(bundle: dict[str, Any], frame: pd.DataFrame) -> dict[str, float]:
    """p10/p50/p90 merge hours (ordered, non-negative) from a merge bundle."""
    q = bundle["quantiles"]
    p10 = float(np.expm1(q[0.1].predict(frame))[0])
    p50 = float(np.expm1(q[0.5].predict(frame))[0])
    p90 = float(np.expm1(q[0.9].predict(frame))[0])
    p50 = max(p50, 0.0)
    p10 = max(min(p10, p50), 0.0)
    p90 = max(p90, p50)
    return {"p10": p10, "p50": p50, "p90": p90}


def predict_point_hours(bundle: dict[str, Any], frame: pd.DataFrame) -> float:
    """Point estimate (hours) from the mean regressor in a merge bundle."""
    return max(float(np.expm1(bundle["regressor"].predict(frame))[0]), 0.0)


def predict_proba(bundle: dict[str, Any], frame: pd.DataFrame) -> float:
    p = float(bundle["classifier"].predict_proba(frame)[0, 1])
    return min(max(p, 0.0), 1.0)


class ModelStore:
    """All bundles found in a directory, loaded once."""

    def __init__(self, models_dir: Path, bundles: dict[tuple[str, str], dict[str, Any]], paths: dict[tuple[str, str], Path]):
        self.models_dir = Path(models_dir)
        self.bundles = bundles
        self.paths = paths

    @classmethod
    def load(cls, models_dir: Path | None = None) -> "ModelStore":
        directory = Path(models_dir) if models_dir is not None else default_models_dir()
        bundles: dict[tuple[str, str], dict[str, Any]] = {}
        paths: dict[tuple[str, str], Path] = {}
        for task in TASK_FILES:
            for stage in STAGES:
                path = bundle_path(directory, task, stage)
                if not path.is_file():
                    continue
                try:
                    bundles[(task, stage)] = joblib.load(path)
                    paths[(task, stage)] = path
                except Exception:  # noqa: BLE001 - a corrupt file must not stop the service
                    logger.exception("Could not load %s", path)
        return cls(directory, bundles, paths)

    def has(self, task: str, stage: str) -> bool:
        return (task, stage) in self.bundles

    def get(self, task: str, stage: str) -> dict[str, Any]:
        return self.bundles[(task, stage)]

    def stages(self) -> list[str]:
        return sorted({stage for (task, stage) in self.bundles if task == "merge_hours"})

    @property
    def empty(self) -> bool:
        return not self.bundles

    def predict(self, features: dict[str, Any] | pd.DataFrame, stage: str) -> dict[str, Any]:
        """Run every model that exists for the stage. Absent models give None."""
        if not self.has("merge_hours", stage):
            raise KeyError(f"No merge_hours model loaded for stage {stage}")
        merge_bundle = self.get("merge_hours", stage)
        frame, imputed = features_to_frame(features, merge_bundle)
        result: dict[str, Any] = {"stage": stage, "missing_features": imputed}
        q = predict_merge_quantiles(merge_bundle, frame)
        result["merge_hours_p10"], result["merge_hours_p50"], result["merge_hours_p90"] = q["p10"], q["p50"], q["p90"]
        for task, key in (("merged_within_7d", "prob_merged_within_7d"), ("ci_failed_first_run", "prob_ci_fail")):
            if self.has(task, stage):
                b = self.get(task, stage)
                f, _ = features_to_frame(features, b)
                result[key] = predict_proba(b, f)
            else:
                result[key] = None
        return result

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for (task, stage), bundle in sorted(self.bundles.items()):
            path = self.paths[(task, stage)]
            out.append(
                {
                    "task": task,
                    "stage": stage,
                    "path": str(path),
                    "n_features": len(bundle.get("feature_columns", [])),
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )
        return out


# --------------------------------------------------------------------------- registry file


def read_registry(path: Path | None = None) -> dict[str, Any]:
    p = path or registry_path()
    if not p.is_file():
        return {"active": None, "versions": {}, "history": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.error("registry.json is not valid JSON: %s", p)
        return {"active": None, "versions": {}, "history": []}
    data.setdefault("active", None)
    data.setdefault("versions", {})
    data.setdefault("history", [])
    return data


def write_registry(data: dict[str, Any], path: Path | None = None) -> Path:
    p = path or registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)
    return p


def derive_version(models_dir: Path) -> str:
    """Version label from the registry when active, else from the newest bundle mtime."""
    reg = read_registry(Path(models_dir).parent / "registry.json")
    if reg.get("active"):
        return str(reg["active"])
    return local_version(models_dir)


def local_version(models_dir: Path) -> str:
    files = sorted(Path(models_dir).glob("*.joblib"), key=lambda p: p.stat().st_mtime) if Path(models_dir).is_dir() else []
    if not files:
        return "local-empty"
    stamp = datetime.fromtimestamp(files[-1].stat().st_mtime, tz=timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"local-{stamp}"


# --------------------------------------------------------------------------- promote / rollback


def versions_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "artifacts" / "versions"


def archive_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "artifacts" / "archive"


def _copy_joblibs(src: Path, dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for path in sorted(src.glob("*.joblib")):
        target = dest / path.name
        target.write_bytes(path.read_bytes())
        copied.append(path.name)
    return copied


def resolve_version_dir(version: str, root: Path | None = None) -> Path:
    """Locate a versioned bundle directory under versions/ or archive/."""
    root = root or project_root()
    candidates = [
        versions_dir(root) / version,
        archive_dir(root) / version,
    ]
    for path in candidates:
        if path.is_dir() and any(path.glob("*.joblib")):
            return path
    raise FileNotFoundError(f"No loadable bundles for version {version!r} under artifacts/versions or artifacts/archive")


def snapshot_active(models_dir: Path, version: str, root: Path | None = None) -> Path:
    """Copy the current live models into archive/<version> so rollback can restore them."""
    dest = archive_dir(root) / version
    _copy_joblibs(models_dir, dest)
    return dest


def promote_version(version: str, *, root: Path | None = None, models_dir: Path | None = None) -> dict[str, Any]:
    """Activate ``version``: archive the current live set, copy the version in, update registry.

    Keeps the previous active version loadable under ``artifacts/archive/<previous>`` and
    records ``previous`` in registry.json for ``rollback_version()``.
    """
    root = root or project_root()
    live = Path(models_dir) if models_dir is not None else default_models_dir(root)
    reg_path = Path(live).parent / "registry.json"
    registry = read_registry(reg_path)
    source = resolve_version_dir(version, root)
    previous = registry.get("active")
    if previous and any(live.glob("*.joblib")):
        snapshot_active(live, str(previous), root)
        registry["previous"] = previous
    copied = _copy_joblibs(source, live)
    if not copied:
        raise FileNotFoundError(f"Version {version!r} has no .joblib files")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    registry["active"] = version
    registry.setdefault("versions", {})
    registry["versions"].setdefault(version, {})
    registry["versions"][version].update({"activated_at": now, "source": str(source)})
    registry.setdefault("history", []).append({"version": version, "at": now, "decision": "promoted"})
    write_registry(registry, reg_path)
    return {"active": version, "previous": registry.get("previous"), "copied": copied, "registry": str(reg_path)}


def rollback_version(*, root: Path | None = None, models_dir: Path | None = None) -> dict[str, Any]:
    """Restore the previous active version recorded in the registry (or raise)."""
    root = root or project_root()
    live = Path(models_dir) if models_dir is not None else default_models_dir(root)
    reg_path = Path(live).parent / "registry.json"
    registry = read_registry(reg_path)
    previous = registry.get("previous")
    if not previous:
        raise RuntimeError("No previous version recorded in the registry; cannot rollback")
    current = registry.get("active")
    # Snapshot the current (bad) active before restoring previous.
    if current and any(live.glob("*.joblib")):
        snapshot_active(live, str(current), root)
    source = resolve_version_dir(str(previous), root)
    copied = _copy_joblibs(source, live)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    registry["previous"] = current
    registry["active"] = previous
    registry.setdefault("history", []).append({"version": previous, "at": now, "decision": "rollback"})
    write_registry(registry, reg_path)
    return {"active": previous, "previous": current, "copied": copied, "registry": str(reg_path)}


def list_versions(*, root: Path | None = None, models_dir: Path | None = None) -> dict[str, Any]:
    root = root or project_root()
    live = Path(models_dir) if models_dir is not None else default_models_dir(root)
    reg_path = Path(live).parent / "registry.json"
    registry = read_registry(reg_path)
    on_disk = sorted({p.name for p in versions_dir(root).glob("*") if p.is_dir()} | {p.name for p in archive_dir(root).glob("*") if p.is_dir()})
    return {
        "active": registry.get("active"),
        "previous": registry.get("previous"),
        "registered": sorted(registry.get("versions", {}).keys()),
        "on_disk": on_disk,
        "history": list(registry.get("history") or [])[-20:],
    }


def smoke_predict(models_dir: Path | None = None, stage: str = "A") -> dict[str, Any]:
    """Load bundles and run a trivial predict; raises if models cannot serve."""
    store = ModelStore.load(models_dir)
    if store.empty or not store.has("merge_hours", stage):
        raise RuntimeError(f"No merge_hours stage {stage} model in {models_dir}")
    features = {
        "draft": 0,
        "commits_at_open": 1,
        "n_files_approx": 1,
        "log1p_n_files_approx": 0.693,
        "log1p_additions_approx": 1.0,
        "log1p_deletions_approx": 0.0,
        "hour_utc": 12,
        "weekday": 2,
        "is_weekend": 0,
        "month": 6,
        "is_friday_evening": 0,
        "repo_is_pandas": 0,
    }
    return store.predict(features, stage)


def main(argv: list[str] | None = None) -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="PR Pulse model registry")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List active / previous / on-disk versions")
    p_promote = sub.add_parser("promote", help="Activate a versioned bundle directory")
    p_promote.add_argument("version")
    sub.add_parser("rollback", help="Restore the previous active version")
    p_smoke = sub.add_parser("smoke", help="Smoke-predict with the live models dir")
    p_smoke.add_argument("--stage", default="A")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "list":
            print(json.dumps(list_versions(), indent=2))
        elif args.cmd == "promote":
            print(json.dumps(promote_version(args.version), indent=2))
        elif args.cmd == "rollback":
            print(json.dumps(rollback_version(), indent=2))
        elif args.cmd == "smoke":
            print(json.dumps(smoke_predict(stage=args.stage), indent=2, default=str))
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
