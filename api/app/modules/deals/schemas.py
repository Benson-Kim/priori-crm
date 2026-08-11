"""Pydantic schemas for the Sales Desk deals API.

Response field names are the frontend's screen contract (issue #39): the
UI renders ``age_days`` / ``idle_days`` / ``weighted_value``, the latest
record and the full stage history verbatim and never computes them.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.constants.enums import (
    BillingCurrency,
    DealHygieneBucket,
    DealLostReason,
    DealStage,
    DealStatus,
    DealTab,
    DealWonReason,
)

#: Drawer copy — the single validation message for note-less transitions.
NOTE_REQUIRED_MESSAGE = "A note is required to advance or close."


def _require_non_empty_note(value: str) -> str:
    """Strip and reject blank notes (422 via request validation)."""
    stripped = value.strip()
    if not stripped:
        raise ValueError(NOTE_REQUIRED_MESSAGE)
    return stripped


# REQUEST SCHEMAS


class DealCreate(BaseModel):
    """Request body for creating a new deal."""

    customer_id: UUID = Field(
        ..., description="Customer (company) this deal belongs to", alias="customerId"
    )
    owner_id: UUID = Field(
        ..., description="Sales rep who owns this deal", alias="ownerId"
    )
    product: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Product being sold",
    )
    seats: int = Field(..., gt=0, description="Number of seats/licences")
    value: Decimal = Field(
        ...,
        ge=0,
        decimal_places=2,
        description="Annual deal value (ARR) in the deal currency",
    )
    currency: BillingCurrency = Field(
        ...,
        description=(
            "Deal currency (USD|KES); must be one of the customer's "
            "billing-profile currencies"
        ),
    )
    note: str | None = Field(
        None,
        max_length=5000,
        description=(
            "Optional first stage-record note; defaults to 'Deal created.'"
        ),
    )

    model_config = {"populate_by_name": True}


class DealUpdate(BaseModel):
    """Request body for updating an open/parked deal's commercial fields."""

    owner_id: UUID | None = Field(None, alias="ownerId")
    product: str | None = Field(None, min_length=1, max_length=255)
    seats: int | None = Field(None, gt=0)
    value: Decimal | None = Field(None, ge=0, decimal_places=2)
    currency: BillingCurrency | None = Field(
        None,
        description=(
            "New deal currency; must be one of the customer's "
            "billing-profile currencies"
        ),
    )

    model_config = {"populate_by_name": True}


class DealAdvanceRequest(BaseModel):
    """Request body for advancing a deal one stage. The note is mandatory."""

    note: str = Field(
        ...,
        max_length=5000,
        description=NOTE_REQUIRED_MESSAGE,
    )

    _validate_note = field_validator("note")(_require_non_empty_note)


class DealActivityCreate(BaseModel):
    """Request body for logging an activity at the current stage."""

    note: str = Field(
        ...,
        max_length=5000,
        description="What happened — every stage keeps a record",
    )

    _validate_note = field_validator("note")(_require_non_empty_note)


class DealCloseRequest(BaseModel):
    """Request body for closing a deal won or lost.

    The reason must come from the enumerated won/lost list matching the
    result, and the note is mandatory.
    """

    result: Literal["won", "lost"] = Field(
        ..., description="Close outcome: won or lost"
    )
    reason: str = Field(
        ...,
        max_length=50,
        description="Main reason we won/lost (enumerated list)",
    )
    note: str = Field(
        ...,
        max_length=5000,
        description=NOTE_REQUIRED_MESSAGE,
    )

    _validate_note = field_validator("note")(_require_non_empty_note)

    @model_validator(mode="after")
    def validate_reason_matches_result(self) -> "DealCloseRequest":
        allowed = DealWonReason if self.result == "won" else DealLostReason
        values = [m.value for m in allowed]
        if self.reason not in values:
            raise ValueError(
                f"'{self.reason}' is not a valid {self.result} reason. "
                f"Allowed: {values}"
            )
        return self


class DealParkRequest(BaseModel):
    """Request body for parking a deal into the future pipeline."""

    parked_until: date = Field(
        ...,
        description="Re-engage date (must be in the future)",
        alias="parkedUntil",
    )
    note: str = Field(
        ...,
        max_length=5000,
        description="Why the deal is parked — every stage keeps a record",
    )

    _validate_note = field_validator("note")(_require_non_empty_note)

    model_config = {"populate_by_name": True}


class DealFilterParams(BaseModel):
    """List filters for the pipeline (tab, owner, hygiene, search)."""

    tab: DealTab = DealTab.ALL
    owner_id: UUID | None = None
    hygiene: DealHygieneBucket | None = None
    show_closed: bool = True
    search: str | None = None


# RESPONSE SCHEMAS


class DealOwnerResponse(BaseModel):
    """Owner as the deal list renders it: id, name, initials."""

    id: UUID
    name: str
    initials: str


class DealBillingProfileResponse(BaseModel):
    """Drawer billing line, joined from the customer's billing profile

    matching the deal currency (e.g. "Billing to NDG-KES · 14 days ·
    VAT 16% · Not synced").
    """

    code: str
    payment_terms: str
    tax_treatment: str
    synced: bool

    model_config = {"from_attributes": True}


class DealActivityResponse(BaseModel):
    """One stage-record entry (stage label, ISO date, note)."""

    id: UUID
    stage_label: str
    note: str
    activity_date: date
    author_id: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DealResponse(BaseModel):
    """A deal exactly as the pipeline list and drawer consume it.

    ``age_days``, ``idle_days``, ``weighted_value``, ``latest_record`` and
    ``stage_history`` are server-computed — the UI never derives them.
    """

    id: UUID
    customer_id: UUID
    company_name: str
    owner: DealOwnerResponse
    product: str
    seats: int
    value: Decimal
    currency: str

    stage: DealStage
    stage_index: int = Field(description="1-based stage position (Stage N of 5)")
    stage_total: int = Field(description="Total pipeline steps the UI renders (5)")
    stage_label: str
    status: DealStatus

    parked_until: date | None
    resume_stage: DealStage | None
    close_reason: str | None
    close_note: str | None
    closed_at: datetime | None

    age_days: int = Field(
        description="Days since the first record (UI: 'Nd in pipeline' / 'Nd total')"
    )
    idle_days: int = Field(
        description="Days since the latest record (UI: 'Last activity Nd ago')"
    )
    weighted_value: Decimal | None = Field(
        description=(
            "value × stage probability while the deal is in play; "
            "null once closed (UI renders '—')"
        )
    )

    latest_record: DealActivityResponse | None
    stage_history: list[DealActivityResponse]

    billing_profile: DealBillingProfileResponse | None = Field(
        description=(
            "Customer billing profile matching the deal currency "
            "(drawer billing line); null if the customer has none"
        )
    )

    created_at: datetime
    updated_at: datetime
    version: int


class DealStatusCounts(BaseModel):
    """Tab header counts: All (6) · Open (4) · Won (1) · Lost (1)."""

    all: int
    open: int
    won: int
    lost: int
    parked: int
