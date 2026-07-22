"""
Pydantic schemas for the Vendors module API.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    computed_field,
    field_validator,
)

from app.common.reporting_time import reporting_date
from app.common.validators import (
    empty_str_to_none as _empty_str_to_none,
)
from app.constants.enums import Currency, VendorStatus

# Helpers

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)


def _normalise_website(v: str | None) -> str | None:
    """
    Add https:// prefix when no scheme is present.
    Returns None for blank / None values.
    Mirrors the ORM-layer validator so the schema can return the normalised
    value in validation errors before it ever reaches the DB.
    """
    if v is None:
        return None
    stripped = v.strip()
    if not stripped:
        return None
    if not _URL_SCHEME_RE.match(stripped):
        return f"https://{stripped}"
    return stripped


class VendorBase(BaseModel):
    """
    Schema for creating a new vendor.

    Covers 2 modal paths:
        Path A (new from scratch): all fields filled manually.
        Path B (from existing contact): same fields, pre-populated from contact;
            contact_id is set so the service can record the linkage.

    Only vendor_name is required
    """

    vendor_name: str = Field(..., min_length=1, max_length=255, alias="vendorName")
    address: str | None = Field(None, max_length=5000)
    email: EmailStr | None = Field(None, max_length=320)
    country: str | None = Field(None, max_length=100)
    phone_primary: str | None = Field(None, max_length=50, alias="phonePrimary")
    phone_secondary: str | None = Field(None, max_length=50, alias="phoneSecondary")
    website: str | None = Field(None, max_length=500)
    vat_number: str | None = Field(None, max_length=100, alias="vatNumber")
    tax_id_pin: str | None = Field(None, max_length=100, alias="taxIdPin")
    currency: Currency | None = Field(default=Currency.KES)
    notes: str | None = Field(None, max_length=5000, alias="notes")

    # Set only when vendor is created from an existing contact
    contact_id: UUID | None = Field(None, alias="contactId")

    # Field Validators

    @field_validator(
        "vendor_name",
        "address",
        "country",
        "phone_primary",
        "phone_secondary",
        "vat_number",
        "tax_id_pin",
        "notes",
        mode="before",
    )
    @classmethod
    def coerce_blank_to_none_or_strip(cls, v: str | None) -> str | None:
        return _empty_str_to_none(v)

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, v: str | None) -> str | None:
        cleaned = _empty_str_to_none(v)
        return cleaned.lower() if cleaned else None

    @field_validator("website", mode="before")
    @classmethod
    def normalise_website(cls, v: str | None) -> str | None:
        """Normalise URL: strip, add https:// if no scheme present."""
        return _normalise_website(v)

    @field_validator("currency", mode="after")
    @classmethod
    def validate_currency_code(cls, v: str) -> str:
        """Ensure currency is a 3-character uppercase code."""
        upper = v.upper()
        if len(upper) != 3:
            raise ValueError("currency must be a 3-character ISO 4217 code")
        return upper

    @field_validator("vendor_name", mode="after")
    @classmethod
    def vendor_name_not_blank(cls, v: str | None) -> str:
        """After stripping, vendor name must not be empty."""
        if not v or not v.strip():
            raise ValueError("Please enter a vendor name.")
        return v

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "vendorName": "Priori Virtual Studio Ltd",
                "address": "Watermark Business Park, Nairobi",
                "email": "accounts@prioritech.com",
                "country": "Kenya",
                "phonePrimary": "+254700000000",
                "phoneSecondary": None,
                "website": "www.prioritech.com",
                "currency": "KES",
                "vatNumber": "V0012345X",
                "taxIdPin": "P09GIDHWOKD",
                "notes": None,
                "contactId": None,
            }
        },
    }


class VendorCreate(VendorBase):
    """Schema for creating a vendor from scratch or from an existing contact."""

    vendor_name: str = Field(..., min_length=1, max_length=255, alias="vendorName")
    contact_id: UUID | None = Field(None, alias="contactId")

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "vendorName": "Priori Virtual Studio Ltd",
                "address": "Watermark",
                "email": "finance@prioritech.com",
                "country": "Kenya",
                "phonePrimary": "+254 700 000 000",
                "phoneSecondary": "+254 711 000 000",
                "website": "www.prioritech.com",
                "vatNumber": "VAT-001",
                "taxIdPin": "P09GIDHWOKD",
                "currency": "KES",
                "contactId": None,
            }
        },
    }


class VendorUpdate(VendorBase):
    """Schema for editing an existing vendor profile.

    `status` is intentionally NOT editable here: lifecycle changes must
    go through the dedicated activate/deactivate endpoints so the
    `_transition()` state machine and version bump are enforced. Allowing a
    plain PUT to set status would bypass ALLOWED_TRANSITIONS.

    All fields are optional: an update accepts a partial payload and only the
    provided fields are applied (the service uses exclude_unset).
    """

    vendor_name: str | None = Field(
        None, min_length=1, max_length=255, alias="vendorName"
    )
    contact_id: UUID | None = Field(None, alias="contactId")

    model_config = {"populate_by_name": True}


# Vendor Response Schemas


class VendorResponse(BaseModel):
    """
    Complete vendor responses
    """

    id: UUID
    vendor_name: str
    address: str | None = None
    email: str | None = None
    country: str | None = None
    phone_primary: str | None = None
    phone_secondary: str | None = None
    website: str | None = None
    currency: str
    vat_number: str | None = None
    tax_id_pin: str | None = None
    notes: str | None = None
    status: str
    contact_id: UUID | None = None

    created_at: datetime
    updated_at: datetime
    created_by: UUID | None = None
    version: int

    # Injected by service layer
    vendor_since_year: int = Field(
        ...,
        description="Year extracted from created_at",
    )
    display_initials: str = Field(
        ...,
        description="Two-letter avatar initials derived from vendor_name",
    )
    payables: Decimal = Field(
        default=Decimal("0.00"),
        description=(
            "Total outstanding balance: SUM(balance) for pending + overdue transactions. "
        ),
    )
    total_unpaid: Decimal = Field(
        default=Decimal("0.00"),
        description="Same as payables.",
    )
    overdue_total: Decimal = Field(
        default=Decimal("0.00"),
        description="SUM(balance) for overdue transactions only.",
    )

    model_config = {"from_attributes": True}


class VendorSummary(BaseModel):
    """
    Lightweight vendor row for the list view data table.

    Excludes verbose contact fields to keep list payloads small.
    The `payables` aggregate is included because it is a visible column.
    """

    id: UUID
    vendor_name: str
    email: str | None = None
    phone_primary: str | None = None
    status: str
    currency: str

    # Computed aggregate — injected by service
    payables: Decimal = Field(default=Decimal("0.00"))

    created_at: datetime

    @computed_field
    @property
    def is_active(self) -> bool:
        """Convenience predicate used by the frontend Actions dropdown."""
        return self.status == VendorStatus.ACTIVE

    model_config = {"from_attributes": True}


class VendorStatusCounts(BaseModel):
    """Counts backing the All/Active/Inactive tabs."""

    all: int = 0
    active: int = 0
    inactive: int = 0


# Payable statuses (expenses/bills) plus the purchase-order lifecycle
# statuses. POs are non-payable commitments, so their statuses live in the
# same union only so the shared transaction-list row can represent them —
# the days_overdue/status_display computed fields stay payable-only.
TransactionStatus = Literal[
    "paid",
    "pending",
    "overdue",
    "draft",
    "sent",
    "billed",
    "canceled",
]
TransactionType = Literal["expense", "bill", "purchase_order"]

# Payable statuses that drive the overdue countdown; PO statuses are excluded.
_PAYABLE_OVERDUE_STATUS = "overdue"


class VendorTransactionSummary(BaseModel):
    """
    A single row in the vendor detail transaction list.

    NOTE: FastAPI serializes response models by alias, and the frontend reads
    snake_case keys (transaction_type, ref_no, due_date) - so those fields
    must NOT carry camelCase aliases (#33). transaction_date keeps the "date"
    alias because the frontend reads `item.date`.
    """

    id: UUID = Field(description="Expense or Bill UUID")
    transaction_type: TransactionType = Field(description="Source record type")
    ref_no: str = Field(description="Reference number, e.g. INV-202407")
    transaction_date: date = Field(description="Transaction date", alias="date")
    amount: Decimal = Field(description="Total transaction amount")
    balance: Decimal = Field(description="Outstanding balance on the transaction")
    status: TransactionStatus = Field(description="Payment status")
    due_date: date | None = Field(None, description="Due date")

    @computed_field
    @property
    def days_overdue(self) -> int:
        """
        Days past due for overdue transactions.
        """
        if self.status != "overdue" or self.due_date is None:
            return 0
        delta = (reporting_date() - self.due_date).days  # `date` is the type again
        return max(delta, 0)

    @computed_field
    @property
    def status_display(self) -> str:
        """
        Status label: 'Paid' | 'Pending' | 'Overdue (N days)'
        """
        if self.status == "overdue" and self.days_overdue > 0:
            return f"Overdue ({self.days_overdue} days)"
        return self.status.capitalize()

    model_config = {"populate_by_name": True, "from_attributes": True}


# Vendor Detail Card Schemas (Total POs / Total Payments / Total Bills)

# Each detail card renders a total plus a list of the underlying transactions,
# every row tagged paid/pending. POs / bills carry both states; a Payment is a
# settled event, so it is always "paid".
CardPaymentState = Literal["paid", "pending"]

# Source tag on a card row — lets the frontend route a row to its document.
CardItemSource = Literal[
    "purchase_order",
    "bill",
    "po_payment",
    "expense_payment",
]


class VendorCardItem(BaseModel):
    """A single transaction row inside a vendor detail card."""

    id: UUID = Field(description="Source document (or payment) UUID")
    source: CardItemSource = Field(description="Row source type")
    ref_no: str = Field(description="Reference number, e.g. PO-0042 / EXP-0042")
    transaction_date: date = Field(description="Row date", alias="date")
    amount: Decimal = Field(description="Row amount (document total or payment)")
    payment_state: CardPaymentState = Field(
        description="'paid' | 'pending' — POs/bills derive it; payments are 'paid'"
    )

    # Payment-only columns (null on PO/bill rows). invoice_number exists only on
    # PO payments; payment_ref is the payment's own reference; parent_id is the
    # source document so the row can link back to it.
    invoice_number: str | None = Field(
        None, description="Invoice/bill number a payment settles (PO payments only)"
    )
    payment_ref: str | None = Field(
        None, description="Payment reference (cheque / transaction id)"
    )
    parent_id: UUID | None = Field(
        None, description="Source document id (PO/Expense) for a payment row"
    )

    model_config = {"populate_by_name": True, "from_attributes": True}


class VendorCardSummary(BaseModel):
    """A vendor detail card: aggregate totals + a paginated, tagged row list.

    Backs the three cards on the vendor detail Overview tab (Total POs,
    Total Payments, Total Bills/Invoices). Aggregates are computed fresh in
    SQL over the whole date-filtered set (never cached, never row-by-row in
    Python); `items` is only the current page.
    """

    total: Decimal = Field(
        default=Decimal("0.00"), description="Sum of document totals in range"
    )
    paid_total: Decimal = Field(
        default=Decimal("0.00"), description="Sum of the paid portion in range"
    )
    pending_total: Decimal = Field(
        default=Decimal("0.00"), description="Sum of the outstanding portion in range"
    )
    count: int = Field(default=0, description="Number of rows in the whole set")
    currency: str = Field(default="KES", description="Display currency (from vendor)")

    period_start: date | None = Field(None, description="Applied filter start")
    period_end: date | None = Field(None, description="Applied filter end")

    # Pagination of `items`
    page: int = Field(default=1)
    per_page: int = Field(default=10)
    total_pages: int = Field(default=1)

    items: list[VendorCardItem] = Field(default_factory=list)


class VendorPayablesSummary(BaseModel):
    """
    Payables summary .
    """

    total_unpaid: Decimal = Field(
        default=Decimal("0.00"), description="Sum of all pending + overdue balances"
    )
    overdue_total: Decimal = Field(
        default=Decimal("0.00"), description="Sum of overdue balances only"
    )
    currency: str = Field(
        default="KES", description="Currency for display (inherited from vendor record)"
    )


# Statement Schemas

VendorStatementSourceType = Literal[
    "opening_balance",
    "expense",
    "purchase_order",
    "expense_payment",
    "po_payment",
]


class VendorStatementTransaction(BaseModel):
    """Single transaction line in a vendor statement."""

    transaction_date: date = Field(description="Date of the transaction", alias="date")
    description: str = Field(description="Human-readable description of the line")
    source_type: VendorStatementSourceType = Field(description="Type of source")
    source_id: UUID | None = Field(
        default=None, description="UUID of the source record for this ledger row"
    )
    reference: str | None = Field(
        default=None, description="Reference number of the source document"
    )
    detail: str | None = Field(
        default=None, description="Detailed description of the transaction"
    )
    amount: Decimal = Field(
        description="Amount charged (expense/bill). 0.00 for payments."
    )
    payment: Decimal = Field(description="Amount paid. 0.00 for expense/bill lines.")
    balance: Decimal = Field(description="Running balance after this line")

    model_config = {"populate_by_name": True, "from_attributes": True}


class VendorStatementSummary(BaseModel):
    """Account summary for the vendor statement header."""

    opening_balance: Decimal = Field(
        default=Decimal("0.00"), description="Balance carried forward before the period"
    )
    invoiced_amount: Decimal = Field(
        default=Decimal("0.00"), description="Total expenses/bills raised in the period"
    )
    amount_paid: Decimal = Field(
        default=Decimal("0.00"), description="Total payments made in the period"
    )
    balance_due: Decimal = Field(
        default=Decimal("0.00"), description="Closing balance owed at period end"
    )


class VendorStatement(BaseModel):
    """Complete vendor statement of account for a given period."""

    vendor: "VendorResponse"
    period_start: date
    period_end: date
    summary: VendorStatementSummary
    transactions: list[VendorStatementTransaction]

    model_config = {"from_attributes": True}


class VendorFilterParams(BaseModel):
    """Query parameters for the vendor list endpoint."""

    status: VendorStatus | None = Field(None, description="Filter by status.")
    search: str | None = Field(
        None,
        max_length=100,
        description="Search across vendor_name, email, and phone_primary",
    )

    @field_validator("status", mode="before")
    @classmethod
    def normalise_status(cls, v: str | None) -> str | None:
        """Accept 'all' as equivalent to None (no filter)."""
        if v is None or v.strip().lower() == "all":
            return None
        return v.strip().lower()

    model_config = {"populate_by_name": True}


class VendorTransactionFilterParams(BaseModel):
    """
    Filter params for the transaction list on the vendor detail view.
    """

    status: TransactionStatus | None = Field(
        None, description="Filter transactions by status. Omit for all."
    )
    transaction_type: TransactionType | None = Field(
        None, description="Filter transactions by type. Omit for all."
    )

    model_config = {"populate_by_name": True}


# Contact Search Schemas


class ContactSearchResult(BaseModel):
    """A single result in the 'Search Existing Vendor' dropdown"""

    id: UUID
    full_name: str
    email: str | None = None
    phone_primary: str | None = None
    address: str | None = None
    country: str | None = None
    website: str | None = None
    vat_number: str | None = None


class ContactSearchResponse(BaseModel):
    """Wrapper for the contact search results list."""

    results: list[ContactSearchResult]
    total: int


# Action / Operation Response Schemas


class VendorActivateResponse(BaseModel):
    """Response after activating a vendor."""

    vendor_id: UUID
    status: str
    message: str = "Vendor activated successfully"


class VendorDeactivateResponse(BaseModel):
    """Response after deactivating a vendor."""

    vendor_id: UUID
    status: str
    message: str = "Vendor deactivated successfully"


class VendorDeleteResponse(BaseModel):
    """Response after successfully deleting a vendor."""

    vendor_id: UUID
    message: str = "Vendor deleted successfully"


class VendorOperationResponse(BaseModel):
    """
    Generic operation envelope — used for error cases and
    any action that does not return the full vendor object.
    """

    success: bool
    message: str
    vendor_id: UUID | None = None
    errors: list[dict] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


# Duplicate Detection Schema


class VendorDuplicateCheckResponse(BaseModel):
    """
    Response for the duplicate email check
    Returned when an email match is found before saving.
    """

    exists: bool
    vendor: VendorSummary | None = None
    message: str | None = None
