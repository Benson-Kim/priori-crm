"""Pydantic schemas for the owner / document-header module.

Reuses the shared validators (normalize_phone, empty_str_to_none, website
normaliser) rather than re-implementing them.
"""

import re
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.common.validators import (
    empty_str_to_none,
    normalize_phone,
    validate_country_code,
)
from app.constants.settings_defaults import (
    MAX_DEFAULT_SEND_MESSAGE_LENGTH,
    MAX_DEFAULT_TERMS_LENGTH,
)

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
    vat_enabled: bool = Field(False, alias="vatEnabled")
    vat_rate: float | None = Field(None, alias="vatRate")
    tax_pin: str | None = Field(None, max_length=50, alias="taxPin")
    website: str | None = Field(None, max_length=255)
    # Org-scoped document-settings defaults
    default_terms_and_conditions: str | None = Field(
        None,
        max_length=MAX_DEFAULT_TERMS_LENGTH,
        alias="defaultTermsAndConditions",
    )
    default_send_message: str | None = Field(
        None,
        max_length=MAX_DEFAULT_SEND_MESSAGE_LENGTH,
        alias="defaultSendMessage",
    )
    jurisdiction: str | None = Field(None, max_length=2)

    model_config = {"populate_by_name": True}

    @field_validator(
        "full_name",
        "location_watermark",
        "address",
        "vat_enabled",
        "vat_rate",
        "tax_pin",
        "default_terms_and_conditions",
        "default_send_message",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return empty_str_to_none(v)

    @field_validator("jurisdiction", mode="before")
    @classmethod
    def _normalise_jurisdiction(cls, v: str | None) -> str | None:
        # Reuse the shared ISO 3166-1 alpha-2 validator (uppercases, blank ->
        # None, rejects non 2-letter codes) so the jurisdiction contract is
        # identical wherever a country code is accepted.
        return validate_country_code(v)

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
    vat_enabled: bool = Field(False, alias="vatEnabled")
    vat_rate: float | None = Field(None, alias="vatRate")
    tax_pin: str | None = Field(None, alias="taxPin")
    website: str | None = None
    # Org-scoped document-settings defaults
    # Returned as the resolved values (persisted value, or the built-in
    # fallback when never set) so the frontend Settings screen and the PO
    # create form read a single authoritative source.
    default_terms_and_conditions: str | None = Field(
        None, alias="defaultTermsAndConditions"
    )
    default_send_message: str | None = Field(None, alias="defaultSendMessage")
    jurisdiction: str | None = None
    # True when a logo is set; the binary is served from a dedicated endpoint
    # so the JSON never carries a path.
    has_logo: bool = Field(False, alias="hasLogo")
    updated_at: datetime | None = Field(None, alias="updatedAt")

    model_config = {"populate_by_name": True, "from_attributes": True}


class OwnerInfo(BaseModel):
    """DTO passed into PDF/email rendering .

    Built from either the live profile or an immutable snapshot, so the
    renderer never depends on the global ``settings`` singleton for branding.
    """

    full_name: str = Field(..., alias="fullName")
    location_watermark: str | None = Field(None, alias="locationWatermark")
    address: str | None = None
    email: str | None = None
    phone: str | None = None
    vat_enabled: bool = Field(False, alias="vatEnabled")
    vat_rate: float | None = Field(None, alias="vatRate")
    tax_pin: str | None = Field(None, alias="taxPin")
    website: str | None = None
    logo_storage_key: str | None = None
    # Jurisdiction (frozen on the snapshot at issue time) so the renderer can
    # resolve the jurisdiction-aware compliance-reference label (PO-10)
    # without reading the live profile.
    jurisdiction: str | None = None

    model_config = {"from_attributes": True}


class PurchaseOrderSettingsDefaults(BaseModel):
    """Resolved org-scoped defaults applied when creating a Purchase Order.

    Built by the owner service from the live profile, with the built-in
    constants substituted whenever a field was never set. Consumed by
    ``PurchaseOrderService.create`` to fill omitted fields at create time only.
    """

    terms_and_conditions: str
    send_message: str | None = None
    jurisdiction: str
