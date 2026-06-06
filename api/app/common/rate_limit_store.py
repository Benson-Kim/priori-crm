"""Rate-limit counter backends (W-4).

The middleware delegates counting to a ``RateLimitStore`` so the window can
be either per-process (in-memory, default) or shared across all workers and
instances (Redis). Sharing the window is what stops the effective limit from
being multiplied by ``workers x instances`` under horizontal scaling.
"""
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RateLimitResult:
    """Outcome of recording one hit against a client's window."""

    allowed: bool
    retry_after: int  # seconds until the window resets (only meaningful when blocked)


class RateLimitStore(Protocol):
    """Records a request against a client window and reports if it's allowed."""

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult: ...


class InMemoryRateLimitStore:
    """Per-process sliding-window counter with an LRU-bounded client cache.

    Identical semantics to the original middleware: a deque of timestamps per
    client, pruned to the window on each hit, with the least-recently-seen
    client evicted once ``max_clients`` is reached. Suitable for a single
    worker or local development; not shared across processes.
    """

    def __init__(self, max_clients: int = 1024) -> None:
        self.max_clients = max_clients
        self._requests: OrderedDict[str, list[datetime]] = OrderedDict()

    @property
    def requests(self) -> OrderedDict[str, list[datetime]]:
        """Exposed for tests/observability."""
        return self._requests

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = datetime.now()
        window = timedelta(seconds=window_seconds)

        if key in self._requests:
            self._requests[key] = [
                ts for ts in self._requests[key] if now - ts < window
            ]
            self._requests.move_to_end(key)
        else:
            self._requests[key] = []

        while len(self._requests) > self.max_clients:
            self._requests.popitem(last=False)

        if len(self._requests[key]) >= limit:
            return RateLimitResult(allowed=False, retry_after=window_seconds)

        self._requests[key].append(now)
        return RateLimitResult(allowed=True, retry_after=0)


class RedisRateLimitStore:
    """Shared fixed-window counter backed by Redis (W-4).

    Uses an atomic ``INCR`` on a per-(client, window) key with a TTL equal to
    the window, so every worker/instance increments the same counter. The
    fixed window is simple and race-free; a brief boundary burst (up to ~2x
    at a window edge) is an accepted trade-off versus the operational cost of
    a sliding-window Lua script.

    Fails OPEN: if Redis is unreachable the request is allowed (and the error
    logged) so a cache outage degrades to "no limiting" rather than a hard
    outage of the whole API.
    """

    def __init__(self, redis_url: str) -> None:
        import redis  # imported lazily so redis is only needed for this backend

        self._redis = redis.Redis.from_url(
            redis_url, socket_timeout=0.25, socket_connect_timeout=0.25
        )
        self._key_prefix = "ratelimit"

    def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        # Bucket the key by the current fixed window so the TTL and counter
        # reset together.
        window_id = int(time.time()) // window_seconds
        redis_key = f"{self._key_prefix}:{key}:{window_id}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds)
            count, _ = pipe.execute()
            count = int(count)
        except Exception as exc:  # noqa: BLE001 - never let Redis break requests
            logger.warning(
                "Rate-limit Redis backend unavailable; failing open",
                exc_info=exc,
            )
            return RateLimitResult(allowed=True, retry_after=0)

        if count > limit:
            retry_after = window_seconds - (int(time.time()) % window_seconds)
            return RateLimitResult(allowed=False, retry_after=max(1, retry_after))
        return RateLimitResult(allowed=True, retry_after=0)


def build_rate_limit_store(
    backend: str, redis_url: str, max_clients: int = 1024
) -> RateLimitStore:
    """Construct the configured counter store."""
    if backend == "redis":
        return RedisRateLimitStore(redis_url)
    return InMemoryRateLimitStore(max_clients=max_clients)
