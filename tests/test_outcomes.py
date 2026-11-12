from __future__ import annotations

import pytest

from src.data.db import connect, init_db
from src.live import outcomes
from src.service.store import ensure_model_version, find_pr, get_or_create_pr, insert_prediction
from src.settings import load_settings

T0 = "2026-01-01T00:00:00Z"
CFG = load_settings()


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "o.db"
    init_db(path)
    c = connect(path)
    ensure_model_version(c, "v-test")
    yield c
    c.close()


def predict(conn, number, p10=2.0, p50=10.0, p90=30.0, p7=0.8, pci=None, created=T0):
    pr_id = get_or_create_pr(conn, "acme/widgets", number, "alice", created)
    insert_prediction(
        conn,
        pr_id,
        "v-test",
        "A",
        {"merge_hours_p10": p10, "merge_hours_p50": p50, "merge_hours_p90": p90, "prob_merged_within_7d": p7, "prob_ci_fail": pci},
    )
    return pr_id


def outcome_row(conn, pr_id):
    return conn.execute("SELECT * FROM outcomes WHERE pr_id = ?", (pr_id,)).fetchone()


def test_merge_records_hours_and_state(conn):
    pr_id = predict(conn, 1)
    assert outcomes.on_pr_closed(conn, "acme/widgets", 1, merged=True, merged_at="2026-01-01T12:00:00Z", closed_at="2026-01-01T12:00:00Z") == pr_id
    o = outcome_row(conn, pr_id)
    assert (o["merged"], o["actual_merge_hours"], o["resolved_at"]) == (1, 12.0, "2026-01-01T12:00:00Z")
    pr = find_pr(conn, "acme/widgets", 1)
    assert pr["state"] == "merged" and pr["merged_at"] == "2026-01-01T12:00:00Z"


def test_close_without_merge(conn):
    pr_id = predict(conn, 2)
    outcomes.on_pr_closed(conn, "acme/widgets", 2, merged=False, closed_at="2026-01-02T00:00:00Z")
    o = outcome_row(conn, pr_id)
    assert o["merged"] == 0 and o["actual_merge_hours"] is None
    assert find_pr(conn, "acme/widgets", 2)["state"] == "closed"


def test_unknown_pr_returns_none(conn):
    assert outcomes.on_pr_closed(conn, "acme/widgets", 404, merged=True) is None
    assert outcomes.on_ci_completed(conn, "acme/widgets", 404, "success", CFG) is None


def test_ci_before_merge_is_kept_and_first_run_wins(conn):
    pr_id = predict(conn, 3)
    assert outcomes.on_ci_completed(conn, "acme/widgets", 3, "cancelled", CFG) is None  # not a usable label
    assert outcome_row(conn, pr_id) is None
    outcomes.on_ci_completed(conn, "acme/widgets", 3, "failure", CFG)
    outcomes.on_ci_completed(conn, "acme/widgets", 3, "success", CFG)  # later rerun must not overwrite
    o = outcome_row(conn, pr_id)
    assert (o["ci_first_conclusion"], o["ci_failed_first_run"], o["resolved_at"]) == ("failure", 1, None)
    assert outcomes.compute_errors(conn, cfg=CFG)["n_resolved"] == 0  # CI alone is not a resolution
    outcomes.on_pr_closed(conn, "acme/widgets", 3, merged=True, merged_at="2026-01-01T05:00:00Z")
    o = outcome_row(conn, pr_id)
    assert o["ci_failed_first_run"] == 1 and o["merged"] == 1  # merge kept the CI fields


def test_timed_out_counts_as_failure_and_success_does_not(conn):
    a, b = predict(conn, 4), predict(conn, 5)
    outcomes.on_ci_completed(conn, "acme/widgets", 4, "timed_out", CFG)
    outcomes.on_ci_completed(conn, "acme/widgets", 5, "success", CFG)
    assert outcome_row(conn, a)["ci_failed_first_run"] == 1
    assert outcome_row(conn, b)["ci_failed_first_run"] == 0


def test_reopen_clears_resolution(conn):
    pr_id = predict(conn, 6)
    outcomes.on_pr_closed(conn, "acme/widgets", 6, merged=False, closed_at="2026-01-02T00:00:00Z")
    outcomes.on_pr_reopened(conn, "acme/widgets", 6)
    o = outcome_row(conn, pr_id)
    assert o["resolved_at"] is None and o["merged"] is None
    pr = find_pr(conn, "acme/widgets", 6)
    assert pr["state"] == "open" and pr["closed_at"] is None and pr["was_reopened"] == 1
    assert outcomes.compute_errors(conn, cfg=CFG)["n_resolved"] == 0
    outcomes.on_pr_closed(conn, "acme/widgets", 6, merged=True, merged_at="2026-01-03T00:00:00Z")
    assert outcome_row(conn, pr_id)["actual_merge_hours"] == 48.0


def test_errors_with_no_data_are_none(conn):
    s = outcomes.compute_errors(conn, cfg=CFG)
    assert s["n_resolved"] == 0 and s["mae_hours"] is None and s["brier_merged_7d"] is None and s["by_stage"] == {}


def test_compute_errors_simulated_sequence(conn):
    # PR 1: forecast p50=10h, actual 12h, inside interval, ci passes
    predict(conn, 1, p7=0.9, pci=0.2)
    outcomes.on_ci_completed(conn, "acme/widgets", 1, "success", CFG)
    outcomes.on_pr_closed(conn, "acme/widgets", 1, merged=True, merged_at="2026-01-01T12:00:00Z")
    # PR 2: forecast p50=20h, actual 100h, outside interval, ci fails
    predict(conn, 2, p10=5, p50=20, p90=40, p7=0.7, pci=0.6)
    outcomes.on_ci_completed(conn, "acme/widgets", 2, "failure", CFG)
    outcomes.on_pr_closed(conn, "acme/widgets", 2, merged=True, merged_at="2026-01-05T04:00:00Z")
    # PR 3: closed without merge, forecast said 0.5 chance within 7 days
    predict(conn, 3, p7=0.5)
    outcomes.on_pr_closed(conn, "acme/widgets", 3, merged=False, closed_at="2026-01-03T00:00:00Z")
    # PR 4: merged after 9 days, so merged_within_7d label is 0
    predict(conn, 4, p7=0.4)
    outcomes.on_pr_closed(conn, "acme/widgets", 4, merged=True, merged_at="2026-01-10T00:00:00Z")
    # PR 5: still open, must not count
    predict(conn, 5)

    s = outcomes.compute_errors(conn, cfg=CFG)
    assert s["n_resolved"] == 4
    assert s["n_merged"] == 3
    # errors |10-12|=2, |20-100|=80, p50 default 10 vs 216h => 206
    assert s["mae_hours"] == pytest.approx((2 + 80 + 206) / 3)
    assert s["median_ae_hours"] == pytest.approx(80)
    assert s["interval_coverage"] == pytest.approx(1 / 3)
    # labels 1, 0 (100h is within 7d => 1)... PR2 actual 100h <= 168 so label 1; PR3 0; PR4 0
    expected = ((0.9 - 1) ** 2 + (0.7 - 1) ** 2 + (0.5 - 0) ** 2 + (0.4 - 0) ** 2) / 4
    assert s["brier_merged_7d"] == pytest.approx(expected)
    assert s["n_ci_scored"] == 2
    assert s["brier_ci_fail"] == pytest.approx(((0.2 - 0) ** 2 + (0.6 - 1) ** 2) / 2)
    assert s["by_stage"]["A"]["n_resolved"] == 4


def test_earliest_prediction_is_scored(conn):
    pr_id = predict(conn, 1, p50=10.0)
    insert_prediction(conn, pr_id, "v-test", "A", {"merge_hours_p10": 11, "merge_hours_p50": 12.0, "merge_hours_p90": 13})
    outcomes.on_pr_closed(conn, "acme/widgets", 1, merged=True, merged_at="2026-01-01T12:00:00Z")
    assert outcomes.compute_errors(conn, cfg=CFG)["mae_hours"] == pytest.approx(2.0)


def test_filter_by_model_version(conn):
    predict(conn, 1)
    outcomes.on_pr_closed(conn, "acme/widgets", 1, merged=True, merged_at="2026-01-01T12:00:00Z")
    assert outcomes.compute_errors(conn, model_version="v-test", cfg=CFG)["n_resolved"] == 1
    assert outcomes.compute_errors(conn, model_version="other", cfg=CFG)["n_resolved"] == 0


def test_signals_gauges_from_summary(conn):
    from src.live import signals

    predict(conn, 1)
    outcomes.on_pr_closed(conn, "acme/widgets", 1, merged=True, merged_at="2026-01-01T12:00:00Z")
    signals.update_live_accuracy(outcomes.compute_errors(conn, cfg=CFG))
    text = signals.render_metrics()[0].decode()
    assert "prpulse_live_mae_hours 2.0" in text
    assert "prpulse_live_outcomes 1.0" in text
