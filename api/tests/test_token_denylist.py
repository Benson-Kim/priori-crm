"""Unit tests for the in-memory refresh-token denylist"""

import threading
import time

from app.common.token_denylist import InMemoryTokenDenylist, build_token_denylist


def test_revoke_then_is_revoked():
    store = InMemoryTokenDenylist()
    assert store.is_revoked("jti-1") is False
    store.revoke("jti-1", ttl_seconds=60)
    assert store.is_revoked("jti-1") is True


def test_unknown_jti_is_not_revoked():
    store = InMemoryTokenDenylist()
    store.revoke("jti-1", ttl_seconds=60)
    assert store.is_revoked("jti-other") is False


def test_entry_expires_after_ttl():
    store = InMemoryTokenDenylist()
    # A non-positive ttl is clamped to 1s; sleep just past it.
    store.revoke("jti-exp", ttl_seconds=0)
    assert store.is_revoked("jti-exp") is True
    time.sleep(1.1)
    assert store.is_revoked("jti-exp") is False


def test_build_defaults_to_in_memory():
    store = build_token_denylist(backend="memory", redis_url="")
    assert isinstance(store, InMemoryTokenDenylist)


def test_build_unknown_backend_falls_back_to_in_memory():
    store = build_token_denylist(backend="something-else", redis_url="")
    assert isinstance(store, InMemoryTokenDenylist)


# revoke_if_new: the atomic "spend" primitive (#44)


def test_revoke_if_new_reports_first_spend_only():
    store = InMemoryTokenDenylist()
    assert store.revoke_if_new("jti-spend", ttl_seconds=60) is True
    assert store.revoke_if_new("jti-spend", ttl_seconds=60) is False
    assert store.is_revoked("jti-spend") is True


def test_revoke_if_new_after_expiry_is_new_again():
    store = InMemoryTokenDenylist()
    store.revoke_if_new("jti-exp2", ttl_seconds=0)  # clamped to 1s
    time.sleep(1.1)
    assert store.revoke_if_new("jti-exp2", ttl_seconds=60) is True


def test_revoke_if_new_sees_plain_revoke():
    """A jti revoked via logout (revoke) is already spent for rotation."""
    store = InMemoryTokenDenylist()
    store.revoke("jti-logout", ttl_seconds=60)
    assert store.revoke_if_new("jti-logout", ttl_seconds=60) is False


def test_revoke_if_new_thread_race_has_single_winner():
    """Atomicity: N threads racing one jti -> exactly one True."""
    store = InMemoryTokenDenylist()
    n = 16
    barrier = threading.Barrier(n)
    results: list[bool] = []

    def worker() -> None:
        barrier.wait()
        results.append(store.revoke_if_new("jti-race", ttl_seconds=60))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == n
    assert results.count(True) == 1
    assert results.count(False) == n - 1
