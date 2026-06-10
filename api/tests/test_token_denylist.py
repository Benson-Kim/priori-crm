"""Unit tests for the in-memory refresh-token denylist (ISSUE-039)."""

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
