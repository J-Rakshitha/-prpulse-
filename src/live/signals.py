"""Prometheus metrics helpers.

A dedicated registry keeps the process default collectors out of /metrics and
lets tests create several apps without duplicate-registration errors.
"""

from __future__ import annotations

import time
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry()

REQUESTS = Counter("prpulse_requests_total", "HTTP requests", ["endpoint", "method", "status"], registry=REGISTRY)
LATENCY = Histogram(
    "prpulse_request_latency_seconds",
    "HTTP request latency",
    ["endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=REGISTRY,
)
PREDICTIONS = Counter("prpulse_predictions_total", "Predictions served", ["stage", "model_version"], registry=REGISTRY)
PREDICTION_LATENCY = Histogram(
    "prpulse_prediction_latency_seconds",
    "End-to-end prediction latency (model inference path)",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)
WEBHOOK_EVENTS = Counter("prpulse_webhook_events_total", "Webhook deliveries", ["event", "result"], registry=REGISTRY)
WORKER_JOBS = Counter("prpulse_worker_jobs_total", "Worker job outcomes", ["result"], registry=REGISTRY)
MODEL_INFO = Gauge("prpulse_model_info", "Active model version (value is always 1)", ["version"], registry=REGISTRY)
LIVE_OUTCOMES = Gauge("prpulse_live_outcomes", "Resolved PRs with a stored prediction", registry=REGISTRY)
LIVE_MAE = Gauge("prpulse_live_mae_hours", "Live merge-hours MAE of the p50 prediction", registry=REGISTRY)
LIVE_COVERAGE = Gauge("prpulse_live_interval_coverage", "Share of merged PRs inside the p10-p90 interval", registry=REGISTRY)
LIVE_BRIER_7D = Gauge("prpulse_live_brier_merged_7d", "Live Brier score of prob_merged_within_7d", registry=REGISTRY)
LIVE_ACCURACY_UPDATED = Gauge(
    "prpulse_live_accuracy_updated_unixtime",
    "Unix time when live accuracy gauges were last refreshed",
    registry=REGISTRY,
)
QUEUE_DEPTH = Gauge("prpulse_queue_depth", "RQ queue depth", registry=REGISTRY)
PENDING_DELIVERIES = Gauge("prpulse_pending_deliveries", "Deliveries with status=pending", registry=REGISTRY)
FAILED_JOBS = Gauge("prpulse_failed_jobs", "Rows in failed_jobs with status=failed", registry=REGISTRY)
DEAD_LETTER_COUNT = Gauge(
    "prpulse_dead_letter_count",
    "Dead-letter / failed job count (alias of failed_jobs when not split)",
    registry=REGISTRY,
)
GITHUB_RATE_REMAINING = Gauge("prpulse_github_rate_limit_remaining", "GitHub X-RateLimit-Remaining", registry=REGISTRY)
WEBHOOK_ACK = Histogram(
    "prpulse_webhook_ack_seconds",
    "Webhook handler ack latency (HMAC + persist + enqueue)",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)
INSTALLATIONS_COUNT = Gauge("prpulse_installations_count", "Active (not uninstalled) GitHub App installations", registry=REGISTRY)
LAST_WEBHOOK_UNIXTIME = Gauge("prpulse_last_webhook_unixtime", "Unix time of the most recent webhook delivery", registry=REGISTRY)
WEBHOOK_SILENCE_SECONDS = Gauge(
    "prpulse_webhook_silence_seconds",
    "Configured silence threshold before NoWebhookEvents fires",
    registry=REGISTRY,
)
DB_UP = Gauge("prpulse_db_up", "1 when the database answers SELECT 1", registry=REGISTRY)
COVERAGE_STATUS = Gauge(
    "prpulse_coverage_status",
    "Stored predictions by coverage_status",
    ["status"],
    registry=REGISTRY,
)

_last_refresh = 0.0


def observe_request(endpoint: str, method: str, status: int, seconds: float) -> None:
    REQUESTS.labels(endpoint=endpoint, method=method, status=str(status)).inc()
    LATENCY.labels(endpoint=endpoint).observe(seconds)


def record_prediction(stage: str, model_version: str) -> None:
    PREDICTIONS.labels(stage=stage, model_version=model_version).inc()


def observe_prediction_latency(seconds: float) -> None:
    PREDICTION_LATENCY.observe(seconds)


def record_webhook(event: str, result: str) -> None:
    WEBHOOK_EVENTS.labels(event=event or "unknown", result=result).inc()
    LAST_WEBHOOK_UNIXTIME.set(time.time())


def record_worker_job(result: str) -> None:
    WORKER_JOBS.labels(result=result or "unknown").inc()


def observe_webhook_ack(seconds: float) -> None:
    WEBHOOK_ACK.observe(seconds)


def set_queue_depth(depth: int | float) -> None:
    QUEUE_DEPTH.set(float(depth))


def set_pending_deliveries(n: int | float) -> None:
    PENDING_DELIVERIES.set(float(n))


def set_failed_jobs(n: int | float) -> None:
    FAILED_JOBS.set(float(n))
    DEAD_LETTER_COUNT.set(float(n))


def set_dead_letter_count(n: int | float) -> None:
    DEAD_LETTER_COUNT.set(float(n))


def set_github_rate_remaining(n: int | float | None) -> None:
    if n is None:
        return
    GITHUB_RATE_REMAINING.set(float(n))


def set_installations_count(n: int | float) -> None:
    INSTALLATIONS_COUNT.set(float(n))


def set_last_webhook_unixtime(ts: float | int | None) -> None:
    if ts is None:
        return
    LAST_WEBHOOK_UNIXTIME.set(float(ts))


def set_webhook_silence_seconds(seconds: float | int) -> None:
    WEBHOOK_SILENCE_SECONDS.set(float(seconds))


def set_db_up(ok: bool) -> None:
    DB_UP.set(1.0 if ok else 0.0)


def set_coverage_status_counts(counts: dict[str, int]) -> None:
    COVERAGE_STATUS.clear()
    for status, n in counts.items():
        COVERAGE_STATUS.labels(status=str(status or "unknown")).set(float(n))


def set_model_version(version: str | None) -> None:
    MODEL_INFO.clear()
    if version:
        MODEL_INFO.labels(version=version).set(1)


def update_live_accuracy(summary: dict[str, Any]) -> None:
    """Set gauges from ``outcomes.compute_errors``. Gauges stay unset (NaN) when there is no data."""
    LIVE_OUTCOMES.set(summary.get("n_resolved", 0))
    for gauge, key in (
        (LIVE_MAE, "mae_hours"),
        (LIVE_COVERAGE, "interval_coverage"),
        (LIVE_BRIER_7D, "brier_merged_7d"),
    ):
        value = summary.get(key)
        gauge.set(float("nan") if value is None else float(value))
    LIVE_ACCURACY_UPDATED.set(time.time())


def refresh_live_accuracy(conn, min_interval_s: float = 30.0, cfg: dict | None = None) -> None:
    """Recompute live accuracy from SQLite at most once per ``min_interval_s``."""
    global _last_refresh
    now = time.monotonic()
    if now - _last_refresh < min_interval_s:
        return
    _last_refresh = now
    from src.live.outcomes import compute_errors

    update_live_accuracy(compute_errors(conn, cfg=cfg))


def reset_refresh_clock() -> None:
    global _last_refresh
    _last_refresh = 0.0


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
