"""GitHub webhook endpoint: HMAC verify, persist delivery, enqueue, fast 200."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.data.db import connect
from src.live import signals
from src.service import deliveries as deliv
from src.service import queue as queue_mod
from src.service.bot import note_result

logger = logging.getLogger("prpulse.webhook")

router = APIRouter()


def sign(secret: str, body: bytes) -> str:
    """Return the ``sha256=...`` header value GitHub would send."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str | None, body: bytes, header: str | None) -> bool:
    """Constant-time check of X-Hub-Signature-256. An empty secret never verifies."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    return hmac.compare_digest(sign(secret, body), header)


class DeliveryCache:
    """In-memory hint cache kept for tests/back-compat. Durable idempotency is the DB."""

    def __init__(self, max_size: int = 5000):
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._max = max_size
        self._lock = threading.Lock()

    def claim(self, delivery_id: str) -> bool:
        with self._lock:
            if delivery_id in self._seen:
                return False
            self._seen[delivery_id] = None
            while len(self._seen) > self._max:
                self._seen.popitem(last=False)
            return True

    def release(self, delivery_id: str) -> None:
        with self._lock:
            self._seen.pop(delivery_id, None)


def process_event(bot: Any, event: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Legacy sync dispatcher (used by process_delivery_now via the worker path)."""
    if event == "ping":
        return {"status": "pong"}
    if bot is None:
        return {"status": "ignored", "reason": "bot not configured"}
    if event == "pull_request":
        return bot.on_pull_request(payload)
    if event == "workflow_run":
        return bot.on_workflow_run(payload)
    if event in ("installation", "installation_repositories"):
        from src.service.installations import apply_installation_event

        db_path = getattr(bot, "db_path", None)
        if db_path is None:
            return {"status": "ignored", "reason": "no database"}
        conn = connect(db_path)
        try:
            return apply_installation_event(conn, event, payload)
        finally:
            conn.close()
    return {"status": "ignored", "reason": f"event {event!r} not handled"}


@router.post("/webhook")
async def webhook(request: Request) -> JSONResponse:
    """Verify HMAC, persist the delivery, enqueue (or leave pending), return 200 fast.

    Never runs model inference or GitHub API calls in this handler.
    """
    started = time.perf_counter()
    state = request.app.state
    secret = getattr(state, "webhook_secret", None)
    if not secret:
        return JSONResponse({"detail": "WEBHOOK_SECRET is not configured"}, status_code=503)
    body = await request.body()
    if not verify_signature(secret, body, request.headers.get("X-Hub-Signature-256")):
        note_result("unknown", {"status": "bad_signature"})
        return JSONResponse({"detail": "invalid signature"}, status_code=401)

    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery") or ""
    if not delivery_id:
        return JSONResponse({"detail": "missing X-GitHub-Delivery"}, status_code=400)

    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
    except ValueError:
        return JSONResponse({"detail": "invalid JSON body"}, status_code=400)

    # Ping can ack without waiting on a worker, but still persist for audit.
    db_path = state.db_path
    conn = connect(db_path)
    try:
        inserted, existing_status = deliv.insert_delivery(
            conn, delivery_id=delivery_id, event=event, payload=payload
        )
        if not inserted:
            note_result(event, {"status": "duplicate"})
            signals.observe_webhook_ack(time.perf_counter() - started)
            return JSONResponse({"status": "duplicate", "delivery": delivery_id, "prior_status": existing_status})

        if event == "ping":
            # Fast path: mark done immediately; no model / GitHub needed.
            result = {"status": "pong"}
            deliv.mark_finished(conn, delivery_id, result, status="done")
            note_result(event, result)
            signals.observe_webhook_ack(time.perf_counter() - started)
            return JSONResponse({"status": "pong", "delivery": delivery_id})

        enqueued = queue_mod.enqueue_delivery(delivery_id)
        if enqueued:
            deliv.mark_enqueued(conn, delivery_id)
            response = {"status": "accepted", "delivery": delivery_id}
        else:
            # Redis down / unset: still 200; recovery loop or tests drain pending.
            response = {"status": "pending", "delivery": delivery_id, "reason": "queue unavailable"}
        note_result(event, response)
        signals.observe_webhook_ack(time.perf_counter() - started)
        return JSONResponse(response)
    finally:
        conn.close()
