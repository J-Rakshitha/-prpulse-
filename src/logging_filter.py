"""Log redaction: tokens, signatures, private keys, and URL passwords never reach a log line.

``RedactingFilter`` is attached to every handler by ``src.setup_logging``. It
rewrites the fully formatted message (so ``%s`` arguments are covered) and the
exception text. Exact secret values from the environment can be registered with
``register_secret`` in addition to the pattern rules.
"""

from __future__ import annotations

import logging
import os
import re
import threading

REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), REDACTED),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=\s]*"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._~+/=-]{12,}"), r"\1 " + REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), REDACTED),
    (re.compile(r"(?i)sha(?:1|256)=[0-9a-f]{40,64}"), "sha256=" + REDACTED),
    (re.compile(r"(?i)(x-hub-signature(?:-256)?['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)\b(postgres(?:ql)?(?:\+\w+)?|rediss?)://([^:/@\s]*):[^@/\s]+@"), r"\1://\2:" + REDACTED + "@"),
    (re.compile(r"(?i)\b(webhook_secret|github_token|private_key|password|secret)(['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]{4,}"), r"\1\2" + REDACTED),
)

_secret_lock = threading.Lock()
_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Also redact this exact string (ignored when shorter than 8 characters)."""
    if value and len(value) >= 8:
        with _secret_lock:
            _secrets.add(value)


def register_env_secrets(names: tuple[str, ...] = ("WEBHOOK_SECRET", "GITHUB_TOKEN", "GITHUB_APP_PRIVATE_KEY", "PRPULSE_ADMIN_TOKEN")) -> None:
    for name in names:
        register_secret(os.environ.get(name, "").strip())


def redact(text: str) -> str:
    if not text:
        return text
    with _secret_lock:
        known = sorted(_secrets, key=len, reverse=True)
    for secret in known:
        if secret in text:
            text = text.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let a bad format string drop the record
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def install(handler: logging.Handler) -> None:
    """Attach the filter once."""
    if not any(isinstance(f, RedactingFilter) for f in handler.filters):
        handler.addFilter(RedactingFilter())
