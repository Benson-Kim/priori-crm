"""Custom exceptions and global error handlers."""

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.lib.config import settings

logger = logging.getLogger(__name__)


class AppException(Exception):
    """Base application exception with status code and detail."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        error_code: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code or self.__class__.__name__
        self.extra = extra or {}
        super().__init__(detail)


class NotFoundException(AppException):
    """Resource not found (404)."""

    def __init__(
        self, detail: str = "Resource not found", resource: str | None = None
    ) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
            extra={"resource": resource} if resource else {},
        )


class UnauthorizedException(AppException):
    """Authentication required or failed (401)."""

    def __init__(self, detail: str = "Authentication required") -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


class ForbiddenException(AppException):
    """Insufficient permissions (403)."""

    def __init__(
        self, detail: str = "Permission denied", required_permission: str | None = None
    ) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            extra={"required_permission": required_permission}
            if required_permission
            else {},
        )


class BadRequestException(AppException):
    """Invalid request data (400)."""

    def __init__(
        self, detail: str = "Invalid request", field: str | None = None
    ) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            extra={"field": field} if field else {},
        )


class ConflictException(AppException):
    """Resource conflict (409)."""

    def __init__(
        self, detail: str = "Resource already exists", field: str | None = None
    ) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
            extra={"field": field} if field else {},
        )


class ValidationException(AppException):
    """Validation error (422)."""

    def __init__(
        self,
        detail: str = "Validation failed",
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
            extra={"errors": errors} if errors else {},
        )


class DatabaseException(AppException):
    """Database operation failed (500)."""

    def __init__(self, detail: str = "Database operation failed") -> None:
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail
        )


class EmailDeliveryException(AppException):
    """Email delivery failed (502)."""

    def __init__(
        self, detail: str = "Email delivery failed", recipient: str | None = None
    ) -> None:
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
            extra={"recipient": recipient} if recipient else {},
        )


class RateLimitException(AppException):
    """Rate limit exceeded (429)."""

    def __init__(
        self, detail: str = "Rate limit exceeded", retry_after: int | None = None
    ) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            extra={"retry_after": retry_after} if retry_after else {},
        )


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers for consistent error responses."""

    @app.exception_handler(AppException)
    async def app_exception_handler(
        request: Request, exc: AppException
    ) -> JSONResponse:
        """Handle custom application exceptions."""
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

        logger.warning(
            f"Application exception: {exc.error_code}",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "status_code": exc.status_code,
                "detail": exc.detail,
                "extra": exc.extra,
            },
        )

        response_data = {
            "error": exc.detail,
            "error_code": exc.error_code,
            "status_code": exc.status_code,
            "request_id": request_id,
        }

        if exc.extra:
            response_data["details"] = exc.extra

        return JSONResponse(status_code=exc.status_code, content=response_data)

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Handle FastAPI request validation errors."""
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

        logger.warning(
            "Request validation error",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "errors": exc.errors(),
            },
        )

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "Validation failed",
                "error_code": "VALIDATION_ERROR",
                "status_code": status.HTTP_422_UNPROCESSABLE_ENTITY,
                "request_id": request_id,
                "details": {"errors": exc.errors()},
            },
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(
        request: Request, exc: IntegrityError
    ) -> JSONResponse:
        """Handle database integrity constraint violations."""
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

        logger.error(
            "Database integrity error",
            exc_info=exc,
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
            },
        )

        # Parse constraint violation to provide user-friendly message
        detail = "A database constraint was violated"
        error_code = "INTEGRITY_ERROR"

        pgcode = getattr(exc.orig, "pgcode", None)
        if pgcode == "23505":
            detail = "This record already exists"
            error_code = "DUPLICATE_RECORD"
        elif pgcode == "23503":
            detail = "Referenced record does not exist"
            error_code = "INVALID_REFERENCE"
        else:
            # Fallback for SQLite in tests
            orig_str = str(exc.orig).lower()
            if "unique constraint" in orig_str:
                detail = "This record already exists"
                error_code = "DUPLICATE_RECORD"
            elif "foreign key constraint" in orig_str:
                detail = "Referenced record does not exist"
                error_code = "INVALID_REFERENCE"

        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": detail,
                "error_code": error_code,
                "status_code": status.HTTP_409_CONFLICT,
                "request_id": request_id,
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(
        request: Request, exc: SQLAlchemyError
    ) -> JSONResponse:
        """Handle general SQLAlchemy errors."""
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

        logger.exception(
            "Database error",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
            },
        )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "A database error occurred",
                "error_code": "DATABASE_ERROR",
                "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "request_id": request_id,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Handle all unhandled exceptions."""
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

        logger.exception(
            "Unhandled exception",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "client": request.client.host if request.client else None,
            },
        )

        # Don't leak exception details in production
        detail = "An internal server error occurred"
        response_data: dict[str, Any] = {
            "error": detail,
            "error_code": "INTERNAL_SERVER_ERROR",
            "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "request_id": request_id,
        }

        if settings.is_development:
            response_data["debug"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=response_data,
        )
