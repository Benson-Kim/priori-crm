"""Custom middleware for request tracking, rate limiting, and monitoring."""
import logging
import time
import uuid
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.common.exceptions import RateLimitException
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    In-memory rate limiting with LRU-bounded client cache.
    Uses OrderedDict capped at MAX_CLIENTS entries;
    the least-recently-seen client is evicted when the cap is reached.
    """

    MAX_CLIENTS = 1024

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.requests: OrderedDict[str, list[datetime]] = OrderedDict()
        self.window = timedelta(minutes=1)
        self.max_requests = settings.RATE_LIMIT_PER_MINUTE

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Check rate limit and process request."""
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        # Use client IP as identifier (use user ID in production)
        client_id = request.client.host if request.client else "unknown"
        now = datetime.now()

        # Prune expired timestamps for this client
        if client_id in self.requests:
            self.requests[client_id] = [
                req_time for req_time in self.requests[client_id]
                if now - req_time < self.window
            ]
            # Move to end (most-recently-seen)
            self.requests.move_to_end(client_id)
        else:
            self.requests[client_id] = []

        # Evict oldest client if cache is full
        while len(self.requests) > self.MAX_CLIENTS:
            self.requests.popitem(last=False)

        # Check rate limit
        if len(self.requests[client_id]) >= self.max_requests:
            logger.warning(
                f"Rate limit exceeded for {client_id}",
                extra={
                    "client_id": client_id,
                    "path": request.url.path,
                    "requests_count": len(self.requests[client_id]),
                },
            )
            raise RateLimitException(retry_after=60)

        # Record this request
        self.requests[client_id].append(now)

        return await call_next(request)