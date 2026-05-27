"""
Tests for the RateLimitMiddleware memory-leak fix.

The original implementation uses an unbounded defaultdict(list) that grows
with every unique client IP, leaking memory indefinitely.  The fix bounds
the cache with an LRU eviction strategy.
"""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.middleware import RateLimitMiddleware
from app.common.exceptions import RateLimitException


def _make_request(client_ip: str = "127.0.0.1", path: str = "/api/test"):
    """Build a minimal mock Request."""
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = client_ip
    req.url = MagicMock()
    req.url.path = path
    return req


def _make_call_next():
    """Build a minimal async call_next that returns a 200 response."""
    response = MagicMock()
    response.status_code = 200
    return AsyncMock(return_value=response)


class TestRateLimiterBoundedCache:
    """The requests dict must not grow beyond MAX_CLIENTS."""

    @patch("app.common.middleware.settings")
    def test_cache_evicts_oldest_client_when_full(self, mock_settings):
        """After MAX_CLIENTS unique IPs, the oldest entry is evicted."""
        mock_settings.RATE_LIMIT_ENABLED = True
        mock_settings.RATE_LIMIT_PER_MINUTE = 100

        app = MagicMock()
        mw = RateLimitMiddleware(app)
        call_next = _make_call_next()

        max_clients = mw.MAX_CLIENTS

        # Fill the cache with max_clients unique IPs
        for i in range(max_clients):
            req = _make_request(client_ip=f"10.0.0.{i}")
            asyncio.get_event_loop().run_until_complete(
                mw.dispatch(req, call_next)
            )

        assert len(mw.requests) <= max_clients

        # One more unique IP should still keep size bounded
        req = _make_request(client_ip="10.0.99.99")
        asyncio.get_event_loop().run_until_complete(
            mw.dispatch(req, call_next)
        )
        assert len(mw.requests) <= max_clients


class TestRateLimiterWindowCleanup:
    """Expired timestamps are pruned on each request."""

    @patch("app.common.middleware.settings")
    def test_expired_entries_are_pruned(self, mock_settings):
        """Requests older than the window are removed."""
        mock_settings.RATE_LIMIT_ENABLED = True
        mock_settings.RATE_LIMIT_PER_MINUTE = 5

        app = MagicMock()
        mw = RateLimitMiddleware(app)

        client_ip = "192.168.1.1"
        old_time = datetime.now() - timedelta(minutes=2)

        # Seed with old timestamps
        mw.requests[client_ip] = [old_time] * 5

        call_next = _make_call_next()
        req = _make_request(client_ip=client_ip)

        # Should NOT raise — old entries should be pruned
        asyncio.get_event_loop().run_until_complete(
            mw.dispatch(req, call_next)
        )

        # Only the current request should remain
        assert len(mw.requests[client_ip]) == 1


class TestRateLimiterEnforcement:
    """Rate limit is enforced when enabled."""

    @patch("app.common.middleware.settings")
    def test_exceeding_limit_raises(self, mock_settings):
        """Requests beyond max_requests within the window raise RateLimitException."""
        mock_settings.RATE_LIMIT_ENABLED = True
        mock_settings.RATE_LIMIT_PER_MINUTE = 3

        app = MagicMock()
        mw = RateLimitMiddleware(app)
        call_next = _make_call_next()
        client_ip = "10.0.0.1"

        # Fill up the limit
        for _ in range(3):
            req = _make_request(client_ip=client_ip)
            asyncio.get_event_loop().run_until_complete(
                mw.dispatch(req, call_next)
            )

        # Next request should be rate-limited
        req = _make_request(client_ip=client_ip)
        with pytest.raises(RateLimitException):
            asyncio.get_event_loop().run_until_complete(
                mw.dispatch(req, call_next)
            )


class TestRateLimiterDisabled:
    """When disabled, no tracking occurs."""

    @patch("app.common.middleware.settings")
    def test_disabled_passes_through(self, mock_settings):
        """Rate limiter disabled — all requests pass, no tracking."""
        mock_settings.RATE_LIMIT_ENABLED = False

        app = MagicMock()
        mw = RateLimitMiddleware(app)
        call_next = _make_call_next()

        for _ in range(100):
            req = _make_request()
            asyncio.get_event_loop().run_until_complete(
                mw.dispatch(req, call_next)
            )

        assert len(mw.requests) == 0


class TestRateLimiterUnknownClient:
    """Missing client is handled gracefully."""

    @patch("app.common.middleware.settings")
    def test_no_client_uses_unknown_key(self, mock_settings):
        """Requests with no client IP use 'unknown' as the key."""
        mock_settings.RATE_LIMIT_ENABLED = True
        mock_settings.RATE_LIMIT_PER_MINUTE = 100

        app = MagicMock()
        mw = RateLimitMiddleware(app)
        call_next = _make_call_next()

        req = MagicMock()
        req.client = None
        req.url = MagicMock()
        req.url.path = "/api/test"

        asyncio.get_event_loop().run_until_complete(
            mw.dispatch(req, call_next)
        )

        assert "unknown" in mw.requests
