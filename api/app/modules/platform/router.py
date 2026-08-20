"""Platform-operator API (ADR-0011).

Owner-id-scoped module entitlement administration, restricted to the
PLATFORM_OPERATOR role. Entitlements are commercial grants made by the
platform, not tenant preferences: the tenant-facing write
(``PATCH /owner/modules/{module_key}``) was removed in favour of this
surface, and the owner Settings screen renders entitlements read-only.

Deliberately owner-id-scoped even though today's deployment holds a single
``owner_profiles`` row: when real multi-tenancy arrives, only owner
resolution changes — this API contract does not.

No ``require_module`` gate: platform administration is infrastructure and
must keep working even when every toggleable module is disabled.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.common.dependencies import (
    OperatorMfaServiceDep,
    OwnerServiceDep,
    enforce_platform_ip_allowlist,
    require_role,
    require_step_up,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.routing import CommitOnSuccessRoute
from app.constants.enums import ModuleKey, UserRole
from app.modules.owner.schemas import (
    ModuleSettingsResponse,
    ModuleSettingState,
    ModuleSettingUpdate,
    OwnerStatusUpdate,
    PlatformAuditEvent,
    PlatformOwnersResponse,
    PlatformOwnerSummary,
)
from app.modules.platform.schemas import (
    MfaActivationRequest,
    MfaActivationResponse,
    MfaEnrollmentResponse,
    MfaStatusResponse,
)

# The IP allowlist (ADR-0014 interim control; empty list = disabled) runs
# before the role gate so a disallowed network is rejected without touching
# authentication at all.
router = APIRouter(
    route_class=CommitOnSuccessRoute,
    dependencies=[
        Depends(enforce_platform_ip_allowlist),
        Depends(require_role(UserRole.PLATFORM_OPERATOR)),
    ],
)


@router.get(
    "/owners",
    response_model=PlatformOwnersResponse,
    summary="List owner profiles (platform operator only)",
    description=(
        "Paginated, name-searchable listing of the owner profiles the "
        "platform operator administers, with a per-owner summary "
        "(lifecycle status + disabled-module count). The operator role "
        "carries no access to tenant business data. Backward-compatible: "
        "with no parameters the default page (per_page=50) covers every "
        "owner up to 50 profiles — ample at today's ~10-tenant target; "
        "beyond that, paginate."
    ),
    responses={403: {"description": "Caller is not a platform operator"}},
)
def list_owners(
    service: OwnerServiceDep,
    page: Annotated[
        int, Query(ge=1, le=1000, description="Page number (1-indexed)")
    ] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
    search: Annotated[
        str | None,
        Query(description="Case-insensitive substring match on the display name"),
    ] = None,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Include total/total_pages (runs a COUNT(*); off by default)",
        ),
    ] = False,
) -> PlatformOwnersResponse:
    """Paginated, searchable owner listing for the operator console."""
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    return service.platform_owner_summaries(params, search=search)


@router.get(
    "/audit",
    response_model=PaginatedResponse[PlatformAuditEvent],
    summary="Operator audit trail (platform operator only)",
    description=(
        "Paginated, filterable view over the audit_events rows written by "
        "the owner service: entitlement grants/revocations "
        "(entity_type 'owner_module_setting'), tenant lifecycle changes "
        "(entity_type 'owner_profile') and operator-MFA events "
        "(entity_type 'operator_mfa', ADR-0014: enrollment lifecycle, "
        "recovery-code use, step-up grants AND denials). Newest first. "
        "Read-only evidence — append-only is enforced by a database "
        "trigger (BEFORE UPDATE OR DELETE raises), not just by "
        "application convention. Beyond the ADR-0014 MFA events, the "
        "trail records successful writes only: operator sign-ins and "
        "reads are not audited."
    ),
    responses={403: {"description": "Caller is not a platform operator"}},
)
def list_platform_audit(
    service: OwnerServiceDep,
    page: Annotated[
        int, Query(ge=1, le=1000, description="Page number (1-indexed)")
    ] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    owner_id: Annotated[
        uuid.UUID | None,
        Query(
            alias="ownerId",
            description=(
                "Only events attributable to this owner (lifecycle events "
                "directly; entitlement events via the setting row's owner FK)"
            ),
        ),
    ] = None,
    action: Annotated[
        str | None,
        Query(
            description=(
                "Exact action filter, e.g. module_enabled, module_disabled, "
                "owner_suspended, owner_reactivated"
            ),
        ),
    ] = None,
    actor_id: Annotated[
        uuid.UUID | None,
        Query(alias="actorId", description="Only events by this acting user"),
    ] = None,
    date_from: Annotated[
        datetime | None,
        Query(alias="dateFrom", description="Only events at/after this instant"),
    ] = None,
    date_to: Annotated[
        datetime | None,
        Query(alias="dateTo", description="Only events at/before this instant"),
    ] = None,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Include total/total_pages (runs a COUNT(*); off by default)",
        ),
    ] = False,
) -> PaginatedResponse[PlatformAuditEvent]:
    """Paginated, filterable operator audit trail."""
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    return service.platform_audit_events(
        params,
        owner_id=owner_id,
        action=action,
        actor_id=actor_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get(
    "/owners/{owner_id}/modules",
    response_model=ModuleSettingsResponse,
    summary="List one owner's module entitlements (platform operator only)",
    description=(
        "Every ModuleKey with its RESOLVED enabled state for the given "
        "owner (missing override row = enabled) and whether it is "
        "essential (never disableable)."
    ),
    responses={
        403: {"description": "Caller is not a platform operator"},
        404: {"description": "Unknown owner id"},
    },
)
def list_owner_module_settings(
    owner_id: uuid.UUID,
    service: OwnerServiceDep,
) -> ModuleSettingsResponse:
    """Return the effective entitlement state of every module for one owner."""
    return ModuleSettingsResponse(modules=service.module_settings_for_owner(owner_id))


@router.patch(
    "/owners/{owner_id}/status",
    response_model=PlatformOwnerSummary,
    summary="Suspend or reactivate one owner (platform operator only)",
    description=(
        "Set the tenant lifecycle status (active | suspended) of one owner "
        "profile (ADR-0013 Phase A). Reversible and non-destructive: no "
        "tenant data and no users.role is ever touched (QA finding 09). "
        "Suspension takes effect immediately on every authenticated route "
        "(live sessions included), denies non-essential modules and blocks "
        "non-operator sign-in/refresh; nothing is revoked, so reactivation "
        "restores existing sessions and tokens. Both directions are "
        "audited (owner_suspended / owner_reactivated) with actor and "
        "before/after state. Unknown owner ids return 404. Destructive: "
        "demands a fresh step-up proof (X-MFA-Code header, ADR-0014) — a "
        "live session is never sufficient."
    ),
    dependencies=[Depends(require_step_up("owner_status_change"))],
    responses={
        401: {"description": "Missing/invalid step-up proof (X-MFA-Code)"},
        403: {"description": "Caller is not a platform operator"},
        404: {"description": "Unknown owner id"},
        422: {"description": "Invalid status value"},
    },
)
def update_owner_status(
    owner_id: uuid.UUID,
    body: OwnerStatusUpdate,
    service: OwnerServiceDep,
) -> PlatformOwnerSummary:
    """Suspend or reactivate one owner (platform operator only; audited)."""
    profile = service.set_owner_status(owner_id, body.status)
    return PlatformOwnerSummary(
        id=profile.id,
        full_name=profile.full_name,
        status=profile.status,
        disabled_module_count=service.disabled_module_count(profile.id),
    )


@router.patch(
    "/owners/{owner_id}/modules/{module_key}",
    response_model=ModuleSettingState,
    summary="Grant or revoke one module for one owner (platform operator only)",
    description=(
        "Upsert the per-owner override for one toggleable module. Disabling "
        "an essential module (auth, owner, health, dashboard) returns 422. "
        "Unknown owner ids return 404 — an owner profile is never created "
        "implicitly. Every change is audited. Demands a fresh step-up "
        "proof (X-MFA-Code header, ADR-0014) for revocations AND grants — "
        "a live session is never sufficient."
    ),
    dependencies=[Depends(require_step_up("module_entitlement_change"))],
    responses={
        401: {"description": "Missing/invalid step-up proof (X-MFA-Code)"},
        403: {"description": "Caller is not a platform operator"},
        404: {"description": "Unknown owner id"},
        422: {"description": "Unknown module key, or essential module"},
    },
)
def update_owner_module_setting(
    owner_id: uuid.UUID,
    module_key: ModuleKey,
    body: ModuleSettingUpdate,
    service: OwnerServiceDep,
) -> ModuleSettingState:
    """Enable/disable one module for one owner (platform operator only)."""
    return service.set_module_enabled_for_owner(owner_id, module_key, body.enabled)


# Operator MFA (ADR-0014, issue #73)
#
# The only /platform routes reachable by a CONSTRAINED (enrollment-scoped)
# operator token — see _enforce_operator_mfa_scope in
# app/common/dependencies.py. Every endpoint acts on the authenticated
# operator itself: nothing here creates, promotes or demotes any account
# (QA finding 09), so enrollment applies to existing seeded operators only.


@router.get(
    "/mfa",
    response_model=MfaStatusResponse,
    summary="Own MFA enrollment status (platform operator only)",
    description=(
        "Whether the calling operator has an active (enforced) TOTP "
        "enrollment, a pending unconfirmed one, and how many single-use "
        "recovery codes remain. Reachable by enrollment-scoped tokens."
    ),
    responses={403: {"description": "Caller is not a platform operator"}},
)
def mfa_status(service: OperatorMfaServiceDep) -> MfaStatusResponse:
    """Return the calling operator's own MFA enrollment state."""
    return MfaStatusResponse(**service.status())


@router.post(
    "/mfa/enrollment",
    response_model=MfaEnrollmentResponse,
    summary="Start (or rotate) own TOTP enrollment (platform operator only)",
    description=(
        "Provision a fresh TOTP seed for the calling operator and return "
        "it exactly once (base32 secret + otpauth URI for authenticator "
        "apps); only the encrypted form is persisted and nothing is "
        "logged. A pending enrollment may be freely restarted. Rotating "
        "an ACTIVE enrollment additionally demands a fresh step-up proof "
        "(X-MFA-Code header). Audited (mfa_enrollment_started)."
    ),
    responses={
        401: {"description": "Rotation without a valid step-up proof"},
        403: {"description": "Caller is not a platform operator"},
    },
)
def start_mfa_enrollment(
    service: OperatorMfaServiceDep,
    x_mfa_code: Annotated[
        str | None,
        Header(
            alias="X-MFA-Code",
            description=(
                "Step-up proof, required only when rotating an active enrollment"
            ),
        ),
    ] = None,
) -> MfaEnrollmentResponse:
    """Provision (or rotate, with step-up proof) the caller's TOTP seed."""
    secret, otpauth_uri = service.start_enrollment(x_mfa_code)
    return MfaEnrollmentResponse(
        secret=secret,
        otpauth_uri=otpauth_uri,
        message=(
            "Add this secret to your authenticator app, then confirm with "
            "POST /platform/mfa/enrollment/activate. It is shown only once."
        ),
    )


@router.post(
    "/mfa/enrollment/activate",
    response_model=MfaActivationResponse,
    summary="Confirm own TOTP enrollment with a live code (platform operator only)",
    description=(
        "Verify a live authenticator code against the pending seed. On "
        "success the enrollment becomes ACTIVE — from then on operator "
        "sign-in REQUIRES the TOTP (or a recovery code) and full console "
        "tokens are only issued with it — and the single-use recovery "
        "codes are returned exactly once (store them securely; only "
        "hashes are persisted). Rate-limited; failures are audited. "
        "Existing enrollment-scoped tokens stay constrained: sign in "
        "again to obtain a full console token."
    ),
    responses={
        400: {"description": "No pending enrollment to activate"},
        401: {"description": "Invalid or expired authenticator code"},
        403: {"description": "Caller is not a platform operator"},
        429: {"description": "Too many attempts"},
    },
)
def activate_mfa_enrollment(
    body: MfaActivationRequest,
    service: OperatorMfaServiceDep,
) -> MfaActivationResponse:
    """Confirm the pending enrollment; returns recovery codes exactly once."""
    codes = service.activate_enrollment(body.code)
    return MfaActivationResponse(
        recovery_codes=codes,
        message=(
            "MFA is now enforced for your account. Store these recovery "
            "codes securely — they are shown only once and each works "
            "once. Sign in again with your authenticator code to obtain "
            "a full console session."
        ),
    )
