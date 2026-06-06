import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.common.database import check_database_connection, get_pool_status
from app.common.dependencies import verify_internal_secret
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
    """Detailed health check with dependencies."""

    status: str
    version: str
    environment: str
    timestamp: datetime
    database: dict
    pool: dict


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