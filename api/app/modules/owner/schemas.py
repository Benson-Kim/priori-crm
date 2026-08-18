"""Pydantic schemas for the owner / document-header module.

Reuses the shared validators (normalize_phone, empty_str_to_none, website
normaliser) rather than re-implementing them.
"""

import re
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.common.validators import (
    empty_str_to_none,
    normalize_phone,
    validate_country_code,
)
from app.constants.enums import DealStage, OwnerStatus
from app.constants.settings_defaults import (
    MAX_DEFAULT_SEND_MESSAGE_LENGTH,
    MAX_DEFAULT_TERMS_LENGTH,
    MAX_ONBOARDING_TASK_LABEL_LENGTH,
    MAX_ONBOARDING_TEMPLATE_TASKS,
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
    # Onboarding task template (issue #41). Ordered list of task labels;
    # null resets the org back to the built-in 7-task default. Applied to
    # a checklist at create time only — editing the template never mutates
    # existing checklists.
    onboarding_task_template: list[str] | None = Field(
        None,
        min_length=1,
        max_length=MAX_ONBOARDING_TEMPLATE_TASKS,
        alias="onboardingTaskTemplate",
    )
    # Sales Desk analytics settings (issue #45). Null resets the org back
    # to the built-in defaults; both are resolved read-side.
    deal_stage_probabilities: dict[str, str] | None = Field(
        None,
        alias="dealStageProbabilities",
        description=(
            "Per-stage win probabilities (stage value -> decimal string "
            "0..1). All four open stages required when set; null resets to "
            "the built-in 0.10/0.25/0.50/0.75 defaults."
        ),
    )
    rep_quarterly_targets: dict[str, str] | None = Field(
        None,
        alias="repQuarterlyTargets",
        description=(
            "Per-rep quarterly won-value targets in USD (user id -> "
            "non-negative decimal string). Reps without an entry fall back "
            "to the built-in default; null resets every rep to it."
        ),
    )

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

    @field_validator("onboarding_task_template")
    @classmethod
    def _validate_template_labels(cls, v: list[str] | None) -> list[str] | None:
        """Strip labels; reject blank or over-long entries."""
        if v is None:
            return None
        cleaned: list[str] = []
        for label in v:
            stripped = (label or "").strip()
            if not stripped:
                raise ValueError("Onboarding task labels cannot be blank")
            if len(stripped) > MAX_ONBOARDING_TASK_LABEL_LENGTH:
                raise ValueError(
                    "Onboarding task labels must be at most "
                    f"{MAX_ONBOARDING_TASK_LABEL_LENGTH} characters"
                )
            cleaned.append(stripped)
        return cleaned

    @field_validator("deal_stage_probabilities")
    @classmethod
    def _validate_stage_probabilities(
        cls, v: dict[str, str] | None
    ) -> dict[str, str] | None:
        """Require all four open stages, each with a decimal in [0, 1]."""
        if v is None:
            return None
        expected = {stage.value for stage in DealStage}
        if set(v) != expected:
            raise ValueError(
                "Stage probabilities must cover exactly the open stages: "
                f"{sorted(expected)}"
            )
        normalised: dict[str, str] = {}
        for stage, raw in v.items():
            try:
                probability = Decimal(str(raw).strip())
            except (InvalidOperation, ValueError) as e:
                raise ValueError(
                    f"Probability for stage '{stage}' is not a decimal number"
                ) from e
            if not Decimal("0") <= probability <= Decimal("1"):
                raise ValueError(
                    f"Probability for stage '{stage}' must be between 0 and 1"
                )
            normalised[stage] = str(probability)
        return normalised

    @field_validator("rep_quarterly_targets")
    @classmethod
    def _validate_rep_targets(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        """Keys must be user UUIDs; values non-negative USD decimals."""
        if v is None:
            return None
        normalised: dict[str, str] = {}
        for key, raw in v.items():
            try:
                user_id = uuid.UUID(str(key))
            except ValueError as e:
                raise ValueError(
                    f"Rep target key '{key}' is not a valid user id"
                ) from e
            try:
                target = Decimal(str(raw).strip())
            except (InvalidOperation, ValueError) as e:
                raise ValueError(
                    f"Quarterly target for rep '{key}' is not a decimal number"
                ) from e
            if target < 0:
                raise ValueError(
                    f"Quarterly target for rep '{key}' must be non-negative"
                )
            normalised[str(user_id)] = str(target)
        return normalised

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
    # Resolved onboarding task template (persisted value, or the built-in
    # 7-task default when never set) + its monotonic version (issue #41).
    onboarding_task_template: list[str] = Field(alias="onboardingTaskTemplate")
    onboarding_template_version: int = Field(alias="onboardingTemplateVersion")
    # Sales Desk analytics settings (issue #45), resolved: probabilities are
    # returned with the built-in defaults applied; targets are the persisted
    # per-rep mapping (empty when never set) plus the default applied to any
    # rep without an entry.
    deal_stage_probabilities: dict[str, str] = Field(alias="dealStageProbabilities")
    rep_quarterly_targets: dict[str, str] = Field(alias="repQuarterlyTargets")
    default_rep_quarterly_target: str = Field(alias="defaultRepQuarterlyTarget")
    # True when a logo is set; the binary is served from a dedicated endpoint
    # so the JSON never carries a path.
    has_logo: bool = Field(False, alias="hasLogo")
    # Per-owner module entitlements: module_key -> effective enabled state
    # (missing owner_module_settings row = enabled). The UI bootstrap reads
    # this map to hide nav entries and gate routes for disabled modules.
    enabled_modules: dict[str, bool] = Field(
        default_factory=dict, alias="enabledModules"
    )
    updated_at: datetime | None = Field(None, alias="updatedAt")
    reporting_timezone: str = Field(alias="reportingTimezone")
    reporting_date: str = Field(alias="reportingDate")

    model_config = {"populate_by_name": True, "from_attributes": True}


class ModuleSettingState(BaseModel):
    """Effective entitlement state of one application module.

    ``enabled`` is the RESOLVED state (explicit override, or the default-on
    when no ``owner_module_settings`` row exists). ``essential`` marks core
    modules that can never be disabled (the UI renders them locked).
    """

    module_key: str = Field(alias="moduleKey")
    enabled: bool
    essential: bool

    model_config = {"populate_by_name": True}


class ModuleSettingsResponse(BaseModel):
    """Every module key with its effective state (GET /owner/modules)."""

    modules: list[ModuleSettingState]


class ModuleSettingUpdate(BaseModel):
    """PATCH /platform/owners/{owner_id}/modules/{module_key} body."""

    enabled: bool


class PlatformOwnerSummary(BaseModel):
    """One owner profile as listed on the platform-operator surface.

    Deliberately identity + lifecycle only (id, display name, status,
    disabled-module count): the operator manages entitlements and tenant
    lifecycle, not tenant business data (ADR-0011, ADR-0013 Phase A).
    """

    id: uuid.UUID
    full_name: str = Field(alias="fullName")
    # Tenant lifecycle state (active | suspended), operator-set via the
    # audited status PATCH (ADR-0013 Phase A).
    status: OwnerStatus = OwnerStatus.ACTIVE
    # Number of modules with an explicit enabled=false override — a quick
    # per-owner entitlement summary for the console list.
    disabled_module_count: int = Field(0, alias="disabledModuleCount")

    model_config = {"populate_by_name": True, "from_attributes": True}


class PlatformOwnersResponse(BaseModel):
    """Every owner profile (GET /platform/owners)."""

    owners: list[PlatformOwnerSummary]


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

    model_config = {"populate_by_name": True, "from_attributes": True}


class OnboardingTemplate(BaseModel):
    """Resolved onboarding task template + its version (issue #41).

    Built by the owner service from the live profile, with the built-in
    ``DEFAULT_ONBOARDING_TASKS`` substituted when the org never configured
    its own. Consumed by ``OnboardingService.create_for_won_deal``, which
    copies the labels (and snapshots the version) at create time only.
    """

    tasks: list[str]
    version: int


class PurchaseOrderSettingsDefaults(BaseModel):
    """Resolved org-scoped defaults applied when creating a Purchase Order.

    Built by the owner service from the live profile, with the built-in
    constants substituted whenever a field was never set. Consumed by
    ``PurchaseOrderService.create`` to fill omitted fields at create time only.
    """

    terms_and_conditions: str
    send_message: str | None = None
    jurisdiction: str


class SalesPriceListEntry(BaseModel):
    """One product row of the Sales Desk price list (issue #44).

    Raw catalog value plus the two derived display columns of
    ``Quotes___Pricing.svg`` ("Annual / seat" = monthly x 12 at list,
    "10-seat ARR" = monthly x 12 x 10), server-derived so the UI renders
    them verbatim.
    """

    name: str
    usd_per_seat_month: str = Field(
        description="List price per seat per month in USD (decimal string)"
    )
    usd_per_seat_year: str = Field(
        description="Annual / seat column: usd_per_seat_month x 12 (list, decimal string)"
    )
    usd_ten_seat_arr: str = Field(
        description="10-seat ARR column: usd_per_seat_month x 12 x 10 (decimal string)"
    )


class SalesPricingSettings(BaseModel):
    """Org sales pricing settings + derived price list (issue #44).

    This settings read IS the documented price-list source for the Quotes
    & pricing UI (#49): the seeded product catalog with the derived
    Annual/seat and 10-seat ARR columns, the annual billing discount
    applied by the quote builder, and the display-only FX conventions
    (units of currency per 1 USD) used for the USD-equivalent column and
    the reference-currency conversions row.
    """

    catalog: list[SalesPriceListEntry]
    annual_billing_discount_pct: str = Field(
        description=(
            "Discount (percentage points) applied to the per-user/month "
            "price when a quote line is billed annually"
        )
    )
    fx_units_per_usd: dict[str, str] = Field(
        description=(
            "Display-only FX conventions: units of each currency per 1 "
            "USD. EUR/GBP are reference (display-only) currencies."
        )
    )
