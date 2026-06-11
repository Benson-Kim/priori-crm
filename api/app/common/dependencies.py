"""FastAPI dependencies for database sessions, authentication, and services."""

import secrets
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.common.exceptions import ForbiddenException, UnauthorizedException
from app.common.security import decode_access_token
from app.constants.enums import PRIVILEGED_ROLES, UserRole
from app.lib.config import settings

# Type alias for database session dependency
DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DbSession,
    token: dict = Depends(decode_access_token),
) -> "User":  # noqa: F821
    """Extract and validate the current user from the JWT access token."""
    from app.modules.auth.models import User

    user = db.query(User).filter(User.id == token["sub"]).first()

    if user is None:
        raise UnauthorizedException("User not found")

    if not user.is_active:
        raise UnauthorizedException("User account is inactive")

    return user


# Type alias for current user dependency
CurrentUser = Annotated["User", Depends(get_current_user)]  # noqa: F821


def require_role(*allowed_roles: UserRole):
    """Build a dependency that rejects users whose role is not allowed.

    Use for ad-hoc role sets. For the common destructive/financial gate,
    prefer :func:`require_privileged`, which centralizes the policy.
    """
    allowed = set(allowed_roles)

    def _check(current_user: CurrentUser):
        if UserRole(current_user.role) not in allowed:
            raise ForbiddenException(
                detail="You do not have permission to perform this action.",
                required_permission="/".join(sorted(r.value for r in allowed)),
            )
        return current_user

    return _check


def require_privileged():
    """Dependency gating destructive/financial actions to privileged roles.

    The single place that encodes the ``PRIVILEGED_ROLES`` policy (ADMIN /
    MANAGER). Use on every financial or destructive endpoint, e.g.::

        dependencies=[Depends(require_privileged())]
    """

    def _check(current_user: CurrentUser):
        if not UserRole(current_user.role).is_privileged:
            raise ForbiddenException(
                detail="You do not have permission to perform this action.",
                required_permission="/".join(sorted(r.value for r in PRIVILEGED_ROLES)),
            )
        return current_user

    return _check


def verify_internal_secret(
    x_internal_secret: Annotated[str | None, Header(alias="X-Internal-Secret")] = None,
) -> None:
    """Authenticate machine-to-machine / scheduler calls via a shared secret.

    Used to protect internal endpoints (e.g. nightly overdue transition) that
    must never be publicly callable. The header is compared in constant time.
    If no secret is configured the endpoint is refused outright, so a
    misconfigured deployment fails closed rather than open.
    """
    expected = settings.INTERNAL_API_SECRET
    if not expected:
        raise ForbiddenException(
            detail="Internal endpoint is not enabled (no INTERNAL_API_SECRET configured)."
        )
    if not x_internal_secret or not secrets.compare_digest(x_internal_secret, expected):
        raise UnauthorizedException("Invalid or missing internal credentials.")


def get_customer_service(db: DbSession, current_user: CurrentUser):
    """Provide a CustomerService scoped to the current request and acting user."""
    from app.modules.customers.service import CustomerService

    return CustomerService(db, current_user=current_user)


CustomerServiceDep = Annotated["CustomerService", Depends(get_customer_service)]  # noqa: F821


def get_invoice_service(db: DbSession, current_user: CurrentUser):
    """Provide a InvoiceService scoped to the current request and acting user."""
    from app.modules.invoices.service import InvoiceService

    return InvoiceService(db, current_user=current_user)


InvoiceServiceDep = Annotated["InvoiceService", Depends(get_invoice_service)]  # noqa: F821


def get_quote_service(db: DbSession, current_user: CurrentUser):
    """Provide a QuoteService scoped to the current request and acting user."""
    from app.modules.quotes.service import QuoteService

    return QuoteService(db, current_user=current_user)


QuoteServiceDep = Annotated["QuoteService", Depends(get_quote_service)]  # noqa: F821


def get_expense_service(db: DbSession, current_user: CurrentUser) -> "ExpenseService":  # noqa: F821
    """Provide an ExpenseService scoped to the current request and acting user."""
    from app.modules.expenses.service import ExpenseService

    return ExpenseService(db, current_user=current_user)


ExpenseServiceDep = Annotated["ExpenseService", Depends(get_expense_service)]  # noqa: F821


def get_vendor_service(db: DbSession, current_user: CurrentUser):
    """Provide a VendorService scoped to the current request and acting user."""
    from app.modules.vendors.service import VendorService

    return VendorService(db, current_user=current_user)


VendorServiceDep = Annotated["VendorService", Depends(get_vendor_service)]  # noqa: F821


def get_owner_service(db: DbSession, current_user: CurrentUser):
    """Provide an OwnerService scoped to the current request and acting user."""
    from app.modules.owner.service import OwnerService

    return OwnerService(db, current_user=current_user)


OwnerServiceDep = Annotated["OwnerService", Depends(get_owner_service)]  # noqa: F821
