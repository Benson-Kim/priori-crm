"""Database configuration with connection pooling and session management."""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import Pool

from app.lib.config import settings

logger = logging.getLogger(__name__)

# Naming convention for constraints (helps with Alembic migrations)
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""

    metadata = metadata


# Database engine with connection pooling
engine = create_engine(
    str(settings.DATABASE_URL),
    pool_pre_ping=True,  # Verify connections before using
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    echo=settings.DB_ECHO,
    future=True,
    isolation_level="READ COMMITTED",  # Default isolation level
)

# Session factory
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # Prevent lazy loading issues after commit
)


# Event listeners for connection pool monitoring
@event.listens_for(Pool, "connect")
def receive_connect(dbapi_conn: Any, connection_record: Any) -> None:
    """Log when a new database connection is established."""
    logger.debug("Database connection established")


@event.listens_for(Pool, "checkout")
def receive_checkout(
    dbapi_conn: Any, connection_record: Any, connection_proxy: Any
) -> None:
    """Log when a connection is checked out from the pool."""
    logger.debug("Connection checked out from pool")


@event.listens_for(Engine, "handle_error")
def receive_handle_error(exception_context: Any) -> None:
    """Log database errors."""
    logger.error(
        "Database error occurred",
        exc_info=exception_context.original_exception,
        extra={
            "statement": exception_context.statement,
            "parameters": exception_context.parameters,
        },
    )


def assert_version(
    db: Session,
    model: type,
    record_id: Any,
    expected_version: int | None,
) -> None:
    """Atomically assert a row is still at ``expected_version`` (P-9).

    Re-selects the row's version column ``WITH FOR UPDATE`` so concurrent
    updaters serialize on the row: the second updater blocks until the first
    commits, then observes the bumped version and is rejected. This replaces
    the non-atomic "load without lock, compare in Python" check that allowed
    silent last-write-wins.

    No-op when ``expected_version`` is None (caller opted out of the check).
    Raises ConflictException (HTTP 409) on mismatch.

    Note: the row-level lock is a PostgreSQL guarantee; on the SQLite test
    fallback FOR UPDATE is a no-op, but the version comparison still holds.
    """
    if expected_version is None:
        return

    # Local import avoids a circular import (exceptions imports config only).
    from app.common.exceptions import ConflictException

    current_version = (
        db.query(model.version).filter(model.id == record_id).with_for_update().scalar()
    )

    if current_version is not None and current_version != expected_version:
        raise ConflictException(
            detail=(
                f"This record has been modified by another user "
                f"(expected version {expected_version}, current version "
                f"{current_version}). Please refresh and try again."
            ),
        )


def get_db() -> Generator[Session, None, None]:
    """
    Dependency that provides a database session.
    Session is automatically closed after use, even if an exception occurs.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """
    Context manager for database sessions in non-FastAPI contexts.

    Usage:
        with get_db_context() as db:
            customer = db.query(Customer).first()
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_database_connection() -> bool:
    """
    Check if database is accessible.

    Returns:
        bool: True if connection successful, False otherwise
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except DBAPIError:
        logger.exception("Database connection check failed")
        return False


def get_pool_status() -> dict[str, Any]:
    """
    Get current connection pool statistics.

    Returns:
        dict: Pool status metrics
    """
    pool = engine.pool
    return {
        "size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "total": pool.size() + pool.overflow(),
    }
