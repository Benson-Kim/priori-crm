"""Pydantic schemas for quote API requests and responses."""

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

from app.common.financial import check_is_overdue
from app.common.reporting_time import reporting_date
from app.constants.enums import (
    Currency,
    DiscountType,
    QuoteStatus,
    TaxType,
)
from app.modules.customers.schemas import CustomerSummary

# LINE ITEM SCHEMAS


class QuoteLineItemCreate(BaseModel):
    """Schema for creating a quote line item."""

    item_name: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Product/service name",
        alias="itemName",
    )

    description: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Detailed line item description",
        alias="description",
    )

    quantity: Decimal = Field(
        ..., gt=0, decimal_places=2, description="Quantity of items", alias="quantity"
    )

    unit_price: Decimal = Field(
        ...,
        gt=0,
        decimal_places=2,
        description="Price per unit (must be greater than 0)",
        alias="unitPrice",
    )

    tax_type: TaxType = Field(
        default=TaxType.VAT_16,
        description="Tax type for this line item",
        alias="taxType",
    )

    # Optional: product_id when Products module exists
    # product_id: UUID | None = Field(None, alias="productId")

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "itemName": "Microsoft 365 Business Premium",
                "description": "Monthly subscription for 10 users",
                "quantity": 10,
                "unitPrice": 1500.00,
                "taxType": "vat_16",
            }
        },
    }


class QuoteLineItemUpdate(BaseModel):
    """Schema for updating a quote line item."""

    item_name: str | None = Field(None, min_length=1, max_length=500, alias="itemName")
    description: str | None = Field(None, min_length=1, max_length=2000)
    quantity: Decimal | None = Field(None, gt=0, decimal_places=2)
    unit_price: Decimal | None = Field(None, ge=0, decimal_places=2, alias="unitPrice")
    tax_type: TaxType | None = Field(None, alias="taxType")

    model_config = {"populate_by_name": True}


class QuoteLineItemResponse(BaseModel):
    """Schema for quote line item in responses."""

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


# QUOTE REQUEST SCHEMAS


class QuoteCreate(BaseModel):
    """Schema for creating a new quote."""

    # Required Fields
    customer_id: UUID = Field(
        ..., description="Customer receiving this quote", alias="customerId"
    )

    transaction_date: date = Field(
        default_factory=reporting_date,
        description="Quote issue date",
        alias="transactionDate",
    )

    due_date: date = Field(..., description="Quote expiry date", alias="dueDate")

    currency: Currency = Field(
        default=Currency.KES, description="Quote currency", alias="currency"
    )

    # Line Items (at least one required)
    line_items: list[QuoteLineItemCreate] = Field(
        ...,
        min_length=1,
        description="Quote line items (at least one required)",
        alias="lineItems",
    )

    # Optional Fields
    rfq_rfp_number: str | None = Field(
        None,
        max_length=100,
        description="RFQ/RFP reference number",
        alias="rfqRfpNumber",
    )

    notes: str | None = Field(
        None, max_length=5000, description="Customer-facing notes/terms", alias="notes"
    )

    # Discount (optional)
    discount_type: DiscountType | None = Field(
        None,
        description="Type of discount: 'amount' or 'percentage'",
        alias="discountType",
    )

    discount_amount: Decimal | None = Field(
        None,
        ge=0,
        decimal_places=2,
        description="Fixed discount amount",
        alias="discountAmount",
    )

    discount_percentage: Decimal | None = Field(
        None,
        ge=0,
        le=100,
        decimal_places=2,
        description="Discount percentage (0-100)",
        alias="discountPercentage",
    )

    # Document-level VAT (optional). When BOTH fields are omitted the
    # service defaults them from the owner profile's VAT settings, so new
    # quotes inherit our own (owner) VAT configuration.
    vat_enabled: bool | None = Field(
        None,
        alias="vatEnabled",
        description=(
            "Enable document-level VAT computed once on the discounted "
            "subtotal. Omit (together with vatRate) to inherit the owner "
            "profile's VAT settings."
        ),
    )

    vat_rate: Decimal | None = Field(
        None,
        ge=0,
        le=1,
        alias="vatRate",
        description=(
            "VAT rate as a fraction (e.g. 0.16 for 16%). Required when "
            "vatEnabled is true."
        ),
    )

    @field_validator("rfq_rfp_number", "notes", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        """Convert empty strings to None."""
        if v == "" or (isinstance(v, str) and v.strip() == ""):
            return None
        return v

    @model_validator(mode="after")
    def validate_due_date(self) -> "QuoteCreate":
        """Ensure due date is not before transaction date."""
        if self.due_date < self.transaction_date:
            raise ValueError("due_date must be on or after transaction_date")
        return self

    @model_validator(mode="after")
    def validate_discount(self) -> "QuoteCreate":
        """Ensure discount type matches provided values."""
        sent = self.model_fields_set
        has_amount = self.discount_amount is not None and self.discount_amount > 0
        has_percentage = (
            self.discount_percentage is not None and self.discount_percentage > 0
        )

        if has_amount and has_percentage:
            raise ValueError(
                "Cannot specify both discount_amount and discount_percentage. "
                "Choose one discount method."
            )

        if has_amount:
            self.discount_type = DiscountType.AMOUNT
            if "discount_percentage" in sent:
                self.discount_percentage = None
            return self

        if has_percentage:
            self.discount_type = DiscountType.PERCENTAGE
            if "discount_amount" in sent:
                self.discount_amount = None
            return self

        discount_fields = {"discount_type", "discount_amount", "discount_percentage"}
        client_sent_discount_fields = discount_fields & sent

        if client_sent_discount_fields:
            if "discount_type" in sent and self.discount_type is None:
                self.discount_amount = None
                self.discount_percentage = None
            elif not ({"discount_amount", "discount_percentage"} & sent):
                pass
            else:
                self.discount_type = None
                self.discount_amount = None
                self.discount_percentage = None

        return self

    @model_validator(mode="after")
    def validate_vat(self) -> "QuoteCreate":
        """A VAT rate is required when VAT is explicitly enabled."""
        if self.vat_enabled and self.vat_rate is None:
            raise ValueError("vat_rate is required when vat_enabled is true")
        return self

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "customerId": "123e4567-e89b-12d3-a456-426614174000",
                "transactionDate": "2026-03-30",
                "dueDate": "2026-04-29",
                "currency": "KES",
                "lineItems": [
                    {
                        "itemName": "Website Development",
                        "description": "Custom corporate website with CMS",
                        "quantity": 1,
                        "unitPrice": 250000.00,
                        "taxType": "vat_16",
                    }
                ],
                "rfqRfpNumber": "RFP-2026-001",
                "notes": "Valid for 30 days. Payment terms: 50% upfront, 50% on completion.",
                "discountType": "percentage",
                "discountPercentage": 10.0,
            }
        },
    }


class QuoteUpdate(BaseModel):
    """Schema for updating an existing quote (only editable fields)."""

    # Customer can only be changed in DRAFT status
    customer_id: UUID | None = Field(None, alias="customerId")

    transaction_date: date | None = Field(None, alias="transactionDate")
    due_date: date | None = Field(None, alias="dueDate")
    currency: Currency | None = None

    line_items: list[QuoteLineItemCreate] | None = Field(
        None, min_length=1, alias="lineItems"
    )

    rfq_rfp_number: str | None = Field(None, max_length=100, alias="rfqRfpNumber")
    notes: str | None = Field(None, max_length=5000)

    discount_type: DiscountType | None = Field(None, alias="discountType")
    discount_amount: Decimal | None = Field(None, ge=0, alias="discountAmount")
    discount_percentage: Decimal | None = Field(
        None, ge=0, le=100, alias="discountPercentage"
    )

    # Document-level VAT (editable while the quote is editable). The
    # service validates that an effective rate exists whenever the toggle
    # ends up enabled.
    vat_enabled: bool | None = Field(None, alias="vatEnabled")
    vat_rate: Decimal | None = Field(None, ge=0, le=1, alias="vatRate")

    @field_validator("rfq_rfp_number", "notes", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        """Convert empty strings to None."""
        if v == "" or (isinstance(v, str) and v.strip() == ""):
            return None
        return v

    @model_validator(mode="after")
    def validate_due_date(self) -> "QuoteUpdate":
        """Ensure due date is not before transaction date if both provided."""
        if (
            self.due_date is not None
            and self.transaction_date is not None
            and self.due_date < self.transaction_date
        ):
            raise ValueError("due_date must be on or after transaction_date")
        return self

    @model_validator(mode="after")
    def validate_discount(self) -> "QuoteUpdate":
        """Ensure discount type is consistent with the provided values."""
        has_amount = self.discount_amount is not None and self.discount_amount > 0
        has_percentage = (
            self.discount_percentage is not None and self.discount_percentage > 0
        )

        if has_amount and has_percentage:
            raise ValueError(
                "Cannot specify both discount_amount and discount_percentage. "
                "Choose one discount method."
            )

        if has_amount and self.discount_type != DiscountType.AMOUNT:
            self.discount_type = DiscountType.AMOUNT

        if has_percentage and self.discount_type != DiscountType.PERCENTAGE:
            self.discount_type = DiscountType.PERCENTAGE

        if not has_amount and not has_percentage:
            self.discount_type = None
            self.discount_amount = None
            self.discount_percentage = None

        return self

    model_config = {"populate_by_name": True}


# QUOTE RESPONSE SCHEMAS


class QuoteResponse(BaseModel):
    """Complete quote data for API responses."""

    id: UUID
    quote_number: str
    quote_reference: str
    customer_id: UUID

    transaction_date: date
    due_date: date

    status: str
    currency: str

    subtotal: Decimal
    discount_type: str | None = None
    discount_amount: Decimal | None = None
    discount_percentage: Decimal | None = None
    vat_enabled: bool = False
    vat_rate: Decimal | None = None
    tax_total: Decimal
    total_due: Decimal

    rfq_rfp_number: str | None = None
    notes: str | None = None

    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None = None
    approved_at: datetime | None = None
    invoiced_at: datetime | None = None
    expired_at: datetime | None = None

    created_by: UUID | None = None
    approved_by: UUID | None = None
    version: int

    # Related invoice (if converted)
    related_invoice_id: UUID | None = None

    # Relationships
    customer: CustomerSummary
    line_items: list[QuoteLineItemResponse] = Field(default_factory=list)

    # Computed fields
    @computed_field
    @property
    def is_editable(self) -> bool:
        """Check if quote can be edited."""
        return self.status in [QuoteStatus.DRAFT, QuoteStatus.SENT]

    @computed_field
    @property
    def is_expired(self) -> bool:
        """Check if quote is expired."""
        return check_is_overdue(
            self.status,
            self.due_date,
            terminal_statuses={QuoteStatus.APPROVED, QuoteStatus.INVOICED},
        )

    @computed_field
    @property
    def days_until_expiry(self) -> int:
        """Calculate days until expiry (negative if expired)."""
        return (self.due_date - reporting_date()).days

    @computed_field
    @property
    def can_convert_to_invoice(self) -> bool:
        """Check if quote can be converted to invoice.

        Requires: status in APPROVED/SENT, not expired, and not already converted.
        """
        return (
            self.status in [QuoteStatus.APPROVED, QuoteStatus.SENT]
            and not self.is_expired
            and self.related_invoice_id is None  # already-converted guard
        )

    model_config = {"from_attributes": True}


class QuoteSummary(BaseModel):
    """Lightweight quote summary for list views."""

    id: UUID
    quote_number: str
    quote_reference: str
    customer_id: UUID
    customer_name: str  # Joined from customer table

    transaction_date: date
    due_date: date

    status: str
    currency: str
    total_due: Decimal

    created_at: datetime

    @computed_field
    @property
    def is_expired(self) -> bool:
        """Check if quote is expired."""
        return check_is_overdue(
            self.status,
            self.due_date,
            terminal_statuses={QuoteStatus.APPROVED, QuoteStatus.INVOICED},
        )

    @computed_field
    @property
    def days_until_expiry(self) -> int:
        """Calculate days until expiry (negative if expired)."""
        return (self.due_date - reporting_date()).days

    model_config = {"from_attributes": True}


class QuoteStatusCounts(BaseModel):
    """Quote counts by status for dashboard."""

    all: int = 0
    draft: int = 0
    sent: int = 0
    approved: int = 0
    invoiced: int = 0
    expired: int = 0


class QuoteStatisticsResponse(BaseModel):
    """Response schema for quote statistics."""

    total_quotes: int = Field(..., description="Total number of quotes in period")
    total_quoted: Decimal = Field(..., description="Total quoted amount")
    total_approved: Decimal = Field(..., description="Total approved quote value")
    total_invoiced: Decimal = Field(
        ..., description="Total value converted to invoices"
    )
    conversion_rate: float = Field(
        ..., description="Approval to invoice conversion rate (%)"
    )
    average_quote_value: Decimal = Field(..., description="Average quote amount")
    average_days_to_approval: float = Field(
        ..., description="Average days from quote to approval"
    )
    expired_count: int = Field(..., description="Number of expired quotes")
    expired_amount: Decimal = Field(..., description="Total value of expired quotes")
    date_from: date | None = Field(None, description="Start date of period")
    date_to: date | None = Field(None, description="End date of period")

    model_config = {"from_attributes": True}


# ACTION SCHEMAS


class QuoteSendRequest(BaseModel):
    """Schema for sending a quote via email (future feature)."""

    to_email: str | None = Field(
        None,
        description="Override recipient email (defaults to customer email)",
        alias="toEmail",
    )

    subject: str | None = Field(
        None,
        max_length=200,
        description="Email subject (auto-generated if not provided)",
        alias="subject",
    )

    body: str | None = Field(
        None,
        max_length=5000,
        description="Email body (uses template if not provided)",
        alias="body",
    )

    attach_pdf: bool = Field(
        default=True, description="Whether to attach quote PDF", alias="attachPdf"
    )

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "toEmail": "customer@example.com",
                "subject": "Quote QT-0101 from Priori Technologies",
                "body": "Please find attached your quote.",
                "attachPdf": True,
            }
        },
    }


class QuoteSendResponse(BaseModel):
    """Response after sending a quote."""

    quote_id: UUID
    sent_to: str
    sent_at: datetime
    message: str = "Quote sent successfully"


class QuoteMarkSentRequest(BaseModel):
    """Schema for marking quote as sent without emailing."""

    sent_at: datetime | None = Field(
        None,
        description="Timestamp when quote was sent (defaults to now)",
        alias="sentAt",
    )

    model_config = {"populate_by_name": True}


class QuoteApproveRequest(BaseModel):
    """Schema for approving a quote."""

    approved_at: datetime | None = Field(
        None,
        description="Timestamp when quote was approved (defaults to now)",
        alias="approvedAt",
    )

    notes: str | None = Field(None, max_length=1000, description="Approval notes")

    model_config = {"populate_by_name": True}


class QuoteConvertToInvoiceResponse(BaseModel):
    """Response after converting a quote to an invoice."""

    quote_id: UUID
    invoice_id: UUID
    invoice_number: str
    message: str = "Quote converted to invoice successfully"


class QuoteCalculationResponse(BaseModel):
    """Response for quote total calculations (preview)."""

    subtotal: Decimal
    discount_value: Decimal
    tax_total: Decimal
    total_due: Decimal

    line_items: list[dict] = Field(
        default_factory=list, description="Line items with calculated totals"
    )


# FILTER & SEARCH SCHEMAS


class QuoteFilterParams(BaseModel):
    """Query parameters for filtering quotes."""

    status: QuoteStatus | None = Field(None, description="Filter by status")

    customer_id: UUID | None = Field(
        None, description="Filter by customer", alias="customerId"
    )

    date_from: date | None = Field(
        None,
        description="Filter quotes from this date (transaction_date)",
        alias="dateFrom",
    )

    date_to: date | None = Field(
        None,
        description="Filter quotes up to this date (transaction_date)",
        alias="dateTo",
    )

    due_date_from: date | None = Field(
        None, description="Filter by due date range start", alias="dueDateFrom"
    )

    due_date_to: date | None = Field(
        None, description="Filter by due date range end", alias="dueDateTo"
    )

    search: str | None = Field(
        None,
        max_length=100,
        description="Search in quote number, customer name, or reference",
    )

    model_config = {"populate_by_name": True}
