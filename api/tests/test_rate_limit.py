"""Integration tests for the rate-limit / 429 path (W1.7, W-1).

These drive the *full* ASGI middleware stack through TestClient. The previous
unit test called RateLimitMiddleware.dispatch() directly, which bypassed the
ASGI stack and hid that a raised exception inside a BaseHTTPMiddleware never
reaches the AppException handler (surfacing as a 500 instead of a clean 429).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.common.exceptions import register_exception_handlers
from app.common.middleware import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    RequestLoggingMiddleware,
)
from app.common.security import create_access_token
from app.modules.health.router import router as health_router


def _build_app(monkeypatch, *, limit: int) -> FastAPI:
    """Build a minimal app wired exactly like main.create_app().

    The limiter reads RATE_LIMIT_PER_MINUTE at construction time, so patch
    settings before adding the middleware. Tracing middleware is registered
    last (outermost) to match production ordering.
    """
    from app.lib.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", limit, raising=False)

    app = FastAPI()

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)
    app.include_router(health_router, prefix="/api/v1")

    @app.get("/api/v1/probe")
    def _probe() -> dict:
        return {"ok": True}

    return app


def test_exceeding_limit_returns_clean_429_with_headers(monkeypatch):
    app = _build_app(monkeypatch, limit=3)
    client = TestClient(app)

    # Exhaust the allowance.
    for _ in range(3):
        ok = client.get("/api/v1/probe")
        assert ok.status_code == 200

    # Next request is throttled.
    resp = client.get("/api/v1/probe")

    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "60"
    # Tracing headers must survive the throttled path.
    assert resp.headers.get("X-Request-ID")
    assert resp.headers.get("X-Response-Time")

    body = resp.json()
    assert body["status_code"] == 429
    assert body["error_code"] == "RateLimitException"
    assert body["details"]["retry_after"] == 60
    assert body["request_id"]


@pytest.mark.parametrize(
    "path",
    ["/api/v1/health", "/api/v1/health/detailed", "/api/v1/ping"],
)
def test_health_and_ping_are_never_throttled(monkeypatch, path):
    app = _build_app(monkeypatch, limit=1)
    from app.lib.config import settings

    monkeypatch.setattr(settings, "INTERNAL_API_SECRET", "test-secret")
    client = TestClient(app)

    headers = {"X-Internal-Secret": "test-secret"} if "detailed" in path else {}

    # Far exceed the limit; probes must always pass.
    for _ in range(5):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200


def test_distinct_users_get_independent_buckets(monkeypatch):
    app = _build_app(monkeypatch, limit=2)
    client = TestClient(app)

    token_a = create_access_token(subject="user-a")
    token_b = create_access_token(subject="user-b")
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}

    # User A exhausts their own allowance from the same socket IP.
    assert client.get("/api/v1/probe", headers=auth_a).status_code == 200
    assert client.get("/api/v1/probe", headers=auth_a).status_code == 200
    assert client.get("/api/v1/probe", headers=auth_a).status_code == 429

    # User B is unaffected despite sharing the IP (W-5).
    assert client.get("/api/v1/probe", headers=auth_b).status_code == 200
    assert client.get("/api/v1/probe", headers=auth_b).status_code == 200
