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
import threading
import time
from functools import lru_cache
from typing import Protocol

logger = logging.getLogger(__name__)

_KEY_PREFIX = "revoked_jti"


_FENCE_PREFIX = "token_fence"

#: Key prefix for terminated sessions: every live ACCESS token carrying
#: this ``sid`` is refused on the access-token validation path (#67
#: review F5).
_SESSION_PREFIX = "dead_sid"


class TokenDenylist(Protocol):
    """Records and checks revoked refresh-token ids (jti).

    ``revoke_if_new`` is the atomic "spend" primitive used by token
    rotation: a single revoke-and-report operation, so exactly one
    concurrent presenter of a given jti observes ``True``. A separate
    ``is_revoked`` check followed by ``revoke`` is NOT atomic and must
    never be used to gate rotation.

    Also stores per-key "fence" timestamps used for family revocation:
    a refresh token whose ``iat`` is at or before the fence is
    rejected, so detecting reuse of one revoked token can invalidate every
    outstanding token in that user's rotation chain at once.
    """

    def revoke(self, jti: str, ttl_seconds: int) -> None: ...

    def revoke_if_new(self, jti: str, ttl_seconds: int) -> bool: ...

    def is_revoked(self, jti: str) -> bool: ...

    def set_fence(self, key: str, timestamp: float, ttl_seconds: int) -> None: ...

    def get_fence(self, key: str) -> float | None: ...


class InMemoryTokenDenylist:
    """Per-process denylist: jti -> unix expiry. Suitable for a single worker.

    Not shared across processes; use the Redis backend for horizontal scaling.
    Expired entries are pruned lazily on access. All operations hold a single
    process-wide lock, so ``revoke_if_new`` is atomic across threads and the
    dict mutations in the other methods are race-free.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revoked: dict[str, float] = {}
        self._fences: dict[str, tuple[float, float]] = {}  # key -> (value, expiry)

    def _prune(self, now: float) -> None:
        """Drop expired entries. The caller must hold ``self._lock``."""
        expired = [jti for jti, exp in self._revoked.items() if exp <= now]
        for jti in expired:
            del self._revoked[jti]
        expired_fences = [k for k, (_, exp) in self._fences.items() if exp <= now]
        for k in expired_fences:
            del self._fences[k]

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        now = time.time()
        with self._lock:
            self._prune(now)
            self._revoked[jti] = now + max(1, ttl_seconds)

    def revoke_if_new(self, jti: str, ttl_seconds: int) -> bool:
        """Atomically revoke ``jti``; report whether it was newly revoked.

        The check-and-insert runs under the lock, mirroring Redis
        ``SET ... NX EX``: for a given live jti, exactly one concurrent
        caller gets ``True``; every other caller gets ``False``.
        """
        now = time.time()
        with self._lock:
            self._prune(now)
            exp = self._revoked.get(jti)
            if exp is not None and exp > now:
                return False  # already spent by an earlier or concurrent caller
            self._revoked[jti] = now + max(1, ttl_seconds)
            return True

    def is_revoked(self, jti: str) -> bool:
        now = time.time()
        with self._lock:
            exp = self._revoked.get(jti)
            if exp is None:
                return False
            if exp <= now:
                # Expired: token would be rejected by exp validation anyway.
                del self._revoked[jti]
                return False
            return True

    def set_fence(self, key: str, timestamp: float, ttl_seconds: int) -> None:
        now = time.time()
        with self._lock:
            self._prune(now)
            self._fences[key] = (timestamp, now + max(1, ttl_seconds))

    def get_fence(self, key: str) -> float | None:
        now = time.time()
        with self._lock:
            entry = self._fences.get(key)
            if entry is None:
                return None
            value, exp = entry
            if exp <= now:
                del self._fences[key]
                return None
            return value


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

        self._fallback = InMemoryTokenDenylist()
        self._redis: redis.Redis | None = None

        # Handle empty or invalid redis_url gracefully
        if not redis_url or not redis_url.strip():
            logger.warning(
                "RedisTokenDenylist initialized with empty redis_url; "
                "will use in-memory fallback for all operations"
            )
            self._redis = None
        else:
            try:
                self._redis = redis.Redis.from_url(
                    redis_url, socket_timeout=0.25, socket_connect_timeout=0.25
                )
            except (ValueError, redis.RedisError) as exc:
                logger.error(
                    "Failed to initialize Redis connection with url=%s; "
                    "will use in-memory fallback for all operations",
                    redis_url,
                    exc_info=exc,
                )
                self._redis = None

    def revoke(self, jti: str, ttl_seconds: int) -> None:
        if self._redis is None:
            self._fallback.revoke(jti, ttl_seconds)
            return
        try:
            self._redis.set(f"{_KEY_PREFIX}:{jti}", "1", ex=max(1, ttl_seconds))
        except Exception as exc:
            logger.error(
                "Token denylist Redis unavailable on revoke; jti not persisted",
                exc_info=exc,
            )

    def revoke_if_new(self, jti: str, ttl_seconds: int) -> bool:
        """Atomically revoke ``jti``; report whether it was newly revoked.

        ``SET key value NX EX ttl`` is a single atomic revoke-and-report:
        Redis returns True when the key was newly set and None when the key
        already exists (the jti was already spent), so exactly one
        concurrent presenter of a given jti can win across all workers.

        Fails OPEN like the other reads: if Redis is unreachable this
        returns True (and logs), so an outage degrades to "reuse detection
        temporarily not enforced" rather than rejecting every refresh.
        """
        if self._redis is None:
            return self._fallback.revoke_if_new(jti, ttl_seconds)
        try:
            return bool(
                self._redis.set(
                    f"{_KEY_PREFIX}:{jti}", "1", nx=True, ex=max(1, ttl_seconds)
                )
            )
        except Exception as exc:
            logger.error(
                "Token denylist Redis unavailable on revoke_if_new; failing open",
                exc_info=exc,
            )
            return True

    def is_revoked(self, jti: str) -> bool:
        if self._redis is None:
            return self._fallback.is_revoked(jti)
        try:
            return self._redis.exists(f"{_KEY_PREFIX}:{jti}") > 0  # type: ignore[operator]
        except Exception as exc:
            logger.error(
                "Token denylist Redis unavailable on check; failing open",
                exc_info=exc,
            )
            return False

    def set_fence(self, key: str, timestamp: float, ttl_seconds: int) -> None:
        if self._redis is None:
            self._fallback.set_fence(key, timestamp, ttl_seconds)
            return
        try:
            self._redis.set(
                f"{_FENCE_PREFIX}:{key}", repr(timestamp), ex=max(1, ttl_seconds)
            )
        except Exception as exc:
            logger.error(
                "Token denylist Redis unavailable on set_fence; fence not persisted",
                exc_info=exc,
            )

    def get_fence(self, key: str) -> float | None:
        if self._redis is None:
            return self._fallback.get_fence(key)
        try:
            raw = self._redis.get(f"{_FENCE_PREFIX}:{key}")
            return float(raw) if raw is not None else None  # type: ignore[arg-type]
        except Exception as exc:
            logger.error(
                "Token denylist Redis unavailable on get_fence; failing open",
                exc_info=exc,
            )
            return None


def build_token_denylist(backend: str, redis_url: str) -> TokenDenylist:
    """Construct the configured denylist backend."""
    if backend == "redis":
        return RedisTokenDenylist(redis_url)
    return InMemoryTokenDenylist()


@lru_cache(maxsize=1)
def shared_token_denylist() -> TokenDenylist:
    """The process-wide denylist singleton (TOKEN_DENYLIST_BACKEND).

    One instance for refresh-token revocation (auth service), family
    fences AND terminated-session access revocation, so all three views
    agree within a process — and across workers on the Redis backend.
    """
    from app.lib.config import settings

    return build_token_denylist(
        backend=settings.TOKEN_DENYLIST_BACKEND,
        redis_url=settings.REDIS_URL,
    )


def revoke_session_access(session_id) -> None:
    """Deny every live ACCESS token of a terminated session (#67 review F5).

    Refresh-token revocation alone leaves already-issued access tokens
    valid until natural expiry — up to JWT_ACCESS_TOKEN_EXPIRE_MINUTES of
    post-compromise access. Keying the denylist by ``sid`` kills them all
    at once: no new access token can carry this sid either, because a
    terminated session refuses refresh before minting. The TTL is the
    access-token lifetime plus a skew margin, after which every such
    token is expired anyway and the entry cleans itself up.

    Called from every place a session terminates (risk terminate, session
    expiry, refresh-reuse detection, logout, password reset, failed
    step-ups). Idempotent and fail-safe: revoking is best-effort on a
    backend outage (consistent with the denylist's fail-open posture).
    """
    from app.lib.config import settings

    ttl = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60 + 60
    shared_token_denylist().revoke(f"{_SESSION_PREFIX}:{session_id}", ttl)


def is_session_access_revoked(session_id) -> bool:
    """Whether a session's access tokens have been revoked via its sid."""
    return shared_token_denylist().is_revoked(f"{_SESSION_PREFIX}:{session_id}")
