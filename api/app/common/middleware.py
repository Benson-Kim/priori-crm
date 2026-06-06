"""Custom middleware for request tracking, rate limiting, and monitoring."""
import logging
import time
import uuid
from typing import Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.common.rate_limit_store import (
    InMemoryRateLimitStore,
    build_rate_limit_store,
)
from app.common.security import decode_access_token
from app.lib.config import settings

logger = logging.getLogger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Add unique request ID to each request for tracing."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and add request ID."""
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all HTTP requests with timing information."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log request details and response time."""
        start_time = time.time()
        request_id = getattr(request.state, "request_id", "unknown")
        
        # Log request
        logger.info(
            f"{request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "query_params": dict(request.query_params),
                "client": request.client.host if request.client else None,
            },
        )
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000
        
        # Log response
        log_level = logging.INFO if response.status_code < 400 else logging.WARNING
        logger.log(
            log_level,
            f"{request.method} {request.url.path} - {response.status_code} ({duration_ms:.2f}ms)",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        
        # Add timing header
        response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"
        
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach standard security headers to every response (MW-SEC-1).

    Sets nosniff, frame denial, a conservative referrer policy and CSP, and
    — outside development — HSTS. Applied as the outermost middleware so the
    headers are present even on error and throttled responses.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        # API serves JSON, never embeds; deny framing and inline content.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'",
        )
        # HSTS only when not in development (staging/production run over TLS).
        if settings.ENVIRONMENT != "development":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-client request throttling over a pluggable counter backend (W-4).

    The window is owned by a RateLimitStore: an in-memory LRU-bounded
    sliding window per process (default), or a shared Redis fixed window so
    the limit is not multiplied by worker/instance count under scaling. This
    class keeps client-identity resolution, exempt paths, and the clean
    429 + Retry-After response; counting is delegated to the store.
    """

    MAX_CLIENTS = 1024

    # Probe endpoints that must never be throttled: a burst of load-balancer
    # or uptime checks would otherwise flap the service. Matched as path
    # suffixes so the API prefix (e.g. /api/v1) does not need hardcoding.
    EXEMPT_PATH_SUFFIXES = ("/health", "/health/detailed", "/ping")

    WINDOW_SECONDS = 60

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.max_requests = settings.RATE_LIMIT_PER_MINUTE
        # Pluggable counter backend (W-4): per-process in-memory by default,
        # or a shared Redis window when RATE_LIMIT_BACKEND=redis.
        self._store = build_rate_limit_store(
            backend=settings.RATE_LIMIT_BACKEND,
            redis_url=settings.REDIS_URL,
            max_clients=self.MAX_CLIENTS,
        )

    @property
    def requests(self):
        """Backward-compatible view of the in-memory window (tests/metrics).

        Only meaningful for the in-memory backend; empty mapping otherwise.
        """
        if isinstance(self._store, InMemoryRateLimitStore):
            return self._store.requests
        return {}

    def _is_exempt(self, path: str) -> bool:
        """Health/ping probes bypass the limiter."""
        return path.endswith(self.EXEMPT_PATH_SUFFIXES)

    def _client_identity(self, request: Request) -> str:
        """Resolve a stable per-client bucket key.

        Order of preference:
        1. Authenticated user (JWT `sub`) - survives shared/proxy IPs.
        2. First hop of X-Forwarded-For, but only when explicitly trusted
           (behind a known proxy); the header is otherwise spoofable.
        3. Raw socket IP.
        """
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            if token:
                try:
                    from fastapi.security import HTTPAuthorizationCredentials

                    payload = decode_access_token(
                        HTTPAuthorizationCredentials(
                            scheme="Bearer", credentials=token
                        )
                    )
                    sub = payload.get("sub")
                    if sub:
                        return f"user:{sub}"
                except Exception:
                    # Invalid/expired token: fall through to IP-based identity.
                    pass

        if settings.RATE_LIMIT_TRUST_FORWARDED_FOR:
            forwarded = request.headers.get("X-Forwarded-For", "")
            if forwarded:
                first_hop = forwarded.split(",")[0].strip()
                if first_hop:
                    return f"ip:{first_hop}"

        return f"ip:{request.client.host if request.client else 'unknown'}"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Check rate limit and process request."""
        if not settings.RATE_LIMIT_ENABLED or self._is_exempt(request.url.path):
            return await call_next(request)

        client_id = self._client_identity(request)

        # The store owns the window accounting (per-process or shared Redis).
        result = self._store.hit(
            client_id, self.max_requests, self.WINDOW_SECONDS
        )

        if not result.allowed:
            logger.warning(
                f"Rate limit exceeded for {client_id}",
                extra={
                    "client_id": client_id,
                    "path": request.url.path,
                    "backend": settings.RATE_LIMIT_BACKEND,
                },
            )
            # Return the response directly rather than raising: a
            # BaseHTTPMiddleware runs outside the AppException handler, so
            # raising here would surface as an unhandled 500 and drop the
            # tracing headers (W-1/W-2/W-3). The Retry-After header lets
            # compliant clients back off correctly.
            request_id = getattr(request.state, "request_id", "unknown")
            retry_after = result.retry_after or self.WINDOW_SECONDS
            # Stamp tracing headers directly: depending on middleware order
            # this early return may not pass back through the request-ID /
            # logging middleware, so a throttled response must carry them
            # itself (W-2/W-3).
            headers = {
                "Retry-After": str(retry_after),
                "X-Request-ID": request_id,
            }
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers=headers,
                content={
                    "error": "Rate limit exceeded",
                    "error_code": "RateLimitException",
                    "status_code": status.HTTP_429_TOO_MANY_REQUESTS,
                    "request_id": request_id,
                    "details": {"retry_after": retry_after},
                },
            )

        return await call_next(request)