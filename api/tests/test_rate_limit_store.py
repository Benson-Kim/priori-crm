"""rate-limit counter backends."""

from app.common.rate_limit_store import (
    InMemoryRateLimitStore,
    RedisRateLimitStore,
)


class TestInMemoryStore:
    def test_allows_up_to_limit_then_blocks(self):
        store = InMemoryRateLimitStore()
        results = [store.hit("client", limit=3, window_seconds=60) for _ in range(4)]
        assert [r.allowed for r in results] == [True, True, True, False]
        assert results[-1].retry_after == 60

    def test_lru_eviction_bounds_cache(self):
        store = InMemoryRateLimitStore(max_clients=2)
        store.hit("a", limit=10, window_seconds=60)
        store.hit("b", limit=10, window_seconds=60)
        store.hit("c", limit=10, window_seconds=60)
        assert len(store.requests) <= 2

    def test_separate_clients_have_separate_windows(self):
        store = InMemoryRateLimitStore()
        assert store.hit("x", limit=1, window_seconds=60).allowed is True
        assert store.hit("x", limit=1, window_seconds=60).allowed is False
        assert store.hit("y", limit=1, window_seconds=60).allowed is True

    def test_peek_reads_without_charging(self):
        """#84: peek reports the window's units and never counts itself."""
        store = InMemoryRateLimitStore()
        assert store.peek("s", window_seconds=60) == 0
        store.hit("s", limit=100, window_seconds=60)
        store.hit("s", limit=100, window_seconds=60, cost=25)
        assert store.peek("s", window_seconds=60) == 26
        # Repeated peeks are free: the window is unchanged.
        for _ in range(5):
            assert store.peek("s", window_seconds=60) == 26


class _FakePipeline:
    def __init__(self, store, fail=False):
        self._store = store
        self._fail = fail
        self._key = None

    def incrby(self, key, amount=1):
        self._key = key
        self._amount = amount
        return self

    def expire(self, key, seconds):
        return self

    def execute(self):
        if self._fail:
            raise ConnectionError("redis down")
        self._store[self._key] = self._store.get(self._key, 0) + self._amount
        return [self._store[self._key], True]


class _FakeRedis:
    def __init__(self, fail=False):
        self._store = {}
        self._flags = {}
        self._fail = fail

    def pipeline(self):
        return _FakePipeline(self._store, fail=self._fail)

    def set(self, key, value, ex=None, nx=None):
        if self._fail:
            raise ConnectionError("redis down")
        self._flags[key] = value
        return True

    def get(self, key):
        if self._fail:
            raise ConnectionError("redis down")
        value = self._store.get(key)
        return None if value is None else str(value)

    def exists(self, key):
        if self._fail:
            raise ConnectionError("redis down")
        return 1 if key in self._flags else 0


def _redis_store_with(fake) -> RedisRateLimitStore:
    # Bypass __init__ (which would import/construct a real client).
    store = RedisRateLimitStore.__new__(RedisRateLimitStore)
    store._redis = fake
    store._key_prefix = "ratelimit"
    store._redis_exc = (ConnectionError, OSError)
    return store


class TestRedisStore:
    def test_shared_counter_blocks_past_limit(self):
        store = _redis_store_with(_FakeRedis())
        results = [store.hit("user:1", limit=2, window_seconds=60) for _ in range(3)]
        assert [r.allowed for r in results] == [True, True, False]
        assert results[-1].retry_after >= 1

    def test_fails_open_when_redis_errors(self):
        store = _redis_store_with(_FakeRedis(fail=True))
        result = store.hit("user:1", limit=1, window_seconds=60)
        assert result.allowed is True

    def test_weighted_cost_uses_incrby(self):
        store = _redis_store_with(_FakeRedis())
        assert store.hit("u", limit=30, window_seconds=60, cost=25).allowed is True
        assert store.hit("u", limit=30, window_seconds=60, cost=25).allowed is False

    def test_flags_roundtrip_and_fail_open(self):
        store = _redis_store_with(_FakeRedis())
        assert store.get_flag("mark") is False
        store.set_flag("mark", ttl_seconds=60)
        assert store.get_flag("mark") is True

        broken = _redis_store_with(_FakeRedis(fail=True))
        assert broken.get_flag("mark") is False  # fails open, never locks out

    def test_peek_reads_without_charging_and_fails_open(self):
        """#84: peek mirrors the fixed-window counter; outages read 0."""
        store = _redis_store_with(_FakeRedis())
        assert store.peek("s", window_seconds=60) == 0
        store.hit("s", limit=100, window_seconds=60, cost=26)
        assert store.peek("s", window_seconds=60) == 26
        assert store.peek("s", window_seconds=60) == 26  # non-charging

        broken = _redis_store_with(_FakeRedis(fail=True))
        assert broken.peek("s", window_seconds=60) == 0  # fails open
