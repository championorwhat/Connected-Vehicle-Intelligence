"""Sink process exit behaviour when a store stays down past the retry budget (M13 drill)."""

from __future__ import annotations

import logging
from typing import Any

import psycopg
import pytest

from prognos_stream import sink_main


class FakeStore:
    closed = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.stats: dict[str, int] = {}

    def close(self) -> None:
        FakeStore.closed = True


class DownService:
    def __init__(self, *args: Any) -> None:
        pass

    def install_signal_handlers(self) -> None:
        pass

    def run(self) -> dict[str, int]:
        raise psycopg.OperationalError("connection refused")


def test_exits_with_1_and_a_clear_reason_without_committing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("METRICS_PORT", "0")
    monkeypatch.setenv("POSTGRES_PASSWORD", "unused")
    monkeypatch.setattr(sink_main, "configure", lambda _service: None)
    monkeypatch.setattr(sink_main, "AlertStore", FakeStore)
    monkeypatch.setattr(sink_main, "LiveStateStore", FakeStore)
    monkeypatch.setattr(sink_main, "SinkService", DownService)
    with caplog.at_level(logging.ERROR, logger=sink_main.log.name):
        assert sink_main.main([]) == 1
    assert FakeStore.closed  # connections released even on the failure path
    assert "exiting for a restart (offsets not committed)" in caplog.text
