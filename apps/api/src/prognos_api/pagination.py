"""Keyset (cursor) pagination.

OFFSET pagination re-reads and discards every skipped row (O(offset)) and shows
duplicates or gaps when rows are inserted between pages. Keyset pagination seeks
straight to the last seen sort key with an index (O(log n + page)), and is stable
under inserts. The cursor is opaque to clients (base64 JSON of the last key).
"""

from __future__ import annotations

import base64
import json
from typing import Any

from prognos_api.errors import ApiError

MAX_LIMIT = 200


def encode(key: list[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(key, default=str).encode()).decode().rstrip("=")


def decode(cursor: str | None, size: int) -> list[Any] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        key = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ApiError(400, "invalid cursor", code="invalid-cursor") from exc
    if not isinstance(key, list) or len(key) != size:
        raise ApiError(400, "invalid cursor", code="invalid-cursor")
    return key


def page(rows: list[dict[str, Any]], limit: int, key: list[str]) -> dict[str, Any]:
    """`rows` was fetched with LIMIT limit + 1: the extra row only signals a next page."""
    more = len(rows) > limit
    items = rows[:limit]
    return {
        "items": items,
        "next_cursor": encode([items[-1][k] for k in key]) if more and items else None,
    }
