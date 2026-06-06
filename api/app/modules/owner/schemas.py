"""Pydantic schemas for the owner / document-header module (W3.6).

Reuses the shared validators (normalize_phone, empty_str_to_none, website
normaliser) rather than re-implementing them (W-7).
"""
import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.common.validators import empty_str_to_none, normalize_phone

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _normalise_website(v: str | None) -> str | None:
    """Prefix https:// when no scheme present; blank/None -> None."""
    cleaned = empty_str_to_none(v)
    if cleaned is None:
        return None
    stripped = cleaned.strip()
    if not _URL_SCHEME_RE.match(stripped):
        return f"https://{stripped}"
    return stripped


class OwnerProfileUpdate(BaseModel):
    """Editable owner-profile fields (the PUT body)."""

    full_name: str = Field(..., min_length=1, max_length=255, alias="fullName")
    location_watermark: str | None = Field(
        None, max_length=255, alias="locationWatermark"
    )
    address: str | None = Field(None, max_length=5000)
    email: EmailStr | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=30)
    tax_pin: str | None = Field(None, max_length=50, alias="taxPin")
    website: str | None = Field(None, max_length=255)

    model_config = {"populate_by_name": True}

    @field_validator(
        "full_name", "location_watermark", "address", "tax_pin", mode="before"
    )
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return empty_str_to_none(v)

    @field_validator("email", mode="before")
    @classmethod
    def _normalise_email(cls, v: str | None) -> str | None:
        cleaned = empty_str_to_none(v)
        return cleaned.lower() if cleaned else None

    @field_validator("phone", mode="before")
    @classmethod
    def _normalise_phone(cls, v: str | None) -> str | None:
        cleaned = empty_str_to_none(v)
        if cleaned is None:
            return None
        return normalize_phone(cleaned)

    @field_validator("website", mode="before")
    @classmethod
    def _normalise_website(cls, v: str | None) -> str | None:
        return _normalise_website(v)


class OwnerProfileResponse(BaseModel):
    """The live owner profile returned by GET/PUT."""

    full_name: str = Field(..., alias="fullName")
    location_watermark: str | None = Field(None, alias="locationWatermark")
    address: str | None = None
    email: str | None = None
    phone: str | None = None
    tax_pin: str | None = Field(None, alias="taxPin")
    website: str | None = None
    # True when a logo is set; the binary is served from a dedicated endpoint
    # so the JSON never carries a path.
    has_logo: bool = Field(False, alias="hasLogo")
    updated_at: datetime | None = Field(None, alias="updatedAt")

    model_config = {"populate_by_name": True, "from_attributes": True}


class OwnerInfo(BaseModel):
    """DTO passed into PDF/email rendering (V-SOLID-2/3).

    Built from either the live profile or an immutable snapshot, so the
    renderer never depends on the global ``settings`` singleton for branding.
    """

    full_name: str
    location_watermark: str | None = None
    address: str | None = None
    email: str | None = None
    phone: str | None = None
    tax_pin: str | None = None
    website: str | None = None
    logo_storage_key: str | None = None

    model_config = {"from_attributes": True}
