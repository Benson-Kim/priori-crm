import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.common.database import check_database_connection, get_pool_status
from app.common.dependencies import verify_internal_secret
from app.common.financial import TAX_RATES
from app.lib.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    environment: str
    timestamp: datetime


class DetailedHealthResponse(BaseModel):
    status: str = Field(description="Overall health status (healthy, degraded)")
    version: str = Field(description="API version")
    environment: str = Field(description="Current deployment environment")
    timestamp: datetime = Field(description="Current server time (UTC)")
    database: dict = Field(description="Database connectivity metrics")
    pool: dict = Field(description="Connection pool statistics")
    redis: dict | None = Field(
        default=None, description="Redis connectivity (if configured)"
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Basic health check",
    description="Quick health check for load balancers.",
)
def health_check() -> HealthResponse:
    """
    Basic health check endpoint.

    Returns application status and version.
    Suitable for load balancer health checks.
    """
    return HealthResponse(
        status="healthy",
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        timestamp=datetime.now(UTC),
    )


def check_redis_connection() -> bool:
    """Check Redis connectivity."""
    if settings.RATE_LIMIT_BACKEND == "redis" and settings.REDIS_URL:
        try:
            import redis

            r = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=1)
            return r.ping()
        except Exception:
            return False
    return False


@router.get(
    "/health/detailed",
    response_model=DetailedHealthResponse,
    summary="Detailed health check",
    description=(
        "Comprehensive health check including database connectivity and "
        "connection-pool internals. Requires the internal machine-to-machine "
        "secret (MW-SEC-3) since it exposes infrastructure details."
    ),
    dependencies=[Depends(verify_internal_secret)],
)
def detailed_health_check() -> DetailedHealthResponse:
    """
    Detailed health check with dependency status.

    Checks:
    - Application status
    - Database connectivity
    - Connection pool status
    """
    db_connected = check_database_connection()
    pool_status = get_pool_status()

    redis_info = None
    if settings.RATE_LIMIT_BACKEND == "redis":
        redis_info = {"connected": check_redis_connection()}

    return DetailedHealthResponse(
        status="healthy" if db_connected else "degraded",
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        timestamp=datetime.now(UTC),
        database={
            "connected": db_connected,
            "type": "postgresql",
        },
        pool=pool_status,
        redis=redis_info,
    )


@router.get(
    "/ping",
    status_code=status.HTTP_200_OK,
    summary="Simple ping",
    description="Minimal endpoint for uptime checks.",
)
def ping() -> dict:
    """Minimal ping endpoint."""
    return {"ping": "pong"}


@router.get(
    "/taxes",
    status_code=status.HTTP_200_OK,
    summary="Get tax configuration",
    description="Retrieve system tax rates for frontend calculation.",
)
def get_tax_rates() -> dict[str, float]:
    """Get tax rates from the single source of truth."""
    return {k.value: float(v) for k, v in TAX_RATES.items()}
