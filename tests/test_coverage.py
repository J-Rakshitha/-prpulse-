from __future__ import annotations

from src.data.db import connect, init_db
from src.service.bot import MARKER, UNSEEN_BANNER, render_comment
from src.service.coverage import adapt_result_for_coverage, coarse_bucket, coverage_status_for_repo, count_resolved_prs


def _seed_resolved(conn, repo: str, n: int, *, complete: bool = True) -> None:
    owner, name = repo.split("/")
    conn.execute(
        "INSERT INTO repos (id, owner, name, full_name, collected_at, updated_at) VALUES (1, ?, ?, ?, ?, ?)",
        (owner, name, repo, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    for i in range(n):
        conn.execute(
            """
            INSERT INTO prs (
                id, repo_id, number, author_login, created_at, merged_at, closed_at, state,
                ingest_status, collected_at, updated_at
            ) VALUES (?, 1, ?, 'a', '2025-01-01T00:00:00Z', '2025-01-02T00:00:00Z',
                      '2025-01-02T00:00:00Z', 'merged', ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """,
            (i + 1, i + 1, "complete" if complete else "partial"),
        )
    conn.commit()


def test_coverage_threshold_seen_vs_unseen(tmp_path):
    path = tmp_path / "c.db"
    init_db(path)
    conn = connect(path)
    try:
        _seed_resolved(conn, "acme/widgets", 49)
        assert count_resolved_prs(conn, "acme/widgets") == 49
        assert coverage_status_for_repo(conn, "acme/widgets", threshold=50) == "unseen"
        conn.execute(
            """
            INSERT INTO prs (
                id, repo_id, number, author_login, created_at, merged_at, closed_at, state,
                ingest_status, collected_at, updated_at
            ) VALUES (100, 1, 100, 'a', '2025-01-01T00:00:00Z', '2025-01-02T00:00:00Z',
                      '2025-01-02T00:00:00Z', 'merged', 'complete', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """
        )
        conn.commit()
        assert coverage_status_for_repo(conn, "acme/widgets", threshold=50) == "seen"
    finally:
        conn.close()


def test_coarse_bucket():
    assert coarse_bucket(2) == "under 1 day"
    assert coarse_bucket(48) == "1 to 7 days"
    assert coarse_bucket(200) == "over 7 days"


def test_unseen_comment_format_hides_hours_and_suggestions():
    base = {
        "merge_hours_p10": 1.0,
        "merge_hours_p50": 30.0,
        "merge_hours_p90": 80.0,
        "prob_merged_within_7d": 0.7,
        "prob_ci_fail": 0.2,
        "model_version": "v-test",
        "explanation": [{"plain_english": "large diff"}],
        "suggestions": [
            {
                "action": "split PR",
                "estimated_change_hours": 5,
                "predicted_hours_before": 30,
                "predicted_hours_after": 25,
            }
        ],
    }
    unseen = adapt_result_for_coverage(base, "unseen")
    body = render_comment(unseen, "A")
    assert body.startswith(MARKER)
    assert UNSEEN_BANNER in body
    assert "1 to 7 days" in body
    assert "What-ifs" not in body
    assert "Likely time to merge" not in body
    assert "30.0 h" not in body

    seen = adapt_result_for_coverage(base, "seen")
    seen_body = render_comment(seen, "A")
    assert UNSEEN_BANNER not in seen_body
    assert "Likely time to merge" in seen_body
    assert "What-ifs" in seen_body


def test_prediction_stores_coverage_status(tmp_path, tiny_models_dir):
    from src.models.registry import ModelStore, derive_version
    from src.service.core import PredictionService
    from src.service.store import ensure_model_version
    from tests.helpers import SAMPLE_FEATURES

    path = tmp_path / "p.db"
    init_db(path)
    store = ModelStore.load(tiny_models_dir)
    version = derive_version(tiny_models_dir)
    conn = connect(path)
    try:
        ensure_model_version(conn, version, activate=True)
    finally:
        conn.close()
    service = PredictionService(store, version, path)
    result = service.predict(
        "A",
        SAMPLE_FEATURES,
        repo_full_name="acme/new",
        pr_number=1,
        author_login="alice",
        coverage_status="unseen",
    )
    assert result["coverage_status"] == "unseen"
    conn = connect(path)
    try:
        row = conn.execute("SELECT coverage_status FROM predictions WHERE id = ?", (result["prediction_id"],)).fetchone()
        assert row["coverage_status"] == "unseen"
    finally:
        conn.close()
