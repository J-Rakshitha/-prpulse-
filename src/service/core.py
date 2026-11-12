"""Prediction service logic shared by the HTTP API and the GitHub bot."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from src.data.db import connect
from src.live import signals
from src.models.explain import ESTIMATE_LABEL, explain_with_method, suggestions as make_suggestions
from src.models.registry import ModelStore
from src.service.store import get_or_create_pr, insert_prediction

logger = logging.getLogger("prpulse.service")


class UnknownFeatures(ValueError):
    """Request contained feature names the stage's model does not use."""


class StageUnavailable(KeyError):
    """No model is loaded for the requested stage."""


class PredictionService:
    def __init__(self, store: ModelStore, model_version: str, db_path: "Path | str"):
        self.store = store
        self.model_version = model_version
        # Keep PostgreSQL URLs as strings; only filesystem paths become Path.
        from src.data.database import is_postgres_url

        self.db_path: Path | str = db_path if is_postgres_url(db_path) else Path(db_path)

    def _bundle(self, stage: str) -> dict[str, Any]:
        if not self.store.has("merge_hours", stage):
            raise StageUnavailable(f"No merge_hours model is loaded for stage {stage}")
        return self.store.get("merge_hours", stage)

    def filter_features(self, features: dict[str, Any], stage: str) -> dict[str, Any]:
        """Keep only columns the stage model uses.

        Live bots and clients may still send Stage A aux fields (size / path
        flags) that were dropped from training after leakage ablation. Those
        extras are ignored; missing model columns are imputed from training
        medians inside ``ModelStore.predict``.
        """
        allowed = set(self._bundle(stage)["feature_columns"])
        return {key: value for key, value in features.items() if key in allowed}

    def validate_features(self, features: dict[str, Any], stage: str) -> None:
        """Deprecated strict check; prefer ``filter_features``.

        Kept for callers/tests that still expect ``UnknownFeatures`` when every
        provided key is unknown *and* ``strict`` validation is desired. After
        filtering, this is a no-op for normal predict/explain paths.
        """
        allowed = set(self._bundle(stage)["feature_columns"])
        unknown = sorted(set(features) - allowed)
        if unknown:
            raise UnknownFeatures(f"Unknown features for stage {stage}: {unknown}")

    def predict(
        self,
        stage: str,
        features: dict[str, Any],
        repo_full_name: str | None = None,
        pr_number: int | None = None,
        author_login: str | None = None,
        include_explanation: bool = False,
        created_at: str | None = None,
        draft: bool = False,
        store_prediction: bool = True,
        coverage_status: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        features = self.filter_features(features, stage)
        result = self.store.predict(features, stage)
        explanation = None
        suggestion_list = None
        if include_explanation:
            bundle = self._bundle(stage)
            explanation, _, _ = explain_with_method(features, stage, bundle=bundle)
            suggestion_list = make_suggestions(features, stage, bundle=bundle)
        if coverage_status in ("seen", "unseen"):
            result["coverage_status"] = coverage_status
        result["prediction_id"] = None
        if store_prediction and repo_full_name and pr_number:
            conn = connect(self.db_path)
            try:
                pr_id = get_or_create_pr(conn, repo_full_name, pr_number, author_login, created_at, draft)
                result["prediction_id"] = insert_prediction(
                    conn,
                    pr_id,
                    self.model_version,
                    stage,
                    result,
                    explanation,
                    suggestion_list,
                    coverage_status=coverage_status,
                )
            finally:
                conn.close()
        signals.record_prediction(stage, self.model_version)
        signals.observe_prediction_latency(time.perf_counter() - started)
        result["model_version"] = self.model_version
        result["explanation"] = explanation
        result["suggestions"] = suggestion_list
        return result

    def explain(self, stage: str, features: dict[str, Any], top_k: int = 5, include_suggestions: bool = True) -> dict[str, Any]:
        features = self.filter_features(features, stage)
        bundle = self._bundle(stage)
        items, method, _ = explain_with_method(features, stage, bundle=bundle, top_k=top_k)
        out: dict[str, Any] = {
            "model_version": self.model_version,
            "stage": stage,
            "method": method,
            "contributions": items,
            "suggestions": [],
            "suggestions_note": None,
        }
        if include_suggestions:
            out["suggestions"] = make_suggestions(features, stage, bundle=bundle)
            out["suggestions_note"] = ESTIMATE_LABEL
        return out

    def db_ok(self) -> bool:
        try:
            conn: sqlite3.Connection = connect(self.db_path)
            try:
                conn.execute("SELECT 1 FROM predictions LIMIT 1")
            finally:
                conn.close()
            return True
        except Exception:  # noqa: BLE001
            logger.exception("database health check failed")
            return False
