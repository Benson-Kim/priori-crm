"""Pydantic schemas for invoice API requests and responses."""

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

from app.constants.enums import (
    Currency,
    DiscountType,
    InvoiceStatus,
    PaymentMethod,
    TaxType,
)
from app.modules.customers.schemas import CustomerSummary

# LINE ITEM SCHEMAS


class InvoiceLineItemCreate(BaseModel):
    """Schema for creating an invoice line item."""

    item_name: str = Field(
        ..., max_length=500, description="Product/service name", alias="itemName"
    )

    description: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Line item description",
        alias="description",
    )

    quantity: Decimal = Field(
        ..., gt=0, decimal_places=2, description="Quantity of items", alias="quantity"
    )

    unit_price: Decimal = Field(
        ..., ge=0, decimal_places=2, description="Price per unit", alias="unitPrice"
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
                "itemName": "Microsoft 365 Suite",
                "description": "Monthly subscription",
                "quantity": 10,
                "unitPrice": 120.55,
                "taxType": "vat_16",
            }
        },
    }


class InvoiceLineItemUpdate(BaseModel):
    """Schema for updating an invoice line item."""

    description: str | None = Field(None, min_length=1, max_length=1000)
    quantity: Decimal | None = Field(None, gt=0, decimal_places=2)
    unit_price: Decimal | None = Field(None, ge=0, decimal_places=2, alias="unitPrice")
    tax_type: TaxType | None = Field(None, alias="taxType")

    model_config = {"populate_by_name": True}


class InvoiceLineItemResponse(BaseModel):
    """Schema for invoice line item in responses."""

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


# PAYMENT SCHEMAS


class PaymentCreate(BaseModel):
    """Schema for recording a payment against an invoice."""

    amount: Decimal = Field(
        ...,
        gt=0,
        decimal_places=2,
        description="Payment amount (must be positive)",
        alias="amount",
    )

    payment_date: date = Field(
        default_factory=date.today,
        description="Date payment was received",
        alias="paymentDate",
    )

    payment_method: PaymentMethod = Field(
        ..., description="Payment method used", alias="paymentMethod"
    )

    reference: str | None = Field(
        None,
        max_length=200,
        description="Payment reference (check number, transaction ID, etc.)",
        alias="reference",
    )

    notes: str | None = Field(
        None,
        max_length=1000,
        description="Internal notes about this payment",
        alias="notes",
    )

    @field_validator("reference", "notes", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        """Convert empty strings to None."""
        if v == "" or (isinstance(v, str) and v.strip() == ""):
            return None
        return v

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "amount": 608.07,
                "paymentDate": "2026-03-30",
                "paymentMethod": "bank_transfer",
                "reference": "TXN-123456789",
                "notes": "Payment received via M-Pesa",
            }
        },
    }


class PaymentResponse(BaseModel):
    """Schema for payment in responses."""

    id: UUID
    amount: Decimal
    payment_date: date
    payment_method: str
    reference: str | None = None
    notes: str | None = None
    created_at: datetime
    recorded_by: UUID | None = None

    model_config = {"from_attributes": True}


# INVOICE REQUEST SCHEMAS


class InvoiceCreate(BaseModel):
    """Schema for creating a new invoice."""

    # Required Fields
    customer_id: UUID = Field(
        ..., description="Customer receiving this invoice", alias="customerId"
    )

    transaction_date: date = Field(
        default_factory=date.today,
        description="Invoice issue date",
        alias="transactionDate",
    )

    due_date: date = Field(..., description="Payment due date", alias="dueDate")

    currency: Currency = Field(
        default=Currency.KES, description="Invoice currency", alias="currency"
    )

    # Line Items (at least one required)
    line_items: list[InvoiceLineItemCreate] = Field(
        ...,
        min_length=1,
        description="Invoice line items (at least one required)",
        alias="lineItems",
    )

    # Optional Fields
    rfq_number: str | None = Field(
        None,
        max_length=100,
        description="Purchase order or RFQ reference",
        alias="rfqNumber",
    )

    notes: str | None = Field(
        None, max_length=5000, description="Customer-facing notes", alias="notes"
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
    # invoices inherit our own (owner) VAT configuration.
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

    @field_validator("rfq_number", "notes", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        """Convert empty strings to None."""
        if v == "" or (isinstance(v, str) and v.strip() == ""):
            return None
        return v

    @model_validator(mode="after")
    def validate_due_date(self) -> "InvoiceCreate":
        """Ensure due date is not before transaction date."""
        if self.due_date < self.transaction_date:
            raise ValueError("due_date must be on or after transaction_date")
        return self

    @model_validator(mode="after")
    def validate_discount(self) -> "InvoiceCreate":
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
    def validate_vat(self) -> "InvoiceCreate":
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
                        "description": "Microsoft 365 Suite",
                        "quantity": 120.55,
                        "unitPrice": 120.55,
                        "taxType": "vat_16",
                    }
                ],
                "rfqNumber": "PO-2026-001",
                "notes": "Thank you for your business",
                "discountType": "percentage",
                "discountPercentage": 10.0,
            }
        },
    }


class InvoiceUpdate(BaseModel):
    """Schema for updating an existing invoice (only editable fields)."""

    # Customer can only be changed in DRAFT status
    customer_id: UUID | None = Field(None, alias="customerId")

    transaction_date: date | None = Field(None, alias="transactionDate")
    due_date: date | None = Field(None, alias="dueDate")
    currency: Currency | None = None

    line_items: list[InvoiceLineItemCreate] | None = Field(
        None, min_length=1, alias="lineItems"
    )

    rfq_number: str | None = Field(None, max_length=100, alias="rfqNumber")
    notes: str | None = Field(None, max_length=5000)

    discount_type: DiscountType | None = Field(None, alias="discountType")
    discount_amount: Decimal | None = Field(None, ge=0, alias="discountAmount")
    discount_percentage: Decimal | None = Field(
        None, ge=0, le=100, alias="discountPercentage"
    )

    # Document-level VAT (editable while the invoice is editable). The
    # service validates that an effective rate exists whenever the toggle
    # ends up enabled.
    vat_enabled: bool | None = Field(None, alias="vatEnabled")
    vat_rate: Decimal | None = Field(None, ge=0, le=1, alias="vatRate")

    @field_validator("rfq_number", "notes", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        """Convert empty strings to None."""
        if v == "" or (isinstance(v, str) and v.strip() == ""):
            return None
        return v

    @model_validator(mode="after")
    def validate_due_date(self) -> "InvoiceUpdate":
        """Ensure due date is not before transaction date if both provided."""
        if (
            self.due_date is not None
            and self.transaction_date is not None
            and self.due_date < self.transaction_date
        ):
            raise ValueError("due_date must be on or after transaction_date")
        return self

    model_config = {"populate_by_name": True}


# INVOICE RESPONSE SCHEMAS


class InvoiceResponse(BaseModel):
    """Complete invoice data for API responses."""

    id: UUID
    invoice_number: str
    invoice_reference: str
    customer_id: UUID

    transaction_date: date
    due_date: date

    status: str
    currency: str

    subtotal: Decimal
    discount_type: str | None = None
    discount_amount: Decimal | None = None
    discount_percentage: Decimal | None = None
    tax_total: Decimal
    total_due: Decimal
    amount_paid: Decimal
    balance_due: Decimal

    rfq_number: str | None = None
    notes: str | None = None

    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None = None
    paid_at: datetime | None = None

    created_by: UUID | None = None
    version: int

    # Relationships
    customer: CustomerSummary
    line_items: list[InvoiceLineItemResponse] = Field(default_factory=list)
    payments: list[PaymentResponse] = Field(default_factory=list)

    # Computed fields
    @computed_field
    @property
    def is_editable(self) -> bool:
        """Check if invoice can be edited."""
        return self.status in [InvoiceStatus.DRAFT, InvoiceStatus.SENT]

    @computed_field
    @property
    def is_overdue(self) -> bool:
        """Check if invoice is overdue."""
        return (
            self.status not in [InvoiceStatus.PAID, InvoiceStatus.CANCELED]
            and self.due_date < date.today()
            and self.balance_due > 0
        )

    @computed_field
    @property
    def days_overdue(self) -> int:
        """Calculate days overdue."""
        if not self.is_overdue:
            return 0
        return (date.today() - self.due_date).days

    model_config = {"from_attributes": True}


class InvoiceSummary(BaseModel):
    """Lightweight invoice summary for list views."""

    id: UUID
    invoice_number: str
    invoice_reference: str
    customer_id: UUID
    customer_name: str  # Joined from customer table

    transaction_date: date
    due_date: date

    status: str
    currency: str
    total_due: Decimal
    balance_due: Decimal

    created_at: datetime

    @computed_field
    @property
    def is_overdue(self) -> bool:
        """Check if invoice is overdue."""
        return (
            self.status not in [InvoiceStatus.PAID, InvoiceStatus.CANCELED]
            and self.due_date < date.today()
            and self.balance_due > 0
        )

    @computed_field
    @property
    def days_overdue(self) -> int:
        """Calculate days overdue."""
        if not self.is_overdue:
            return 0
        return (date.today() - self.due_date).days

    model_config = {"from_attributes": True}


class InvoiceStatusCounts(BaseModel):
    """Invoice counts by status for dashboard."""

    all: int = 0
    draft: int = 0
    sent: int = 0
    partial: int = 0
    paid: int = 0
    overdue: int = 0
    canceled: int = 0


class InvoiceStatisticsResponse(BaseModel):
    """Response schema for invoice statistics."""

    total_invoices: int = Field(..., description="Total number of invoices in period")
    total_invoiced: Decimal = Field(..., description="Total invoiced amount")
    total_paid: Decimal = Field(..., description="Total amount paid")
    total_outstanding: Decimal = Field(..., description="Total outstanding balance")
    average_invoice_value: Decimal = Field(..., description="Average invoice amount")
    average_days_to_payment: float = Field(
        ..., description="Average days from invoice to payment"
    )
    overdue_count: int = Field(..., description="Number of overdue invoices")
    overdue_amount: Decimal = Field(..., description="Total overdue amount")
    date_from: date | None = Field(None, description="Start date of period")
    date_to: date | None = Field(None, description="End date of period")

    model_config = {"from_attributes": True}


# ACTION SCHEMAS


class InvoiceSendRequest(BaseModel):
    """Schema for sending an invoice via email."""

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
        default=True, description="Whether to attach invoice PDF", alias="attachPdf"
    )

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "toEmail": "customer@example.com",
                "subject": "Invoice IN-0101 from Priori Technologies",
                "body": "Please find attached your invoice.",
                "attachPdf": True,
            }
        },
    }


class InvoiceSendResponse(BaseModel):
    """Response after sending an invoice."""

    invoice_id: UUID
    sent_to: str
    sent_at: datetime
    message: str = "Invoice sent successfully"


class InvoiceMarkSentRequest(BaseModel):
    """Schema for marking invoice as sent without emailing."""

    sent_at: datetime | None = Field(
        None,
        description="Timestamp when invoice was sent (defaults to now)",
        alias="sentAt",
    )

    model_config = {"populate_by_name": True}


class InvoiceDuplicateResponse(BaseModel):
    """Response after duplicating an invoice."""

    original_invoice_id: UUID
    new_invoice_id: UUID
    new_invoice_number: str
    message: str = "Invoice duplicated successfully"


class InvoiceCalculationResponse(BaseModel):
    """Response for invoice total calculations (preview)."""

    subtotal: Decimal
    discount_value: Decimal
    tax_total: Decimal
    total_due: Decimal

    line_items: list[dict] = Field(
        default_factory=list, description="Line items with calculated totals"
    )


# FILTER & SEARCH SCHEMAS


class InvoiceFilterParams(BaseModel):
    """Query parameters for filtering invoices."""

    status: InvoiceStatus | None = Field(None, description="Filter by status")

    customer_id: UUID | None = Field(
        None, description="Filter by customer", alias="customerId"
    )

    date_from: date | None = Field(
        None,
        description="Filter invoices from this date (transaction_date)",
        alias="dateFrom",
    )

    date_to: date | None = Field(
        None,
        description="Filter invoices up to this date (transaction_date)",
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
        description="Search in invoice number, customer name, or reference",
    )

    model_config = {"populate_by_name": True}


# DETAIL VIEW SCHEMAS


class InvoiceDetailResponse(BaseModel):
    """Enhanced invoice detail with customer information."""

    invoice: InvoiceResponse
    customer: dict  # CustomerResponse from customers module

    # Computed summaries
    total_line_items: int
    total_payments: int
    next_actions: list[str] = Field(
        default_factory=list, description="Available actions based on current status"
    )


# EXPORT SCHEMAS


class InvoiceExportRequest(BaseModel):
    """Request parameters for Excel export."""

    status: InvoiceStatus | None = None
    customer_id: UUID | None = Field(None, alias="customerId")
    date_from: date | None = Field(None, alias="dateFrom")
    date_to: date | None = Field(None, alias="dateTo")

    include_line_items: bool = Field(
        default=False,
        alias="includeLineItems",
        description="Include line items in export",
    )

    model_config = {"populate_by_name": True}


# VALIDATION ERROR SCHEMAS


class InvoiceValidationError(BaseModel):
    """Detailed validation error for invoice operations."""

    field: str
    message: str
    error_code: str


class InvoiceOperationResponse(BaseModel):
    """Generic response for invoice operations."""

    success: bool
    message: str
    invoice_id: UUID | None = None
    errors: list[InvoiceValidationError] = Field(default_factory=list)
