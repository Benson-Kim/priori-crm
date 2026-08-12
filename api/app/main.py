import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Model registry bootstrap
# Import every ORM model *before* any router / service so that
# SQLAlchemy's string-based relationship() references (e.g. "Customer",
# "Vendor") can always be resolved when the mapper is first configured.
# Order is: parent tables first, then children that reference them.
import app.common.audit
import app.common.email_outbox
import app.common.reference_sequence
import app.common.reference_triggers
import app.modules.auth.models
import app.modules.customers.models
import app.modules.deals.models
import app.modules.expenses.models
import app.modules.invoices.models
import app.modules.nurture.models
import app.modules.onboarding.models
import app.modules.owner.models
import app.modules.purchase_orders.models
import app.modules.quotes.models
import app.modules.vendors.models
from app.common.database import engine
from app.common.exceptions import register_exception_handlers
from app.common.logging import setup_logging
from app.common.middleware import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from app.common.otel import setup_metrics, shutdown_metrics
from app.lib.config import settings
from app.modules.auth.router import router as auth_router
from app.modules.customers.router import router as customers_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.deals.router import router as deals_router
from app.modules.expenses.router import router as expenses_router
from app.modules.health.router import router as health_router
from app.modules.invoices.router import router as invoices_router
from app.modules.nurture.router import router as nurture_router
from app.modules.onboarding.router import router as onboarding_router
from app.modules.owner.router import router as owner_router
from app.modules.purchase_orders.router import router as purchase_orders_router
from app.modules.quotes.router import router as quotes_router
from app.modules.reports.router import router as reports_router
from app.modules.statements.router import router as statements_router
from app.modules.vendors.router import router as vendors_router

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    # --- Startup ---
    logger.info(
        "Starting %s v%s in %s mode",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.ENVIRONMENT,
    )
    logger.info("Verifying database connection")
    # Base.metadata.create_all(bind=engine)  # Removed: using Alembic for migrations

    if settings.RATE_LIMIT_BACKEND == "redis" and settings.REDIS_URL:
        try:
            import redis

            r = redis.Redis.from_url(settings.REDIS_URL, socket_timeout=1)
            r.ping()
            logger.info("Connected to Redis for rate limiting")
        except Exception as e:
            logger.error("Redis connectivity check failed at startup", exc_info=e)

    # OTel metrics (no-op unless OTEL_METRICS_ENABLED).
    setup_metrics()

    logger.info("API documentation available at /docs")
    yield
    # --- Shutdown ---
    logger.info("Shutting down application")
    shutdown_metrics()
    engine.dispose()
    logger.info("Database connections closed")


def create_app() -> FastAPI:
    """Application factory for the Business Central API."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Enterprise CRM and Accounting Platform",
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
        lifespan=lifespan,
    )

    # Middleware executes in reverse order of registration: the last one
    # added is the outermost. Register the rate limiter first so the
    # logging (X-Response-Time) and request-ID (X-Request-ID) middleware
    # wrap it and a throttled 429 still unwinds back through them with full
    # tracing headers.
    if settings.RATE_LIMIT_ENABLED:
        app.add_middleware(RateLimitMiddleware)

    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)

    # Outermost: security headers apply to every response, including error
    # and throttled (429) paths.
    app.add_middleware(SecurityHeadersMiddleware)

    cors_kwargs: dict[str, Any] = {
        "allow_origins": settings.cors_origins_list,
        "allow_credentials": True,
        # Explicit allow-lists instead of "*": wildcards combined
        # with credentials are overly permissive.
        "allow_methods": settings.cors_allow_methods_list,
        "allow_headers": settings.cors_allow_headers_list,
        "expose_headers": [
            "X-Request-ID",
            "X-Response-Time",
            "X-Truncated",
            "X-Export-Limit",
            "X-Delete-Type",
        ],
    }

    if settings.CORS_ORIGIN_REGEX:
        cors_kwargs["allow_origin_regex"] = settings.CORS_ORIGIN_REGEX

    app.add_middleware(CORSMiddleware, **cors_kwargs)

    register_exception_handlers(app)
    _register_routers(app)

    return app


def _register_routers(app: FastAPI) -> None:
    """Register all module routers with the API prefix."""
    api_prefix = settings.API_V1_PREFIX

    app.include_router(health_router, prefix=api_prefix, tags=["Health"])
    app.include_router(auth_router, prefix=f"{api_prefix}/auth", tags=["Auth"])
    app.include_router(
        customers_router, prefix=f"{api_prefix}/customers", tags=["Customers"]
    )
    app.include_router(
        invoices_router, prefix=f"{api_prefix}/invoices", tags=["Invoices"]
    )
    app.include_router(deals_router, prefix=f"{api_prefix}/deals", tags=["Deals"])
    app.include_router(nurture_router, prefix=f"{api_prefix}/nurture", tags=["Nurture"])
    app.include_router(
        onboarding_router, prefix=f"{api_prefix}/onboardings", tags=["Onboarding"]
    )
    app.include_router(quotes_router, prefix=f"{api_prefix}/quotes", tags=["Quotes"])
    app.include_router(vendors_router, prefix=f"{api_prefix}/vendors", tags=["Vendors"])
    app.include_router(
        expenses_router, prefix=f"{api_prefix}/expenses", tags=["Expenses"]
    )
    app.include_router(
        purchase_orders_router,
        prefix=f"{api_prefix}/purchase-orders",
        tags=["Purchase Orders"],
    )
    app.include_router(owner_router, prefix=f"{api_prefix}/owner", tags=["Owner"])
    app.include_router(
        statements_router, prefix=f"{api_prefix}/statements", tags=["Statements"]
    )
    app.include_router(
        dashboard_router, prefix=f"{api_prefix}/dashboard", tags=["Dashboard"]
    )
    app.include_router(reports_router, prefix=f"{api_prefix}/reports", tags=["Reports"])
    logger.info(
        "Registered routers: %s",
        [route.path for route in app.routes],  # type: ignore
    )


app = create_app()
