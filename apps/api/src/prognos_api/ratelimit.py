"""Fixed-window rate limiting in Redis (INCR + EXPIRE, one round trip).

Fixed windows allow a burst of up to 2x the limit across a window boundary; that
is acceptable for abuse protection here and far simpler than a sliding log. If
Redis is unavailable the request is allowed (fail open): the limiter protects
capacity, it is not an authorisation control.
"""

from __future__ import annotations

import logging
import time

import redis.asyncio as aioredis
from redis.exceptions import RedisError

log = logging.getLogger("prognos_api")


class RateLimiter:
    def __init__(self, client: aioredis.Redis) -> None:
        self.client = client

    async def hit(self, key: str, limit: int, window_s: int = 60) -> tuple[bool, int]:
        """Returns (allowed, seconds until the window resets)."""
        now = int(time.time())
        window = now - now % window_s
        redis_key = f"rl:{key}:{window}"
        try:
            pipe = self.client.pipeline(transaction=False)
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_s + 1)
            count, _ = await pipe.execute()
        except RedisError as exc:
            log.warning("rate limiter unavailable, allowing request: %s", exc)
            return True, 0
        return int(count) <= limit, window + window_s - now

    async def count(self, key: str, window_s: int = 60) -> int:
        """Hits so far in the current window (0 if Redis is unavailable)."""
        now = int(time.time())
        try:
            value = await self.client.get(f"rl:{key}:{now - now % window_s}")
        except RedisError:
            return 0
        return int(value or 0)
