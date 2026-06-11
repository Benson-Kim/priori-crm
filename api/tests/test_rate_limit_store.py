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


class _FakePipeline:
    def __init__(self, store, fail=False):
        self._store = store
        self._fail = fail
        self._key = None

    def incr(self, key):
        self._key = key
        return self

    def expire(self, key, seconds):
        return self

    def execute(self):
        if self._fail:
            raise ConnectionError("redis down")
        self._store[self._key] = self._store.get(self._key, 0) + 1
        return [self._store[self._key], True]


class _FakeRedis:
    def __init__(self, fail=False):
        self._store = {}
        self._fail = fail

    def pipeline(self):
        return _FakePipeline(self._store, fail=self._fail)


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
