from __future__ import annotations

import logging

from src.logging_filter import REDACTED, RedactingFilter, redact, register_secret


def test_redact_patterns():
    text = (
        "token ghp_abcdefghijklmnopqrstuvwxyz012345 "
        "bearer ABCDEFGHIJKLMNOPQRSTUV "
        "sha256=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef "
        "postgres://user:supersecret@localhost:5432/db "
        "-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----"
    )
    out = redact(text)
    assert "ghp_" not in out
    assert "supersecret" not in out
    assert "ABCDEFGHIJKLMNOPQRSTUV" not in out
    assert REDACTED in out


def test_register_secret_exact():
    register_secret("my-webhook-secret-value")
    assert REDACTED in redact("got my-webhook-secret-value in log")


def test_filter_rewrites_log_record():
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz012345",
        args=(),
        exc_info=None,
    )
    assert RedactingFilter().filter(record) is True
    assert "ghp_" not in record.getMessage()
    assert REDACTED in record.getMessage()
