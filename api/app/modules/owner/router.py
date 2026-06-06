"""Owner / document-header API (W3.6).

GET/PUT the single live owner profile, plus logo upload/remove/serve. Reads
require authentication; profile and logo writes require an elevated role
since this is an organisation-wide setting.
"""
from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import RedirectResponse, StreamingResponse

from app.common.dependencies import OwnerServiceDep, require_role
from app.common.exceptions import NotFoundException
from app.constants.enums import UserRole
from app.modules.owner.schemas import OwnerProfileResponse, OwnerProfileUpdate

router = APIRouter()

_WRITE_ROLES = (UserRole.MANAGER, UserRole.ADMIN)


def _to_response(profile) -> OwnerProfileResponse:
    return OwnerProfileResponse(
        full_name=profile.full_name,
        location_watermark=profile.location_watermark,
        address=profile.address,
        email=profile.email,
        phone=profile.phone,
        tax_pin=profile.tax_pin,
        website=profile.website,
        has_logo=bool(profile.logo_storage_key),
        updated_at=profile.updated_at,
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
    """Stream the logo binary (or redirect to a presigned URL on S3)."""
    profile = service.get_or_create()
    key = profile.logo_storage_key
    if not key:
        raise NotFoundException(detail="No logo set", resource="logo")

    presigned = service._storage.presigned_url(key)
    if presigned:
        return RedirectResponse(url=presigned, status_code=status.HTTP_302_FOUND)

    return StreamingResponse(
        service._storage.download_stream(key),
        media_type="application/octet-stream",
    )
