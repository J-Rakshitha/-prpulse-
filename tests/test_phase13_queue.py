from __future__ import annotations

from src.data.db import connect, init_db
from src.service import deliveries as deliv
from src.service import queue as queue_mod
from src.service.worker_jobs import backoff_seconds


def test_insert_delivery_is_idempotent(tmp_path):
    path = tmp_path / "q.db"
    init_db(path)
    conn = connect(path)
    try:
        ok, status = deliv.insert_delivery(
            conn, delivery_id="d1", event="ping", payload={"zen": "x"}
        )
        assert ok and status == "pending"
        ok2, status2 = deliv.insert_delivery(
            conn, delivery_id="d1", event="ping", payload={"zen": "x"}
        )
        assert not ok2
        assert status2 == "pending"
        n = conn.execute("SELECT COUNT(*) AS n FROM webhook_deliveries").fetchone()["n"]
        assert n == 1
    finally:
        conn.close()


def test_enqueue_returns_false_without_redis(tmp_path):
    queue_mod.configure(redis_url="", queue_name="prpulse", db_target=tmp_path / "x.db")
    assert queue_mod.enqueue_delivery("whatever") is False
    assert queue_mod.redis_available("") is False


def test_backoff_grows_and_is_capped():
    from src.runtime_settings import RuntimeSettings

    settings = RuntimeSettings(
        job_backoff_base_seconds=2.0,
        job_backoff_cap_seconds=10.0,
    )
    a1 = backoff_seconds(1, settings)
    a4 = backoff_seconds(4, settings)
    assert a1 >= 2.0
    assert a4 >= a1
    assert a4 <= 10.0 * 1.25 + 0.01
