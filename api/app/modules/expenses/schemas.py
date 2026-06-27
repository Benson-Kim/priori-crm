"""
Pydantic schemas for the Expenses API.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import (
    BaseModel,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.common.financial import calculate_days_overdue, check_is_overdue
from app.common.validators import empty_str_to_none as normalize_empty_str
from app.constants.enums import Currency, ExpenseStatus, TaxType

# VENDOR SUMMARY


class VendorSummary(BaseModel):
    """
    Vendor fields surfaced on expense responses
    """

    id: UUID
    vendor_name: str
    email: str | None = None
    phone_primary: str | None = None
    phone_secondary: str | None = None

    model_config = {"from_attributes": True}


# LINE ITEM SCHEMAS


class ExpenseLineItemCreate(BaseModel):
    """Payload for a single line item — used on create and full line-item replace."""

    item_name: str = Field(
        ...,
        min_length=1,
        max_length=500,
        alias="itemName",
        description="Product or service name",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Detailed description of the line item",
    )
    quantity: Decimal = Field(
        ...,
        gt=0,
        decimal_places=2,
        description="Quantity — must be greater than zero",
    )
    unit_price: Decimal = Field(
        ...,
        ge=0,
        decimal_places=2,
        alias="unitPrice",
        description="Price per unit — zero allowed for zero-rated items",
    )
    tax_type: TaxType = Field(
        default=TaxType.VAT_16,
        alias="taxType",
        description="Tax type applied to this line item",
    )

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "itemName": "Office Supplies",
                "description": "Printer cartridges and A4 paper reams",
                "quantity": 5,
                "unitPrice": 2500.00,
                "taxType": "vat_16",
            }
        },
    }


class ExpenseLineItemResponse(BaseModel):
    """Line item in API responses — mirrors InvoiceLineItemResponse."""

    id: UUID
    line_number: int
    item_name: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    tax_type: str
    tax_amount: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# DOCUMENT SCHEMAS


class ExpenseDocumentResponse(BaseModel):
    """
    Document attachment in API responses.

    storage_key is intentionally excluded — it is an internal detail
    that must never be sent to clients.
    """

    id: UUID
    expense_id: UUID
    filename: str
    file_size_bytes: int
    file_size_kb: float  # @property on model — readable via from_attributes
    mime_type: str
    source: str
    uploaded_at: datetime
    uploaded_by: UUID | None = None

    model_config = {"from_attributes": True}


# PAYMENT SCHEMAS


class ExpensePaymentCreate(BaseModel):
    """
    Payload for recording a payment
    """

    amount: Decimal = Field(
        ...,
        gt=0,
        decimal_places=2,
        description=(
            "Payment amount — must be > 0. May exceed the current balance_due "
            "(overpayment is recorded and drives balance_due negative)."
        ),
    )
    payment_date: date = Field(
        ...,
        alias="paymentDate",
        description="Date the payment was made — required",
    )
    reference: str | None = Field(
        None,
        max_length=200,
        description="Transaction ID, cheque number, or remittance reference",
    )
    notes: str | None = Field(
        None,
        max_length=2000,
        description="Internal notes for this payment entry",
    )

    @field_validator("reference", "notes", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        return normalize_empty_str(v)

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "amount": 5000.00,
                "paymentDate": "2026-05-20",
                "reference": "TXN-9981234",
                "notes": "Paid via M-Pesa",
            }
        },
    }


class ExpensePaymentResponse(BaseModel):
    """Payment record in API responses."""

    id: UUID
    expense_id: UUID
    amount: Decimal
    payment_date: date
    reference: str | None = None
    notes: str | None = None
    document_id: UUID | None = None
    created_at: datetime
    recorded_by: UUID | None = None

    model_config = {"from_attributes": True}


# EXPENSE REQUEST SCHEMAS


class ExpenseCreate(BaseModel):
    """Payload for creating a new expense record ."""

    vendor_id: UUID = Field(
        ...,
        alias="vendorId",
        description="Vendor this expense is payable to — required ",
    )
    expense_date: date = Field(
        default_factory=date.today,
        alias="expenseDate",
        description="Date the expense was incurred",
    )
    due_date: date = Field(
        ...,
        alias="dueDate",
        description="Payment due date — must be on or after expense_date",
    )
    currency: Currency = Field(
        default=Currency.KES,
        description="ISO 4217 currency code — locked after first save",
    )
    is_recurring: bool = Field(
        default=False,
        alias="isRecurring",
        description="Recurring-bill flag — informational only in v1",
    )
    line_items: list[ExpenseLineItemCreate] = Field(
        ...,
        min_length=1,
        alias="lineItems",
        description="At least one line item required",
    )
    notes: str | None = Field(
        None,
        max_length=5000,
        description="Vendor-facing or internal notes",
    )

    @field_validator("notes", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        return normalize_empty_str(v)

    @model_validator(mode="after")
    def validate_due_date(self) -> "ExpenseCreate":
        """due_date must be on or after expense_date."""
        if self.due_date < self.expense_date:
            raise ValueError("due_date must be on or after expense_date")
        return self

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "vendorId": "123e4567-e89b-12d3-a456-426614174000",
                "expenseDate": "2026-05-19",
                "dueDate": "2026-06-19",
                "currency": "KES",
                "isRecurring": False,
                "lineItems": [
                    {
                        "itemName": "Office Rent — May 2026",
                        "description": "Monthly office space Upper Hill",
                        "quantity": 1,
                        "unitPrice": 85000.00,
                        "taxType": "vat_16",
                    }
                ],
                "notes": "Please remit to Equity Bank Ac 1234567890.",
            }
        },
    }


class ExpenseUpdate(BaseModel):
    """
    Payload for updating an existing expense.

    PATCH semantics via model_dump(exclude_unset=True) in the service —
    identical to QuoteUpdate / InvoiceUpdate pattern.

    currency is deliberately excluded — it is locked after first save.
    The service update() enforces this by only applying fields present in update_data;
    since currency never appears in ExpenseUpdate it can never be changed.

    Editing gate enforced in service:
        PENDING / OVERDUE → editable
        PAID              → BadRequestException
    """

    vendor_id: UUID | None = Field(None, alias="vendorId")
    expense_date: date | None = Field(None, alias="expenseDate")
    due_date: date | None = Field(None, alias="dueDate")
    is_recurring: bool | None = Field(None, alias="isRecurring")
    line_items: list[ExpenseLineItemCreate] | None = Field(
        None,
        min_length=1,
        alias="lineItems",
    )
    notes: str | None = Field(None, max_length=5000)

    @field_validator("notes", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        return normalize_empty_str(v)

    @model_validator(mode="after")
    def validate_due_date(self) -> "ExpenseUpdate":
        """
        Validate date ordering only when both dates are explicitly supplied.
        Mirrors QuoteUpdate.validate_due_date behaviour.
        """
        if (
            self.due_date is not None
            and self.expense_date is not None
            and self.due_date < self.expense_date
        ):
            raise ValueError("due_date must be on or after expense_date")
        return self

    model_config = {"populate_by_name": True}


class MarkAsPaidRequest(BaseModel):
    """
    Optional body for the Mark-as-Paid action.

    paid_at defaults to now() in the service when not provided.
    This action does NOT create an ExpensePayment record.
    """

    paid_at: datetime | None = Field(
        None,
        alias="paidAt",
        description="Override the paid timestamp (defaults to server now)",
    )

    model_config = {"populate_by_name": True}


# RESPONSE SCHEMAS


class ExpenseResponse(BaseModel):
    """
    Full expense detail — returned by create, update, get-by-id, and actions.
    Mirrors InvoiceResponse structure adapted to the Expense domain.
    """

    id: UUID
    expense_number: str
    expense_reference: str
    vendor_id: UUID

    expense_date: date
    due_date: date
    status: str
    currency: str
    is_recurring: bool

    subtotal: Decimal
    tax_total: Decimal
    total_due: Decimal
    amount_paid: Decimal
    balance_due: Decimal

    notes: str | None = None

    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None = None
    created_by: UUID | None = None
    version: int

    # Relationships
    vendor: VendorSummary
    line_items: list[ExpenseLineItemResponse] = Field(default_factory=list)
    payments: list[ExpensePaymentResponse] = Field(default_factory=list)
    documents: list[ExpenseDocumentResponse] = Field(default_factory=list)

    @computed_field
    @property
    def is_editable(self) -> bool:
        """PENDING and OVERDUE are editable; PAID is terminal."""
        return self.status in (ExpenseStatus.PENDING, ExpenseStatus.OVERDUE)

    @computed_field
    @property
    def is_overdue(self) -> bool:
        return check_is_overdue(self.status, self.due_date)

    @computed_field
    @property
    def days_overdue(self) -> int:
        return calculate_days_overdue(self.status, self.due_date)

    model_config = {"from_attributes": True}


class ExpenseSummary(BaseModel):
    """
    Lightweight row for the paginated list view.
    Built from a projection query — no ORM model instance.
    """

    id: UUID
    expense_number: str
    expense_reference: str
    vendor_id: UUID
    vendor_name: str  # joined from vendors table in list query

    expense_date: date
    due_date: date
    status: str
    currency: str
    total_due: Decimal
    balance_due: Decimal
    is_recurring: bool
    created_at: datetime

    @computed_field
    @property
    def is_overdue(self) -> bool:
        return check_is_overdue(self.status, self.due_date)

    @computed_field
    @property
    def days_overdue(self) -> int:
        return calculate_days_overdue(self.status, self.due_date)

    model_config = {"from_attributes": True}


class ExpenseStatusCounts(BaseModel):
    """
    Status counts for the filter-tab bar.
    Mirrors QuoteStatusCounts / InvoiceStatusCounts pattern.
    """

    all: int = 0
    pending: int = 0
    paid: int = 0
    overdue: int = 0  # computed-overdue: stored OVERDUE + PENDING-past-due
    canceled: int = 0  # voided/soft-deleted expenses (excluded from `all`)


class ExpenseStatisticsResponse(BaseModel):
    """Aggregated statistics for dashboard display."""

    total_expenses: int
    total_amount: Decimal  # matches get_statistics() dict key exactly
    total_paid: Decimal
    total_outstanding: Decimal
    overdue_count: int
    overdue_amount: Decimal
    average_expense_value: Decimal
    average_days_to_payment: float
    date_from: date | None = None
    date_to: date | None = None

    model_config = {"from_attributes": True}


# FILTER SCHEMA


class ExpenseFilterParams(BaseModel):
    """
    Query parameters for filtering the expenses list.
    Mirrors InvoiceFilterParams / QuoteFilterParams pattern.
    """

    status: ExpenseStatus | None = Field(None, description="Filter by status")
    vendor_id: UUID | None = Field(
        None,
        alias="vendorId",
        description="Filter by vendor",
    )
    date_from: date | None = Field(
        None,
        alias="dateFrom",
        description="expense_date >= this value",
    )
    date_to: date | None = Field(
        None,
        alias="dateTo",
        description="expense_date <= this value",
    )
    due_date_from: date | None = Field(
        None,
        alias="dueDateFrom",
        description="due_date >= this value",
    )
    due_date_to: date | None = Field(
        None,
        alias="dueDateTo",
        description="due_date <= this value",
    )
    is_recurring: bool | None = Field(
        None,
        alias="isRecurring",
        description="Filter by recurring flag",
    )
    search: str | None = Field(
        None,
        max_length=100,
        description="Search expense_number, expense_reference, or vendor name",
    )

    model_config = {"populate_by_name": True}


# CALCULATION PREVIEW


class ExpenseCalculationResponse(BaseModel):
    """
    Totals preview — POST /expenses/calculate.

    No discount in v1: total_due = subtotal + tax_total.
    Mirrors QuoteCalculationResponse / InvoiceCalculationResponse.
    """

    subtotal: Decimal
    tax_total: Decimal
    total_due: Decimal
    line_items: list[dict] = Field(
        default_factory=list,
        description="Line items with calculated totals",
    )
