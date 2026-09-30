"""Retry policy and parameter mapping (no external services)."""

from __future__ import annotations

import pytest

from prognos_stream.sinks import AlertStore, retry


class Flaky:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("down")
        return "ok"


def test_retry_recovers_after_transient_failures() -> None:
    sleeps: list[float] = []
    op = Flaky(failures=3)
    assert retry(op, retry_on=(ConnectionError,), sleep=sleeps.append) == "ok"
    assert op.calls == 4
    assert len(sleeps) == 3
    assert all(0 <= s <= 5.0 for s in sleeps)  # full jitter, capped


def test_retry_gives_up_after_budget() -> None:
    op = Flaky(failures=10**6)
    with pytest.raises(ConnectionError):
        retry(op, retry_on=(ConnectionError,), budget_s=0.05, sleep=lambda s: None)


def test_non_retryable_errors_propagate_immediately() -> None:
    def boom() -> None:
        raise ValueError("bug")

    with pytest.raises(ValueError):
        retry(boom, retry_on=(ConnectionError,), sleep=lambda s: None)


def test_alert_params_keep_evidence_in_details() -> None:
    alert = {
        "tenant_id": "t", "vehicle_id": "v", "fingerprint": "f" * 32, "rule_code": "TYRE_SLOW_LEAK",
        "severity": "warning", "failure_mode": "TYRE_SLOW_LEAK",
        "event_ts": "2026-09-30T00:00:00.000Z",
        "detected_at": "2026-09-30T00:00:01.000Z", "vin": "PG1CT1A59RC000001", "event_seq": 9,
        "value": 0.09, "threshold": 0.08, "details": {"estimated_hours_to_critical": 11.5},
    }  # fmt: skip
    params = AlertStore.params(alert)
    assert params["details"].obj["estimated_hours_to_critical"] == 11.5
    assert params["details"].obj["threshold"] == 0.08
