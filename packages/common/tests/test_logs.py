"""Structured logs: one JSON object per line, with request correlation and extras."""

from __future__ import annotations

import io
import json
import logging

from prognos_common import logs


def capture(fmt: str = "json") -> io.StringIO:
    logs.configure("test-svc", level="INFO", fmt=fmt)
    stream = io.StringIO()
    handler = logging.getLogger().handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(stream)
    return stream


def test_json_lines_carry_service_request_id_and_extras() -> None:
    stream = capture()
    token = logs.request_id.set("req-42")
    try:
        logging.getLogger("x").warning("slow %s", "call", extra={"duration_ms": 1234.5})
    finally:
        logs.request_id.reset(token)
    entry = json.loads(stream.getvalue())
    assert entry["service"] == "test-svc"
    assert entry["level"] == "warning"
    assert entry["msg"] == "slow call"
    assert entry["request_id"] == "req-42"
    assert entry["duration_ms"] == 1234.5
    assert entry["ts"].endswith("Z")


def test_exceptions_are_included_and_no_request_id_outside_requests() -> None:
    stream = capture()
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("x").exception("failed")
    entry = json.loads(stream.getvalue())
    assert "ValueError: boom" in entry["exc"]
    assert "request_id" not in entry


def test_text_format_for_terminals() -> None:
    stream = capture("text")
    logging.getLogger("x").info("hello")
    assert "test-svc hello" in stream.getvalue()
