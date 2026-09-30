import pytest

from prognos_stream.dedup import SequenceWindow, Verdict


def test_in_order_stream_is_all_new() -> None:
    w = SequenceWindow(64)
    assert [w.check("v", s) for s in range(1, 6)] == [Verdict.NEW] * 5


def test_exact_duplicate_detected() -> None:
    w = SequenceWindow(64)
    w.check("v", 10)
    assert w.check("v", 10) is Verdict.DUPLICATE


def test_out_of_order_accepted_once() -> None:
    w = SequenceWindow(64)
    for s in (1, 2, 5, 6):
        w.check("v", s)
    assert w.check("v", 4) is Verdict.NEW_LATE
    assert w.check("v", 4) is Verdict.DUPLICATE
    assert w.check("v", 3) is Verdict.NEW_LATE


def test_outside_window_is_too_old() -> None:
    w = SequenceWindow(8)
    w.check("v", 100)
    assert w.check("v", 92) is Verdict.TOO_OLD
    assert w.check("v", 93) is Verdict.NEW_LATE


def test_large_jump_resets_bitmap() -> None:
    w = SequenceWindow(8)
    w.check("v", 1)
    assert w.check("v", 1_000) is Verdict.NEW
    assert w.check("v", 999) is Verdict.NEW_LATE


def test_vehicles_are_independent() -> None:
    w = SequenceWindow(8)
    w.check("a", 5)
    assert w.check("b", 5) is Verdict.NEW
    assert len(w) == 2


def test_width_must_be_positive() -> None:
    with pytest.raises(ValueError):
        SequenceWindow(0)
