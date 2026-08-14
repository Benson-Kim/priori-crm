import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.database import check_database_connection, get_db, get_pool_status
from app.common.dependencies import verify_internal_secret
from app.common.financial import TAX_RATES
from app.common.routing import CommitOnSuccessRoute
from app.lib.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(route_class=CommitOnSuccessRoute)


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
    Basic health check endpoint (load-balancer probe).

    Canonical contract (#28): exactly ``status``, ``version``,
    ``environment`` and ``timestamp``. This is deliberately minimal — it
    carries no service-name field and no infrastructure detail. Service
    identity and dependency metrics live on the internal-secret-gated
    ``/health/detailed``. ``version`` is the semantic application version
    (``settings.APP_VERSION``).
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
        "secret since it exposes infrastructure details."
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


# SCHEDULER  (internal — hidden from public OpenAPI docs)


@router.post(
    "/internal/email-outbox/drain",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
    summary="Drain the transactional email outbox (internal)",
    description=(
        "Retries pending/failed outbox emails with dead-lettering after "
        "MAX_DELIVERY_ATTEMPTS. Called by the scheduler alongside "
        "transition-overdue / transition-expired / purge-otps. "
        "Requires the X-Internal-Secret header; not a public client endpoint."
    ),
    dependencies=[Depends(verify_internal_secret)],
)
def drain_email_outbox(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict:
    from app.common.email_outbox import EmailOutboxService

    return EmailOutboxService(db).drain(limit=limit)


@router.get(
    "/internal/email-outbox/dead",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
    summary="List dead-lettered outbox emails (internal)",
    description=(
        "Inspect emails whose delivery attempts are exhausted "
        "(status='dead'). Operator tooling for the dead-letter queue; see "
        "docs/operations/email-outbox-dlq.md. Requires X-Internal-Secret."
    ),
    dependencies=[Depends(verify_internal_secret)],
)
def list_dead_letter_emails(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict:
    from app.common.email_outbox import EmailOutboxService

    items = EmailOutboxService(db).list_dead(limit=limit)
    return {"count": len(items), "items": items}


@router.post(
    "/internal/email-outbox/requeue",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
    summary="Requeue dead-lettered outbox emails (internal)",
    description=(
        "Return dead-lettered emails to the retry queue with a fresh "
        "attempt budget. Use only after the delivery root cause is fixed; "
        "see docs/operations/email-outbox-dlq.md. Optionally target one "
        "row via outboxId. Requires X-Internal-Secret."
    ),
    dependencies=[Depends(verify_internal_secret)],
)
def requeue_dead_letter_emails(
    db: Annotated[Session, Depends(get_db)],
    outbox_id: Annotated[
        str | None,
        Query(alias="outboxId", description="Requeue a single row by id"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> dict:
    import uuid as uuid_module

    from app.common.email_outbox import EmailOutboxService
    from app.common.exceptions import BadRequestException

    parsed_id = None
    if outbox_id:
        try:
            parsed_id = uuid_module.UUID(outbox_id)
        except ValueError:
            raise BadRequestException(
                detail="outboxId must be a valid UUID", field="outboxId"
            ) from None

    return EmailOutboxService(db).requeue_dead(outbox_id=parsed_id, limit=limit)
