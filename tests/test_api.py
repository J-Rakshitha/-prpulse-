from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from src.data.db import connect
from src.models.registry import default_models_dir
from src.service.api import create_app
from tests.helpers import SAMPLE_FEATURES


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "api.db"


@pytest.fixture()
def client(tiny_models_dir, db_path):
    app = create_app(models_dir=tiny_models_dir, db_path=db_path, webhook_secret="")
    with TestClient(app) as c:
        yield c


def test_health_and_models(client):
    h = client.get("/health").json()
    assert h["status"] == "ok"
    assert h["models_loaded"] >= 2 and "A" in h["stages"] and h["db_ok"] is True
    m = client.get("/models").json()
    assert m["active_version"] == h["model_version"]
    assert {(x["task"], x["stage"]) for x in m["models"]} >= {("merge_hours", "A"), ("merged_within_7d", "A")}


def test_predict_returns_ordered_quantiles(client):
    r = client.post("/predict", json={"stage": "A", "features": SAMPLE_FEATURES})
    assert r.status_code == 200, r.text
    body = r.json()
    assert 0 <= body["merge_hours_p10"] <= body["merge_hours_p50"] <= body["merge_hours_p90"]
    assert 0 <= body["prob_merged_within_7d"] <= 1
    assert body["prob_ci_fail"] is None  # no ci model in the tiny set
    assert body["prediction_id"] is None  # no repo/PR named, nothing stored
    assert "median_merge_hours_30d" in body["missing_features"]
    assert body["latency_ms"] >= 0


def test_predict_stores_prediction_and_model_version(client, db_path):
    payload = {
        "repo_full_name": "acme/widgets",
        "pr_number": 12,
        "author_login": "alice",
        "features": SAMPLE_FEATURES,
        "include_explanation": True,
    }
    body = client.post("/predict", json=payload).json()
    assert body["prediction_id"]
    assert body["explanation"] and body["suggestions"] is not None
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM predictions WHERE id = ?", (body["prediction_id"],)).fetchone()
        assert row["model_version"] == body["model_version"]
        assert row["stage"] == "A"
        assert row["shap_json"] and row["suggestions_json"] is not None
        active = conn.execute("SELECT version FROM model_versions WHERE is_active = 1").fetchall()
        assert [a["version"] for a in active] == [body["model_version"]]
        pr = conn.execute("SELECT ingest_status, state FROM prs").fetchone()
        assert (pr["ingest_status"], pr["state"]) == ("partial", "open")
    finally:
        conn.close()


def test_predict_same_pr_twice_adds_rows_not_prs(client, db_path):
    payload = {"repo_full_name": "acme/widgets", "pr_number": 3, "features": SAMPLE_FEATURES}
    client.post("/predict", json=payload)
    client.post("/predict", json=payload)
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM prs").fetchone()[0] == 1
    finally:
        conn.close()


def test_predict_rejects_unknown_feature_and_bad_input(client):
    # Unknown keys are ignored; model imputes missing stage columns.
    assert client.post("/predict", json={"features": {"not_a_feature": 1}}).status_code == 200
    assert client.post("/predict", json={"stage": "Z", "features": {}}).status_code == 422
    assert client.post("/predict", json={"repo_full_name": "no-slash", "features": {}}).status_code == 422
    assert client.post("/predict", json={"features": {"draft": "abc"}}).status_code == 422


def test_unloaded_stage_is_503(client):
    assert client.post("/predict", json={"stage": "C", "features": {}}).status_code == 503


def test_explain_endpoint(client):
    r = client.post("/explain", json={"features": SAMPLE_FEATURES, "top_k": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] in {"shap", "difference"}
    assert len(body["contributions"]) == 3
    assert all(set(c) >= {"feature", "contribution", "plain_english"} for c in body["contributions"])
    assert body["suggestions"] and all(s["is_estimate"] for s in body["suggestions"])
    assert "Estimate only" in body["suggestions_note"]


def test_metrics_endpoint(client):
    client.post("/predict", json={"features": SAMPLE_FEATURES})
    text = client.get("/metrics").text
    assert "prpulse_requests_total" in text
    assert "prpulse_request_latency_seconds_bucket" in text
    assert "prpulse_predictions_total" in text


def test_no_models_degrades_cleanly(tmp_path):
    app = create_app(models_dir=tmp_path / "empty", db_path=tmp_path / "x.db", webhook_secret="")
    with TestClient(app) as c:
        h = c.get("/health").json()
        assert h["status"] == "degraded" and h["models_loaded"] == 0
        assert c.post("/predict", json={"features": {}}).status_code == 503
        assert c.get("/models").json()["models"] == []


@pytest.mark.skipif(not (default_models_dir() / "merge_stage_A.joblib").exists(), reason="no trained artifacts")
def test_real_artifacts_smoke(tmp_path):
    app = create_app(db_path=tmp_path / "real.db", webhook_secret="")
    with TestClient(app) as c:
        r = c.post("/predict", json={"stage": "A", "features": {"additions_approx": 100, "touches_tests": 1}})
        assert r.status_code == 200
        b = r.json()
        assert b["merge_hours_p10"] <= b["merge_hours_p50"] <= b["merge_hours_p90"]
