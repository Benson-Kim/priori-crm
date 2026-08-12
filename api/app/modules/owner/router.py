"""Owner / document-header API.

GET/PUT the single live owner profile, plus logo upload/remove/serve. Reads
require authentication; profile and logo writes require an elevated role
since this is an organisation-wide setting.
"""

from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import RedirectResponse, StreamingResponse

from app.common.dependencies import OwnerServiceDep, require_role
from app.common.reporting_time import reporting_date
from app.constants.enums import UserRole
from app.constants.settings_defaults import (
    DEFAULT_ONBOARDING_TASKS,
    DEFAULT_ORG_JURISDICTION,
    DEFAULT_PURCHASE_ORDER_TERMS,
)
from app.lib.config import settings
from app.modules.owner.schemas import OwnerProfileResponse, OwnerProfileUpdate

router = APIRouter()

_WRITE_ROLES = (UserRole.MANAGER, UserRole.ADMIN)


def _to_response(profile) -> OwnerProfileResponse:
    # Surface the org-scoped PO defaults as RESOLVED values (persisted value,
    # or the built-in fallback when never set) so the Settings screen and the
    # PO create form read a single authoritative source
    return OwnerProfileResponse(
        full_name=profile.full_name,
        location_watermark=profile.location_watermark,
        address=profile.address,
        email=profile.email,
        phone=profile.phone,
        vat_enabled=profile.vat_enabled,
        vat_rate=profile.vat_rate,
        tax_pin=profile.tax_pin,
        website=profile.website,
        default_terms_and_conditions=(
            profile.default_terms_and_conditions or DEFAULT_PURCHASE_ORDER_TERMS
        ),
        default_send_message=profile.default_send_message,
        jurisdiction=profile.jurisdiction or DEFAULT_ORG_JURISDICTION,
        onboarding_task_template=list(
            profile.onboarding_task_template or DEFAULT_ONBOARDING_TASKS
        ),
        onboarding_template_version=profile.onboarding_template_version,
        has_logo=bool(profile.logo_storage_key),
        updated_at=profile.updated_at,
        reporting_timezone=settings.REPORTING_TIMEZONE,
        reporting_date=reporting_date().isoformat(),
    )


@router.get("", response_model=OwnerProfileResponse, summary="Get the owner profile")
def get_owner_profile(service: OwnerServiceDep) -> OwnerProfileResponse:
    """Return the live owner profile (seeded from settings on first use)."""
    return _to_response(service.get_or_create())


@router.put(
    "",
    response_model=OwnerProfileResponse,
    summary="Update the owner profile",
    dependencies=[Depends(require_role(*_WRITE_ROLES))],
)
def update_owner_profile(
    body: OwnerProfileUpdate,
    service: OwnerServiceDep,
) -> OwnerProfileResponse:
    """Update the live owner profile. Does not affect already-issued documents."""
    return _to_response(service.update(body))


@router.put(
    "/logo",
    response_model=OwnerProfileResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload / replace the owner logo",
    dependencies=[Depends(require_role(*_WRITE_ROLES))],
)
def upload_owner_logo(
    service: OwnerServiceDep,
    file: UploadFile = File(...),
) -> OwnerProfileResponse:
    """Validate and store a logo, replacing any existing one."""
    mime_type = file.content_type or "application/octet-stream"
    profile = service.upload_logo(file.file, file.filename or "logo", mime_type)
    return _to_response(profile)


@router.delete(
    "/logo",
    response_model=OwnerProfileResponse,
    summary="Remove the owner logo",
    dependencies=[Depends(require_role(*_WRITE_ROLES))],
)
def remove_owner_logo(service: OwnerServiceDep) -> OwnerProfileResponse:
    """Remove the current logo."""
    return _to_response(service.remove_logo())


@router.get(
    "/logo",
    summary="Serve the owner logo",
    responses={
        200: {"content": {"application/octet-stream": {}}},
        302: {"description": "Redirect to a presigned URL (S3 backend)"},
        404: {"description": "No logo set"},
    },
)
def serve_owner_logo(service: OwnerServiceDep):
    """Stream the logo binary (or redirect to a presigned URL on S3).

    All storage access lives behind the service's public ``serve_logo`` API,
    so the router never depends on the storage backend.
    """
    download = service.serve_logo()
    if download.presigned_url is not None:
        return RedirectResponse(
            url=download.presigned_url, status_code=status.HTTP_302_FOUND
        )

    return StreamingResponse(download.stream, media_type=download.media_type)
