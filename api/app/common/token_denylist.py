"""Refresh-token revocation denylist.

Refresh tokens are long-lived and stateless, so logout and rotation need a way
to invalidate a *specific* token before its natural expiry. We track revoked
token ids (``jti``) in a denylist keyed by jti, with a TTL equal to the token's
remaining lifetime so entries clean themselves up.

The design mirrors ``rate_limit_store``: a pluggable backend selected from the
existing ``RATE_LIMIT_BACKEND`` / ``REDIS_URL`` settings, so a multi-worker /
multi-instance deployment shares one denylist (Redis) while local/dev stays
in-process. Reads fail OPEN on backend errors (consistent with the rate-limit
store) and log loudly: a denylist outage degrades to "revocation temporarily
not enforced" rather than locking every refresh out.
"""

import logging
import time
from typing import Protocol

logger = logging.getLogger(__name__)

_KEY_PREFIX = "revoked_jti"


class TokenDenylist(Protocol):
    """Records and checks revoked refresh-token ids (jti)."""

    def revoke(self, jti: str, ttl_seconds: int) -> None: ...

    def is_revoked(self, jti: str) -> bool: ...


class InMemoryTokenDenylist:
    """Per-process denylist: jti -> unix expiry. Suitable for a single worker.

    Not shared across processes; use the Redis backend for horizontal scaling.
    Expired entries are pruned lazily on access.
    """

    def __init__(self) -> None:
        self._revoked: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        expired = [jti for jti, exp in self._revoked.items() if exp <= now]
        for jti in expired:
            del self._revoked[jti]

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        now = time.time()
        self._prune(now)
        self._revoked[jti] = now + max(1, ttl_seconds)

    def is_revoked(self, jti: str) -> bool:
        now = time.time()
        exp = self._revoked.get(jti)
        if exp is None:
            return False
        if exp <= now:
            # Expired: token would be rejected by exp validation anyway.
            del self._revoked[jti]
            return False
        return True


class RedisTokenDenylist:
    """Shared denylist backed by Redis.

    Stores one key per revoked jti with a TTL equal to the token's remaining
    lifetime, so the set of revoked ids is bounded by the live-token window.

    Fails OPEN: if Redis is unreachable, ``is_revoked`` returns False (and logs)
    so a cache outage degrades to "revocation not enforced" rather than a hard
    outage of every refresh. ``revoke`` failures are logged and swallowed.
    """

    def __init__(self, redis_url: str) -> None:
        import redis  # imported lazily so redis is only needed for this backend

        self._redis = redis.Redis.from_url(
            redis_url, socket_timeout=0.25, socket_connect_timeout=0.25
        )

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        try:
            self._redis.set(f"{_KEY_PREFIX}:{jti}", "1", ex=max(1, ttl_seconds))
        except Exception as exc:
            logger.error(
                "Token denylist Redis unavailable on revoke; jti not persisted",
                exc_info=exc,
            )

    def is_revoked(self, jti: str) -> bool:
        try:
            return self._redis.exists(f"{_KEY_PREFIX}:{jti}") > 0
        except Exception as exc:
            logger.error(
                "Token denylist Redis unavailable on check; failing open",
                exc_info=exc,
            )
            return False


def build_token_denylist(backend: str, redis_url: str) -> TokenDenylist:
    """Construct the configured denylist backend."""
    if backend == "redis":
        return RedisTokenDenylist(redis_url)
    return InMemoryTokenDenylist()
