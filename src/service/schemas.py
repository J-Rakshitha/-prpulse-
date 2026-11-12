"""Pydantic request/response models for the PR Pulse API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Stage = Literal["A", "B", "C"]
FeatureValue = float | int | bool | None


class PredictRequest(BaseModel):
    """Feature values known at the chosen stage.

    Unknown feature names are rejected. Missing features are imputed with the
    training medians and listed in the response.
    """

    model_config = ConfigDict(extra="forbid")

    repo_full_name: str | None = Field(default=None, pattern=r"^[^/\s]+/[^/\s]+$", examples=["pandas-dev/pandas"])
    pr_number: int | None = Field(default=None, ge=1)
    author_login: str | None = None
    stage: Stage = "A"
    features: dict[str, FeatureValue] = Field(default_factory=dict)
    include_explanation: bool = False


class Contribution(BaseModel):
    feature: str
    contribution: float
    plain_english: str
    value: float | None = None


class Suggestion(BaseModel):
    id: str
    action: str
    feature_changes: dict[str, float]
    predicted_hours_before: float
    predicted_hours_after: float
    estimated_change_hours: float
    is_estimate: bool = True
    label: str


class PredictResponse(BaseModel):
    model_version: str
    stage: Stage
    merge_hours_p10: float
    merge_hours_p50: float
    merge_hours_p90: float
    prob_merged_within_7d: float | None = None
    prob_ci_fail: float | None = None
    missing_features: list[str] = Field(default_factory=list)
    prediction_id: int | None = Field(default=None, description="predictions.id, or null when no repo/PR was given")
    explanation: list[Contribution] | None = None
    suggestions: list[Suggestion] | None = None
    latency_ms: float


class ExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Stage = "A"
    features: dict[str, FeatureValue] = Field(default_factory=dict)
    top_k: int = Field(default=5, ge=1, le=20)
    include_suggestions: bool = True


class ExplainResponse(BaseModel):
    model_version: str
    stage: Stage
    method: Literal["shap", "difference"]
    unit: str = "log1p(hours); positive lengthens predicted merge time"
    contributions: list[Contribution]
    suggestions: list[Suggestion] = Field(default_factory=list)
    suggestions_note: str | None = None


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    model_version: str | None
    models_loaded: int
    stages: list[str]
    db_ok: bool
    uptime_seconds: float


class ModelInfo(BaseModel):
    task: str
    stage: Stage
    path: str
    n_features: int
    modified_at: str


class Models(BaseModel):
    active_version: str | None
    registry_active: str | None = None
    models: list[ModelInfo]
