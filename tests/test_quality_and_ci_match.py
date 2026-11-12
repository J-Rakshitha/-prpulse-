"""Quality report smoke test and CI SHA matching."""

from __future__ import annotations

from pathlib import Path

from src.data.collect import _match_run_to_pr
from src.data.db import connect, init_db
from src.data.quality import write_data_quality_report
from src.settings import load_settings


def test_match_run_prefers_pull_number_then_sha() -> None:
    by_number = {10: 100}
    by_sha = {"deadbeef": 200}
    assert (
        _match_run_to_pr(
            {"pull_requests": [{"number": 10}], "head_sha": "deadbeef"},
            number_to_pr=by_number,
            sha_to_pr=by_sha,
        )
        == 100
    )
    assert (
        _match_run_to_pr(
            {"pull_requests": [], "head_sha": "deadbeef"},
            number_to_pr=by_number,
            sha_to_pr=by_sha,
        )
        == 200
    )
    assert (
        _match_run_to_pr(
            {"pull_requests": [], "head_sha": "missing"},
            number_to_pr=by_number,
            sha_to_pr=by_sha,
        )
        is None
    )


def test_quality_report_writes_for_empty_db(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    init_db(db)
    cfg = load_settings()
    with connect(db) as conn:
        path = write_data_quality_report(
            conn,
            tmp_path / "data_quality.md",
            cfg=cfg,
            cutoff_iso="2024-11-02T00:00:00Z",
            client_request_count=0,
        )
    text = path.read_text(encoding="utf-8")
    assert "Per-repo summary" in text
    assert "ci_label_available" in text
    assert "Not yet downloaded" in text
