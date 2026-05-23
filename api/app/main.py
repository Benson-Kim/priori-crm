import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.common.database import Base, engine
from app.common.exceptions import register_exception_handlers
from app.common.logging import setup_logging
from app.common.middleware import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    RequestLoggingMiddleware,
)
from app.lib.config import settings

# ── Model registry bootstrap ───────────────────────────────────────────
# Import every ORM model *before* any router / service so that
# SQLAlchemy's string-based relationship() references (e.g. "Customer",
# "Vendor") can always be resolved when the mapper is first configured.
# Order is: parent tables first, then children that reference them.
import app.modules.customers.models  # noqa: F401  ← Customer
import app.modules.invoices.models   # noqa: F401  ← Invoice, Payment
import app.modules.quotes.models     # noqa: F401  ← Quote
import app.modules.vendors.models    # noqa: F401  ← Vendor
import app.modules.expenses.models   # noqa: F401  ← Expense

from app.modules.auth.router import router as auth_router
from app.modules.customers.router import router as customers_router
from app.modules.invoices.router import router as invoices_router
from app.modules.quotes.router import router as quotes_router
from app.modules.vendors.router import router as vendors_router
from app.modules.expenses.router import router as expenses_router
from app.modules.health.router import router as health_router


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
    logger.info("API documentation available at /docs")
    yield
    # --- Shutdown ---
    logger.info("Shutting down application")
    engine.dispose()
    logger.info("Database connections closed")


def create_app() -> FastAPI:
    """Application factory for the Priori Technologies API."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Enterprise CRM and Accounting Platform",
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
        lifespan=lifespan,
    )
    
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    
    if settings.RATE_LIMIT_ENABLED:
        app.add_middleware(RateLimitMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list ,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Response-Time"],
    )   

    register_exception_handlers(app)
    _register_routers(app)

    return app

def _register_routers(app: FastAPI) -> None:
    """Register all module routers with the API prefix."""
    api_prefix = settings.API_V1_PREFIX

    app.include_router(health_router, prefix=api_prefix, tags=["Health"])
    app.include_router(auth_router, prefix=f"{api_prefix}/auth", tags=["Auth"])
    app.include_router(customers_router, prefix=f"{api_prefix}/customers", tags=["Customers"])
    app.include_router(invoices_router, prefix=f"{api_prefix}/invoices", tags=["Invoices"])
    app.include_router(quotes_router, prefix=f"{api_prefix}/quotes", tags=["Quotes"])
    app.include_router(vendors_router, prefix=f"{api_prefix}/vendors", tags=["Vendors"])
    app.include_router(expenses_router, prefix=f"{api_prefix}/expenses", tags=["Expenses"])
    logger.info(
       "Registered routers: %s",
        [route.path for route in app.routes],  # type: ignore
    )

app = create_app()