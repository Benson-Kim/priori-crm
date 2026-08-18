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

from fastapi import APIRouter, Depends, Query

from app.common.dependencies import OwnerServiceDep, require_role
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

router = APIRouter(
    route_class=CommitOnSuccessRoute,
    dependencies=[Depends(require_role(UserRole.PLATFORM_OPERATOR))],
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
        "with no parameters the (large) default page returns every owner."
    ),
    responses={403: {"description": "Caller is not a platform operator"}},
)
def list_owners(
    service: OwnerServiceDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
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
        "(entity_type 'owner_module_setting') and tenant lifecycle changes "
        "(entity_type 'owner_profile'). Newest first. Read-only evidence — "
        "append-only is enforced by a database trigger (BEFORE UPDATE OR "
        "DELETE raises), not just by application convention. The trail "
        "records successful writes only: operator sign-ins, reads and "
        "denied attempts are not audited."
    ),
    responses={403: {"description": "Caller is not a platform operator"}},
)
def list_platform_audit(
    service: OwnerServiceDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
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
        "before/after state. Unknown owner ids return 404."
    ),
    responses={
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
        "implicitly. Every change is audited."
    ),
    responses={
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
