"""Per-vehicle sliding-window duplicate detection (IPsec anti-replay style).

For each vehicle keep the highest sequence number seen (`high`) and a W-bit
bitmap of which of the W sequence numbers at or below `high` have been seen.

    check(seq):
      seq > high          -> shift bitmap left by (seq - high), set bit 0     NEW
      high - seq < W      -> bit (high - seq) set ? DUPLICATE : set it, NEW (late)
      high - seq >= W     -> TOO_OLD (outside the window: cannot tell)

Exact (no false positives, unlike a Bloom filter) for any reordering smaller
than W; the simulator reorders by at most 30 s (~30 sequence numbers at 1 Hz),
so W = 1024 leaves a wide margin. TOO_OLD events are forwarded flagged `late`;
the ReplacingMergeTree in ClickHouse is the backstop for those.

Time O(1) per event (Python int shift/mask on a 1024-bit int);
space O(V * W / 8) bytes: ~13 MB of bitmaps for 100K vehicles.

State is per consumer partition and in memory. After a rebalance or restart the
new owner starts with empty windows, so a redelivered batch can pass once more;
event_id-keyed sinks make that harmless (at-least-once + idempotent sinks).
"""

from __future__ import annotations

from enum import IntEnum


class Verdict(IntEnum):
    NEW = 0
    NEW_LATE = 1  # out of order, but first time seen
    DUPLICATE = 2
    TOO_OLD = 3


class SequenceWindow:
    __slots__ = ("mask", "state", "width")

    def __init__(self, width: int = 1024) -> None:
        if width < 1:
            raise ValueError("width must be positive")
        self.width = width
        self.mask = (1 << width) - 1
        self.state: dict[str, tuple[int, int]] = {}  # key -> (high, bitmap)

    def check(self, key: str, seq: int) -> Verdict:
        current = self.state.get(key)
        if current is None:
            self.state[key] = (seq, 1)
            return Verdict.NEW
        high, bitmap = current
        if seq > high:
            shift = seq - high
            bitmap = 1 if shift >= self.width else ((bitmap << shift) | 1) & self.mask
            self.state[key] = (seq, bitmap)
            return Verdict.NEW
        offset = high - seq
        if offset >= self.width:
            return Verdict.TOO_OLD
        bit = 1 << offset
        if bitmap & bit:
            return Verdict.DUPLICATE
        self.state[key] = (high, bitmap | bit)
        return Verdict.NEW_LATE

    def __len__(self) -> int:
        return len(self.state)
