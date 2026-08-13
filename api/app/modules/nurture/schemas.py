"""Pydantic schemas for the Sales Desk future pipeline API (issue #40).

Response field names are the frontend's screen contract
(``Future_Pipeline.svg`` / ``App__2_.svg``): items carry ``company``,
``contact``, ``note``, ``engage_on`` (ISO), ``est_arr``, ``owner``
(id/name/initials) and the server-computed ``due_state`` / ``dot_state``.
The UI renders these verbatim and never does date math.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.constants.enums import BillingCurrency
from app.modules.customers.schemas import CustomerCreate
from app.modules.deals.schemas import DealOwnerResponse

# REQUEST SCHEMAS


class NurtureProspectCreate(BaseModel):
    """Request body for the '+ Add planned deal' form (``App__2_.svg``)."""

    company: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Company we plan to engage (required)",
    )
    contact: str | None = Field(
        None, max_length=255, description="Contact person at the company"
    )
    owner_id: UUID | None = Field(
        None, description="Sales rep who owns this prospect", alias="ownerId"
    )
    engage_on: date | None = Field(
        None,
        description=(
            "Engage-by date; the UI defaults it to +90 days and the server "
            "applies the same org-local default when omitted"
        ),
        alias="engageOn",
    )
    est_arr: Decimal | None = Field(
        None,
        ge=0,
        decimal_places=2,
        description="Estimated annual value (est. ARR) if we win the company",
        alias="estArr",
    )
    note: str | None = Field(None, max_length=5000, description="Why/when to engage")

    model_config = {"populate_by_name": True}


class NurtureEngageRequest(BaseModel):
    """Request body for the 'Start engaging →' action.

    Engaging atomically creates a customer + an open deal at stage
    Activation and removes the prospect. Either a full ``customer`` payload
    (validated/deduplicated by the existing customers service) or a
    ``customer_id`` linking an existing customer must be provided — the
    latter is the re-submit path after a duplicate-email 409.
    """

    customer: CustomerCreate | None = Field(
        None,
        description=(
            "New-customer payload (existing customers-service validation "
            "and dedup apply, incl. case-insensitive email uniqueness)"
        ),
    )
    customer_id: UUID | None = Field(
        None,
        description=(
            "Link the deal to this existing customer instead of creating "
            "one — the re-submit option offered by the duplicate-email 409"
        ),
        alias="customerId",
    )
    owner_id: UUID | None = Field(
        None,
        description="Deal owner; defaults to the prospect's owner",
        alias="ownerId",
    )
    product: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description=(
            "Product being sold; defaults to the first entry of the org's "
            "sales-pricing catalog, since 'Start engaging' is a one-click "
            "action that collects nothing (#40, #48)"
        ),
    )
    seats: int | None = Field(
        None,
        gt=0,
        description="Number of seats/licences; defaults to 1",
    )
    value: Decimal | None = Field(
        None,
        ge=0,
        decimal_places=2,
        description="Annual deal value (ARR); defaults to the prospect's est. ARR",
    )
    currency: BillingCurrency | None = Field(
        None,
        description=(
            "Deal currency (USD|KES); must be one of the customer's "
            "billing-profile currencies. Defaults to the customer's primary "
            "billing currency."
        ),
    )
    note: str | None = Field(
        None,
        max_length=5000,
        description=(
            "First stage-record note of the deal; defaults to the prospect's note"
        ),
    )

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_customer_source(self) -> "NurtureEngageRequest":
        """Exactly one customer source: a new payload or an existing link."""
        if (self.customer is None) == (self.customer_id is None):
            raise ValueError(
                "Provide either a new 'customer' payload or an existing "
                "'customer_id' (but not both)."
            )
        return self


# RESPONSE SCHEMAS


class NurtureDueState(BaseModel):
    """Server-computed due badge, rendered directly by the UI.

    ``state`` is ``overdue`` / ``today`` / ``in_days`` (with ``in_days``
    carrying N), computed against the org-local accounting date boundary.
    ``label`` is the chip text verbatim: "Overdue", "Today", "45d".
    """

    state: Literal["overdue", "today", "in_days"]
    in_days: int | None = Field(
        None, description="Days until engage_on when state == 'in_days'"
    )
    label: str = Field(description="Chip text: 'Overdue' | 'Today' | 'Nd'")


class NurtureProspectResponse(BaseModel):
    """One nurture-list / planned-prospects row, exactly as rendered."""

    id: UUID
    company: str
    contact: str | None
    note: str | None
    engage_on: date = Field(description="Engage-by date (ISO)")
    est_arr: Decimal | None = Field(
        description="Estimated annual value (est. ARR); null if unknown"
    )
    owner: DealOwnerResponse | None = Field(
        description="Owner (id/name/initials); null if unassigned"
    )
    due_state: NurtureDueState = Field(
        description="Server-computed badge — the UI never does date math"
    )
    dot_state: Literal["due", "planned"] = Field(
        description="Status dot: due=amber / planned=green (prospects table)"
    )
    created_at: datetime
    updated_at: datetime


class NurtureSummary(BaseModel):
    """Header aggregates for the nurture list / planned-prospects table.

    Renders "Nurture list — 4 companies · est. $67,020 potential ARR" and
    the "3" / "1 due now" header counts.
    """

    count: int = Field(description="Number of prospects (companies)")
    total_est_arr: Decimal = Field(description="Sum of est. ARR across the prospects")
    due_now: int = Field(description="Prospects due now (engage_on <= org-local today)")


class NurtureNotificationCounts(BaseModel):
    """Due counts feeding the sidebar badge / notification bell (#45)."""

    due_nurture: int = Field(description="Prospects with engage_on <= org-local today")
    upcoming_nurture: int = Field(
        description="Prospects due within the next 14 days (exclusive of today)"
    )
    due_parked: int = Field(
        description="Parked deals with parked_until <= org-local today"
    )
