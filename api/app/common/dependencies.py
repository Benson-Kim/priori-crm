"""FastAPI dependencies for database sessions, authentication, and services."""

import ipaddress
import secrets
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.common.exceptions import ForbiddenException, UnauthorizedException
from app.common.security import decode_access_token
from app.constants.enums import ESSENTIAL_MODULES, PRIVILEGED_ROLES, ModuleKey, UserRole
from app.lib.config import settings

# Type alias for database session dependency
DbSession = Annotated[Session, Depends(get_db)]

#: Path prefixes (under ``settings.API_V1_PREFIX``) where a PLATFORM_OPERATOR
#: token is accepted. Everything else is tenant surface: the operator's
#: authority axis is platform administration only (ADR-0011), so an operator
#: token must be rejected there CENTRALLY — a route with no role gate would
#: otherwise accept everyone, operator included (isolation contract, #71).
_OPERATOR_SURFACE_PREFIXES = ("/platform", "/auth", "/health")


def _is_operator_surface(path: str) -> bool:
    """Whether a request path lies on the operator-accessible surface.

    Exact-segment prefix match (``/platform`` or ``/platform/…``, never
    ``/platformX``) so the allow-list cannot be widened by a lookalike
    route. Fail-closed by construction: any future router registered
    outside these prefixes rejects operator tokens with no further work.
    """
    for allowed in _OPERATOR_SURFACE_PREFIXES:
        full = f"{settings.API_V1_PREFIX}{allowed}"
        if path == full or path.startswith(f"{full}/"):
            return True
    return False


def _enforce_operator_mfa_scope(path: str, token: dict) -> None:
    """Confine operator tokens without a full-MFA claim to enrollment only.

    ADR-0014 (#73): a full operator token carries ``mfa == "totp"`` —
    stamped at verify-otp only when the sign-in proved a TOTP/recovery
    code against an ACTIVE enrollment, and propagated (never upgraded) by
    refresh. Anything else — the ``"enroll"`` claim an unenrolled
    operator receives, or a claimless legacy token (fail closed) — may
    reach nothing under ``/platform`` except the ``/platform/mfa``
    enrollment endpoints. ``/auth`` and ``/health`` stay reachable so the
    constrained operator can still refresh, log out and probe health.
    """
    platform_root = f"{settings.API_V1_PREFIX}/platform"
    if not (path == platform_root or path.startswith(f"{platform_root}/")):
        return
    if token.get("mfa") == "totp":
        return
    enroll_root = f"{platform_root}/mfa"
    if path == enroll_root or path.startswith(f"{enroll_root}/"):
        return
    raise ForbiddenException(
        detail=(
            "Multi-factor enrollment is required before the platform "
            "console can be used. Complete TOTP enrollment via "
            "/platform/mfa, then sign in again with your authenticator "
            "code."
        ),
        required_permission="mfa:totp",
    )


def get_current_user(
    request: Request,
    db: DbSession,
    token: dict = Depends(decode_access_token),
) -> "User":  # noqa: F821
    """Extract and validate the current user from the JWT access token.

    Also the single enforcement point for platform ⟂ tenant isolation
    (ADR-0011/0013, #71): a PLATFORM_OPERATOR token is rejected with 403
    on every authenticated route outside ``/platform``, ``/auth`` and
    ``/health``. Enforced here at the root — not per-route — because most
    tenant endpoints carry no role gate at all and would otherwise accept
    any authenticated user, the operator included.

    Tenant lifecycle (ADR-0013 Phase A, hardened per the !77 review): a
    SUSPENDED organisation is denied on EVERY authenticated route,
    immediately — a live access token does not ride out its TTL. One
    indexed PK read (the same read as the module gate, which keeps its
    own check for the internal-secret endpoints that carry no JWT).
    Deliberately reactivation-friendly: nothing is revoked or burnt —
    the un-spent refresh token and any live access token work again the
    moment the operator reactivates the org. Operators are exempt (their
    authority axis is disjoint from tenant state, and they must stay
    able to reactivate). Reads the singleton owner row today; Phase T1
    (#75) re-points this at the resolved owner.
    """
    from app.modules.auth.models import User

    user = db.query(User).filter(User.id == token["sub"]).first()

    if user is None:
        raise UnauthorizedException("User not found")

    if not user.is_active:
        raise UnauthorizedException("User account is inactive")

    if UserRole(user.role) is UserRole.PLATFORM_OPERATOR:
        if not _is_operator_surface(request.url.path):
            raise ForbiddenException(
                detail=(
                    "Platform operators have no access to tenant data. "
                    "Use the /platform administration surface."
                ),
                required_permission="tenant-surface",
            )
        _enforce_operator_mfa_scope(request.url.path, token)
        return user

    from app.constants.enums import OwnerStatus
    from app.modules.owner.models import SINGLETON_PROFILE_ID, OwnerProfile

    status = (
        db.query(OwnerProfile.status)
        .filter(OwnerProfile.id == SINGLETON_PROFILE_ID)
        .scalar()
    )
    if status == OwnerStatus.SUSPENDED:
        raise ForbiddenException(
            detail=(
                "This organisation's account is suspended. Contact the "
                "platform operator."
            ),
            required_permission="owner:active",
        )

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


def require_module(module_key: ModuleKey):
    """Build a router-wide dependency rejecting requests to a disabled module.

    Per-owner module entitlements: a missing ``owner_module_settings`` row
    means ENABLED (default-on), so the gate only rejects when an explicit
    ``enabled = false`` override exists. Attach at router level::

        app.include_router(
            invoices_router, ..., dependencies=[Depends(require_module(ModuleKey.INVOICES))]
        )

    Deliberately independent of the JWT (no ``CurrentUser``): gated routers
    also host internal-secret scheduler endpoints, and module availability
    is org state, not a per-user permission. Essential modules are never
    gated (defense in depth — their routers should not attach this at all).

    Tenant lifecycle (ADR-0013 Phase A): a SUSPENDED owner is denied every
    non-essential module outright, before the override lookup. JWT-borne
    requests are additionally denied centrally in :func:`get_current_user`
    (per the !77 review, suspension applies immediately on EVERY
    authenticated route, essential surfaces included); the check HERE
    remains load-bearing for the internal-secret scheduler endpoints,
    which carry no JWT and would otherwise keep transitioning a suspended
    owner's documents. The hot path stays two indexed reads: the owner
    status (PK read) and the override row (unique-composite read). Both
    reads resolve the singleton today; Phase T1 (#75) re-points them at
    the resolved owner (readiness audit #2/#15).
    """

    def _check(db: DbSession) -> None:
        if module_key in ESSENTIAL_MODULES:
            return

        from app.constants.enums import OwnerStatus
        from app.modules.owner.models import (
            SINGLETON_PROFILE_ID,
            OwnerModuleSetting,
            OwnerProfile,
        )

        status = (
            db.query(OwnerProfile.status)
            .filter(OwnerProfile.id == SINGLETON_PROFILE_ID)
            .scalar()
        )
        if status == OwnerStatus.SUSPENDED:
            raise ForbiddenException(
                detail=(
                    "This organisation's account is suspended. Contact the "
                    "platform operator."
                ),
                required_permission="owner:active",
            )

        row = (
            db.query(OwnerModuleSetting.enabled)
            .filter(
                OwnerModuleSetting.owner_profile_id == SINGLETON_PROFILE_ID,
                OwnerModuleSetting.module_key == module_key.value,
            )
            .first()
        )
        if row is not None and not row.enabled:
            raise ForbiddenException(
                detail=(
                    f"The '{module_key.value}' module is disabled for this "
                    "organisation. Contact an administrator to enable it."
                ),
                required_permission=f"module:{module_key.value}",
            )

    return _check


def require_step_up(action: str):
    """Build a dependency demanding a fresh second-factor proof (ADR-0014).

    Attach to destructive platform routes (tenant suspension, entitlement
    changes). The caller must present a live TOTP code or a single-use
    recovery code in the ``X-MFA-Code`` header ON THE DESTRUCTIVE REQUEST
    ITSELF — a live session is never sufficient. Verification is
    rate-limited, replay-fenced, and audited (grant AND denial; the
    denial commits before the 401 propagates). ``action`` is the stable
    label written into the audit event.
    """

    def _check(
        db: DbSession,
        current_user: CurrentUser,
        x_mfa_code: Annotated[
            str | None,
            Header(
                alias="X-MFA-Code",
                description=(
                    "Fresh TOTP code or single-use recovery code "
                    "(step-up re-authentication, ADR-0014)"
                ),
            ),
        ] = None,
    ) -> None:
        from app.modules.platform.service import OperatorMfaService

        OperatorMfaService(db, current_user).verify_step_up(x_mfa_code, action=action)

    return _check


def _request_client_host(request: Request) -> str | None:
    """Client address for the /platform IP allowlist.

    Mirrors the rate limiter's trust decision exactly (one knob, one
    meaning): the first hop of ``X-Forwarded-For`` is honoured only when
    ``RATE_LIMIT_TRUST_FORWARDED_FOR`` is explicitly enabled; otherwise
    the raw socket address is used.
    """
    if settings.RATE_LIMIT_TRUST_FORWARDED_FOR is True:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if isinstance(forwarded, str) and forwarded:
            first_hop = forwarded.split(",")[0].strip()
            if first_hop:
                return first_hop
    return request.client.host if request.client else None


def enforce_platform_ip_allowlist(request: Request) -> None:
    """Interim compensating control (ADR-0014): CIDR-gate /platform.

    ``PLATFORM_IP_ALLOWLIST`` is a comma-separated CIDR list; empty =
    disabled (the default). Fail-closed everywhere else: malformed
    configuration is rejected at startup by the settings validator, and —
    defence in depth — any entry or client address that fails to parse
    HERE denies the request rather than skipping the check.
    """
    raw = settings.PLATFORM_IP_ALLOWLIST
    entries = [item.strip() for item in raw.split(",") if item.strip()]
    if not entries:
        return

    denial = ForbiddenException(
        detail="Platform console access is not permitted from this network.",
        required_permission="platform:allowlisted-ip",
    )
    try:
        networks = [ipaddress.ip_network(entry, strict=False) for entry in entries]
    except ValueError:
        raise denial from None

    host = _request_client_host(request)
    if not host:
        raise denial
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise denial from None
    if not any(address in network for network in networks):
        raise denial


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


def get_deal_service(db: DbSession, current_user: CurrentUser):
    """Provide a DealService scoped to the current request and acting user."""
    from app.modules.deals.service import DealService

    return DealService(db, current_user=current_user)


DealServiceDep = Annotated["DealService", Depends(get_deal_service)]  # noqa: F821


def get_deal_quote_service(db: DbSession, current_user: CurrentUser):
    """Provide a DealQuoteService (deals <-> quotes integration, #44)."""
    from app.modules.deals.quotes_integration import DealQuoteService

    return DealQuoteService(db, current_user=current_user)


DealQuoteServiceDep = Annotated[
    "DealQuoteService",  # noqa: F821
    Depends(get_deal_quote_service),
]


def get_nurture_service(db: DbSession, current_user: CurrentUser):
    """Provide a NurtureService scoped to the current request and acting user."""
    from app.modules.nurture.service import NurtureService

    return NurtureService(db, current_user=current_user)


NurtureServiceDep = Annotated["NurtureService", Depends(get_nurture_service)]  # noqa: F821


def get_onboarding_service(db: DbSession, current_user: CurrentUser):
    """Provide an OnboardingService scoped to the current request and acting user."""
    from app.modules.onboarding.service import OnboardingService

    return OnboardingService(db, current_user=current_user)


OnboardingServiceDep = Annotated[
    "OnboardingService", Depends(get_onboarding_service)  # noqa: F821
]


def get_expense_service(db: DbSession, current_user: CurrentUser) -> "ExpenseService":  # noqa: F821
    """Provide an ExpenseService scoped to the current request and acting user."""
    from app.modules.expenses.service import ExpenseService

    return ExpenseService(db, current_user=current_user)


ExpenseServiceDep = Annotated["ExpenseService", Depends(get_expense_service)]  # noqa: F821


def get_purchase_order_service(
    db: DbSession, current_user: CurrentUser
) -> "PurchaseOrderService":  # noqa: F821
    """Provide a PurchaseOrderService scoped to the request and acting user."""
    from app.modules.purchase_orders.service import PurchaseOrderService

    return PurchaseOrderService(db, current_user=current_user)


PurchaseOrderServiceDep = Annotated[
    "PurchaseOrderService", Depends(get_purchase_order_service)  # noqa: F821
]


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


def get_operator_mfa_service(db: DbSession, current_user: CurrentUser):
    """Provide an OperatorMfaService scoped to the current request and user."""
    from app.modules.platform.service import OperatorMfaService

    return OperatorMfaService(db, current_user=current_user)


OperatorMfaServiceDep = Annotated[
    "OperatorMfaService",  # noqa: F821
    Depends(get_operator_mfa_service),
]


def get_statements_service(db: DbSession, current_user: CurrentUser):
    """Provide a StatementsService scoped to the current request and acting user."""
    from app.modules.statements.service import StatementsService

    return StatementsService(db, current_user=current_user)


StatementsServiceDep = Annotated[
    "StatementsService", Depends(get_statements_service)  # noqa: F821
]


def get_dashboard_service(db: DbSession, current_user: CurrentUser):
    """Provide a DashboardService scoped to the current request and acting user."""
    from app.modules.dashboard.service import DashboardService

    return DashboardService(db, current_user=current_user)


DashboardServiceDep = Annotated[
    "DashboardService", Depends(get_dashboard_service)  # noqa: F821
]


def _start_report_snapshot(db: Session) -> None:
    """Start a read-only repeatable-read transaction for report queries."""
    if db.in_transaction():
        db.commit()

    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        db.execute(text("SET TRANSACTION READ ONLY"))


def get_reports_service(db: DbSession, current_user: CurrentUser):
    """Provide a report service on one consistent PostgreSQL read snapshot.

    Authentication has already read the user with this request session. End
    that short transaction before starting the report transaction so
    ``REPEATABLE READ`` is applied before any report query executes.

    SQLite remains on its normal test fallback; it does not prove PostgreSQL
    snapshot semantics.
    """
    from app.modules.reports.service import ReportsService

    _start_report_snapshot(db)
    return ReportsService(db, current_user=current_user)


ReportsServiceDep = Annotated["ReportsService", Depends(get_reports_service)]  # noqa: F821


def get_sales_desk_service(db: DbSession, current_user: CurrentUser):
    """Provide the Sales Desk analytics service on one read snapshot.

    Same contract as :func:`get_reports_service` (issue #45 mirrors the
    reports module): on PostgreSQL every dashboard / notification /
    overview / export request runs inside ONE read-only ``REPEATABLE
    READ`` transaction, so all its numbers are mutually consistent.
    """
    from app.modules.sales_desk.service import SalesDeskService

    _start_report_snapshot(db)
    return SalesDeskService(db, current_user=current_user)


SalesDeskServiceDep = Annotated[
    "SalesDeskService", Depends(get_sales_desk_service)  # noqa: F821
]
