"""Assert Prometheus metric names used by deploy/alerts.yml exist in REGISTRY."""

from __future__ import annotations

import re
from pathlib import Path

from prometheus_client import CollectorRegistry

from src.live import signals

ROOT = Path(__file__).resolve().parents[1]
ALERTS = (ROOT / "deploy" / "alerts.yml").read_text(encoding="utf-8")


def _metric_names_from_alerts(text: str) -> set[str]:
    found = set(re.findall(r"\bprpulse_[a-z0-9_]+\b", text))
    bases: set[str] = set()
    for name in found:
        # Strip Prometheus histogram/summary series suffixes only (not gauge names
        # that legitimately end in _count, e.g. prpulse_dead_letter_count).
        for suffix in ("_bucket", "_sum"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        bases.add(name)
    return bases


def test_alert_metric_names_registered():
    expected = _metric_names_from_alerts(ALERTS)
    required = {
        "prpulse_webhook_events_total",
        "prpulse_webhook_ack_seconds",
        "prpulse_prediction_latency_seconds",
        "prpulse_queue_depth",
        "prpulse_worker_jobs_total",
        "prpulse_dead_letter_count",
        "prpulse_installations_count",
        "prpulse_last_webhook_unixtime",
        "prpulse_webhook_silence_seconds",
        "prpulse_github_rate_limit_remaining",
        "prpulse_db_up",
        "prpulse_live_accuracy_updated_unixtime",
    }
    assert required <= expected
    # Labelled counters only appear after a sample; Counter.name also drops "_total".
    signals.record_webhook("pull_request", "ok")
    signals.record_worker_job("ok")
    signals.observe_prediction_latency(0.01)
    signals.observe_webhook_ack(0.01)
    signals.set_queue_depth(0)
    signals.set_failed_jobs(0)
    signals.set_installations_count(0)
    signals.set_db_up(True)
    signals.set_webhook_silence_seconds(86400)
    signals.set_last_webhook_unixtime(1.0)
    signals.set_github_rate_remaining(5000)
    signals.update_live_accuracy(
        {"n_resolved": 0, "mae_hours": None, "interval_coverage": None, "brier_merged_7d": None}
    )
    collected: set[str] = set()
    for m in signals.REGISTRY.collect():
        collected.add(m.name)
        if not m.name.endswith("_total"):
            collected.add(m.name + "_total")
    missing = sorted(required - collected)
    assert not missing, f"Missing metrics for alerts.yml: {missing}"


def test_registry_is_isolated():
    assert isinstance(signals.REGISTRY, CollectorRegistry)
    signals.set_failed_jobs(0)
    signals.set_installations_count(0)
    signals.set_db_up(True)
    signals.set_webhook_silence_seconds(86400)
    signals.observe_prediction_latency(0.01)
    signals.record_worker_job("ok")
    body, _ = signals.render_metrics()
    text = body.decode("utf-8")
    for name in (
        "prpulse_prediction_latency_seconds",
        "prpulse_worker_jobs_total",
        "prpulse_dead_letter_count",
        "prpulse_db_up",
        "prpulse_live_accuracy_updated_unixtime",
    ):
        assert name in text
