"""Custom exceptions and global error handlers."""

import logging
import re
import uuid
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.lib.config import settings

logger = logging.getLogger(__name__)


def _get_cors_headers(request: Request) -> dict[str, str]:
    """Get CORS headers for error responses.

    Ensures error responses include CORS headers so cross-origin requests
    from the frontend can read the error details.
    """
    origin = request.headers.get("origin", "")

    # Check if origin is in allowed list
    allowed = origin in settings.cors_origins_list
    if not allowed and settings.CORS_ORIGIN_REGEX:
        try:
            if re.match(settings.CORS_ORIGIN_REGEX, origin):
                allowed = True
        except re.error:
            pass

    if allowed:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Expose-Headers": "X-Request-ID, X-Response-Time, X-Truncated, X-Export-Limit, X-Delete-Type",
        }

    return {}


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


class StepUpRequiredException(AppException):
    """Context-aware access control demands step-up verification (401).

    Raised by the zero-trust gate (issue #67) when the ABAC policy engine
    or the session risk score returns a CHALLENGE decision. The stable
    ``error_code`` ``STEP_UP_REQUIRED`` plus ``details.challenge = "otp"``
    tell the client to re-run the existing login → OTP flow; the fresh
    token pair it yields satisfies the challenge.
    """

    def __init__(
        self,
        detail: str = "Additional verification is required for this action.",
        challenge: str = "otp",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            error_code="STEP_UP_REQUIRED",
            extra={"challenge": challenge},
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


class ServiceUnavailableException(AppException):
    """Service temporarily unavailable / at capacity (503).

    Used to shed load when a bounded resource (e.g. the export concurrency
    gate) is saturated, so the client backs off instead of overwhelming the
    process.
    """

    def __init__(
        self,
        detail: str = "Service temporarily unavailable",
        retry_after: int | None = None,
    ) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
            extra={"retry_after": retry_after} if retry_after else {},
        )


class ReferenceCurrencyException(AppException):
    """Attempt to bill in a reference (display-only) currency (400).

    Sales Desk contract (issue #44): EUR/GBP exist for display/conversion
    only — a customer has billing profiles solely in USD/KES, so a quote
    can never be created in a reference currency. Typed (distinct
    ``error_code``) so the UI can render the specific builder error state.
    """

    def __init__(self, currency: str, billable: list[str]) -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{currency}' is a reference (display-only) currency. "
                f"Quotes can only be billed in a customer billing-profile "
                f"currency: {billable}."
            ),
            extra={"field": "currency", "currency": currency, "billable": billable},
        )


class UnsyncedBillingProfileException(AppException):
    """Issuing/finalizing a document against an unsynced billing profile (409).

    Sales Desk contract (issue #44): a deal-linked quote posts to a customer
    billing profile; it cannot be issued (sent) or finalized (approved)
    until that profile has been pushed to accounting (``synced``, ADR 0010).
    Typed (distinct ``error_code``) so the UI can offer the "push profile
    to accounting" affordance.
    """

    def __init__(self, profile_code: str, currency: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Billing profile '{profile_code}' ({currency}) has not been "
                "pushed to accounting. Sync the profile before issuing or "
                "finalizing this quote."
            ),
            extra={"profile_code": profile_code, "currency": currency},
        )


# Request-validation humanisation
#
# FastAPI/Pydantic validation errors are developer-facing: the location is a
# raw tuple like ("body", "password") and the message is framework prose
# ("String should have at least 8 characters"). Rendering those verbatim leaked
# "body.password: string should have at least 8 characters" to a user on the
# login screen. The helpers below turn each Pydantic error into a sentence a
# non-technical user can act on, keyed off the machine-readable `type` and
# `ctx` rather than by rewriting `msg` text.

# Location segments FastAPI prepends to identify the request part the value
# came from. They are noise in a user-facing message.
_LOCATION_PREFIXES = frozenset({"body", "query", "path", "header", "cookie"})

# Field names whose humanised form is not just a capitalised snake_case split.
_FIELD_LABEL_OVERRIDES = {
    "email": "Email address",
    "otp": "One-time code",
    "code": "One-time code",
    "vat_rate": "VAT rate",
    "vatRate": "VAT rate",
    "iban": "IBAN",
    "url": "URL",
    "id": "ID",
}


def _field_path(loc: tuple[Any, ...]) -> str:
    """Reduce a Pydantic error location to a client-usable field path.

    Drops the leading request-part segment and renders list indices in
    bracket form so a nested error reads `lines[0].quantity`.
    """
    parts = list(loc)
    if parts and parts[0] in _LOCATION_PREFIXES:
        parts = parts[1:]

    if not parts:
        return "request"

    rendered = ""
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif rendered:
            rendered += f".{part}"
        else:
            rendered = str(part)

    return rendered or "request"


def _field_label(field: str) -> str:
    """Humanise a field path into a sentence-leading label."""
    # Label the specific value that failed, not the whole path: for
    # `lines[0].quantity` the actionable noun is "Quantity". Take the last
    # dotted segment first, then strip any trailing list index off it.
    leaf = field.split(".")[-1].split("[")[0] or field

    override = _FIELD_LABEL_OVERRIDES.get(leaf)
    if override:
        return override

    # snake_case and camelCase both occur (query aliases are camelCase).
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", leaf).replace("_", " ").strip()
    if not spaced:
        return "This field"

    words = spaced.split()
    return " ".join([words[0].capitalize(), *(w.lower() for w in words[1:])])


def _plural(count: Any, noun: str) -> str:
    """Render `1 character` / `8 characters` without a stray plural."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _as_sentence(text: str) -> str:
    """Normalise validator-authored text into a user-facing sentence.

    Custom validators sometimes name fields in code form (`vat_rate is
    required when vat_enabled is true`). Spell those out so the reader never
    sees an identifier, then ensure the result is capitalised and terminated.
    """
    # Only touch multi-word snake_case tokens; a bare word like "quantity"
    # is already ordinary English and must not be altered.
    spelled = re.sub(
        r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b", lambda m: m.group(0).replace("_", " "), text
    )

    spelled = spelled.strip()
    if not spelled:
        return spelled

    if spelled[0].islower():
        spelled = spelled[0].upper() + spelled[1:]

    if spelled[-1] not in ".!?":
        spelled += "."

    return spelled


def _friendly_message(error: dict[str, Any]) -> str:
    """Build a user-readable sentence for one Pydantic validation error.

    Dispatches on the stable `type` discriminator and the `ctx` constraint
    values. Any type without an explicit case falls through to a generic
    sentence, so an unmapped framework message can never reach the client.
    """
    error_type = str(error.get("type", ""))
    ctx = error.get("ctx") or {}
    label = _field_label(_field_path(tuple(error.get("loc", ()))))

    if error_type == "missing":
        return f"{label} is required."

    if error_type == "string_too_short":
        return (
            f"{label} must be at least {_plural(ctx.get('min_length'), 'character')}."
        )

    if error_type == "string_too_long":
        return f"{label} must be at most {_plural(ctx.get('max_length'), 'character')}."

    # `too_short`/`too_long` apply to collections (lists, sets), not strings.
    if error_type == "too_short":
        return f"{label} must have at least {_plural(ctx.get('min_length'), 'item')}."

    if error_type == "too_long":
        return f"{label} must have at most {_plural(ctx.get('max_length'), 'item')}."

    if error_type == "string_pattern_mismatch":
        return f"{label} is not in the expected format."

    if error_type in {"greater_than", "greater_than_equal"}:
        bound = ctx.get("gt", ctx.get("ge"))
        qualifier = "greater than" if error_type == "greater_than" else "at least"
        return f"{label} must be {qualifier} {bound}."

    if error_type in {"less_than", "less_than_equal"}:
        bound = ctx.get("lt", ctx.get("le"))
        qualifier = "less than" if error_type == "less_than" else "at most"
        return f"{label} must be {qualifier} {bound}."

    if error_type in {"int_parsing", "int_type", "int_from_float"}:
        return f"{label} must be a whole number."

    if error_type in {"float_parsing", "float_type", "decimal_parsing"}:
        return f"{label} must be a number."

    if error_type in {"bool_parsing", "bool_type"}:
        return f"{label} must be true or false."

    if error_type in {"date_parsing", "date_type", "date_from_datetime_parsing"}:
        return f"{label} must be a valid date."

    if error_type in {
        "datetime_parsing",
        "datetime_type",
        "datetime_from_date_parsing",
    }:
        return f"{label} must be a valid date and time."

    if error_type == "uuid_parsing":
        return f"{label} must be a valid identifier."

    if error_type in {"enum", "literal_error"}:
        expected = ctx.get("expected")
        if expected:
            return f"{label} must be one of: {expected}."
        return f"{label} is not one of the allowed values."

    if error_type in {"json_invalid", "json_type"}:
        return "The request body is not valid JSON."

    if error_type == "string_type":
        return f"{label} must be text."

    if error_type == "value_error":
        # Covers EmailStr and every custom `field_validator`, which already
        # raise sentences written for humans. Pydantic prefixes the raw text
        # with "Value error, "; strip it and reuse the author's wording.
        raw = str(error.get("msg", "")).removeprefix("Value error, ").strip()

        # EmailStr's own text is developer prose ("value is not a valid email
        # address: An email address must have an @-sign."); replace it rather
        # than prefixing a label onto it.
        if "not a valid email address" in raw.lower():
            return f"{label} must be a valid email address."

        if raw:
            return _as_sentence(raw)

    return f"{label} is not valid."


def humanize_validation_errors(
    raw_errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach `field` and `message` to each raw Pydantic error.

    `loc`/`msg` are preserved so existing API consumers and debugging tooling
    keep working; clients should render `message` and key form state off
    `field`.
    """
    humanized: list[dict[str, Any]] = []
    for error in raw_errors:
        entry = dict(error)
        entry["field"] = _field_path(tuple(error.get("loc", ())))
        entry["message"] = _friendly_message(error)
        humanized.append(entry)
    return humanized


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

        # Surface a Retry-After header for backoff-bearing responses
        # (429 rate limit, 503 export saturation) so clients honour it.
        headers = _get_cors_headers(request)
        retry_after = exc.extra.get("retry_after")
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)

        return JSONResponse(
            status_code=exc.status_code, content=response_data, headers=headers or None
        )

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Handle FastAPI request validation errors."""
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

        # exc.errors() can embed a non-serializable exception object in each
        # error's ctx (e.g. the ValueError raised by a Pydantic field_validator).
        # jsonable_encoder coerces those to strings so json.dumps cannot fail
        # inside the handler itself.
        errors = jsonable_encoder(exc.errors())

        # Add a user-readable `message` and a clean `field` to every error, and
        # promote the first message to the top-level `error`. Clients that show
        # `error` verbatim then display "Password must be at least 8
        # characters." instead of the framework's raw prose.
        errors = humanize_validation_errors(errors)
        summary = next(
            (e["message"] for e in errors if e.get("message")),
            "Please check the information you entered and try again.",
        )

        logger.warning(
            "Request validation error",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "errors": errors,
            },
        )

        headers = _get_cors_headers(request)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": summary,
                "error_code": "VALIDATION_ERROR",
                "status_code": status.HTTP_422_UNPROCESSABLE_ENTITY,
                "request_id": request_id,
                "details": {"errors": errors},
            },
            headers=headers or None,
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

        headers = _get_cors_headers(request)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": detail,
                "error_code": error_code,
                "status_code": status.HTTP_409_CONFLICT,
                "request_id": request_id,
            },
            headers=headers or None,
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

        headers = _get_cors_headers(request)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "A database error occurred",
                "error_code": "DATABASE_ERROR",
                "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "request_id": request_id,
            },
            headers=headers or None,
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

        headers = _get_cors_headers(request)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=response_data,
            headers=headers or None,
        )
