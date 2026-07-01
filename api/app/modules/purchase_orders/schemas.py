"""
Pydantic schemas for the Purchase Orders API.

Financial contract (no discount in v1):
    line_total = quantity x unit_price
    tax_amount = line_total x tax_rate  (tax_rate from tax_type via get_tax_rate)
    subtotal   = Σ line_total
    tax_total  = Σ tax_amount
    total      = subtotal + tax_total

Mirrors the Expenses module (vendor-facing analog) field-for-field so totals
are guaranteed identical across the two vendor-facing document types. "Rate"/
"Amount" are UI/PDF labels only; the wire/DB contract stays
unit_price/line_total.

Scope:
- Currency is deliberately absent from PurchaseOrderUpdate — it is locked
  after first save (the service enforces this by only applying supplied
  fields).
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from app.common.validators import empty_str_to_none as normalize_empty_str
from app.constants.enums import Currency, PurchaseOrderStatus, TaxType

# TERMS & CONDITIONS length cap.
MAX_TERMS_AND_CONDITIONS_LENGTH = 2000


# PAYMENT SCHEMAS


class PurchaseOrderPaymentCreate(BaseModel):
    """Payload for recording a payment against a purchase order.

    Mirrors ExpensePaymentCreate: no payment_method field. amount must be
    > 0. Overpayment is allowed: this app records and reconciles payments
    rather than taking them, so an amount exceeding the current balance_due
    is recorded and drives balance_due negative (a credit owed back).

    ``document_id`` optionally links a proof-of-payment document that was
    previously uploaded to this purchase order (source: payment_modal).
    The service validates that the document exists and belongs to the same
    PO; an invalid or foreign document_id is rejected with a 404/400.
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
    reference: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "Transaction ID, cheque number, or remittance reference — required."
        ),
    )
    invoice_number: str | None = Field(
        None,
        max_length=200,
        alias="invoiceNumber",
        description=(
            "Free-text reference of the invoice/bill(s) this payment is paid "
            "against. Optional; one payment may cover several invoices."
        ),
    )
    currency: Currency = Field(
        ...,
        description=(
            "ISO 4217 currency the payment was made in. May differ from the PO "
            "currency; the applied (PO-currency) amount is amount * exchangeRate."
        ),
    )
    exchange_rate: Decimal = Field(
        ...,
        gt=0,
        alias="exchangeRate",
        description=(
            "Rate converting payment currency -> PO currency. Must be 1 when "
            "the payment currency equals the PO currency. User-overridable."
        ),
    )
    notes: str | None = Field(
        None,
        max_length=2000,
        description="Internal notes for this payment entry",
    )
    document_id: UUID | None = Field(
        None,
        alias="documentId",
        description=(
            "Optional proof-of-payment document previously uploaded to this "
            "purchase order (source: payment_modal). The document must belong "
            "to the same PO; a foreign or non-existent document_id is rejected."
        ),
    )

    @field_validator("invoice_number", "notes", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        return normalize_empty_str(v)

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "amount": 5000.00,
                "paymentDate": "2026-06-18",
                "reference": "TXN-9981234",
                "invoiceNumber": "INV-4471, INV-4472",
                "currency": "USD",
                "exchangeRate": 129.50000000,
                "notes": "Paid via bank transfer",
                "documentId": "123e4567-e89b-12d3-a456-426614174000",
            }
        },
    }


class PurchaseOrderPaymentUpdate(BaseModel):
    """Payload for editing an existing payment recorded against a PO.

    PATCH semantics via model_dump(exclude_unset=True) in the service: only
    supplied fields are applied. Changing ``amount`` re-derives the PO's
    amount_paid / balance_due and may re-open a PAID PO back to SENT (or
    settle a SENT PO to PAID) — handled atomically in the service under a
    row lock. Overpayment is allowed: the new amount may exceed the PO total,
    leaving balance_due negative to represent the credit owed back.
    """

    amount: Decimal | None = Field(
        None,
        gt=0,
        decimal_places=2,
        description=(
            "New payment amount — must be > 0. May overpay the PO (balance_due "
            "goes negative to represent the credit owed back)."
        ),
    )
    payment_date: date | None = Field(
        None,
        alias="paymentDate",
        description="Date the payment was made",
    )
    reference: str | None = Field(
        None,
        min_length=1,
        max_length=200,
        description=(
            "Transaction ID, cheque number, or remittance reference. When "
            "supplied it must be non-empty (reference is required and cannot "
            "be cleared)."
        ),
    )
    invoice_number: str | None = Field(
        None,
        max_length=200,
        alias="invoiceNumber",
        description="Free-text reference of the invoice/bill(s) paid against",
    )
    currency: Currency | None = Field(
        None,
        description="ISO 4217 currency the payment was made in",
    )
    exchange_rate: Decimal | None = Field(
        None,
        gt=0,
        alias="exchangeRate",
        description="Rate converting payment currency -> PO currency (> 0)",
    )
    notes: str | None = Field(
        None,
        max_length=2000,
        description="Internal notes for this payment entry",
    )

    @field_validator("invoice_number", "notes", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        return normalize_empty_str(v)

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "amount": 4500.00,
                "paymentDate": "2026-06-19",
                "reference": "TXN-9981234",
                "invoiceNumber": "INV-4471",
                "currency": "USD",
                "exchangeRate": 129.50000000,
                "notes": "Corrected amount",
            }
        },
    }


class PurchaseOrderPaymentResponse(BaseModel):
    """Payment record in API responses.

    ``document_id`` references the optional proof-of-payment attachment;
    the attachment's ``storage_key`` is never surfaced (security gate F).
    """

    id: UUID
    po_id: UUID
    amount: Decimal
    payment_date: date
    reference: str | None = None
    invoice_number: str | None = None
    currency: str
    exchange_rate: Decimal
    notes: str | None = None
    document_id: UUID | None = None
    created_at: datetime
    recorded_by: UUID | None = None

    model_config = {"from_attributes": True}


# DOCUMENT SCHEMAS


class PurchaseOrderDocumentResponse(BaseModel):
    """Document attachment in API responses.

    ``storage_key`` is intentionally excluded — it is an internal object
    path that must never be sent to clients (security gate F). ``file_size_kb``
    is a model @property surfaced via ``from_attributes`` so the list view can
    show the size in KB without the frontend re-deriving it.
    """

    id: UUID
    po_id: UUID
    payment_id: UUID | None = None
    filename: str
    file_size_bytes: int
    file_size_kb: float  # @property on the model — readable via from_attributes
    mime_type: str
    source: str
    document_type: str
    uploaded_at: datetime
    uploaded_by: UUID | None = None

    model_config = {"from_attributes": True}


# VENDOR SUMMARY


class VendorSummary(BaseModel):
    """Vendor fields surfaced on purchase-order responses.

    Mirrors the Expenses VendorSummary. ``email`` is included because the
    PO Send flow resolves the recipient from the vendor.
    """

    id: UUID
    vendor_name: str
    email: str | None = None
    phone_primary: str | None = None
    phone_secondary: str | None = None
    # Vendor's currency is the single source of truth for the PO currency
    # (pinned server-side on create). Surfaced so the editor preview can
    # display the right symbol without a separate vendor fetch.
    currency: str | None = None

    model_config = {"from_attributes": True}


# LINE ITEM SCHEMAS


class PurchaseOrderLineItemCreate(BaseModel):
    """Payload for a single PO line item — used on create and full line-item
    replace, and as the input to the calculation-preview endpoint.

    Mirrors ExpenseLineItemCreate field-for-field so totals are guaranteed
    identical across the two vendor-facing document types.
    """

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
                "itemName": "Steel bolts M8",
                "description": "Box of 500 grade-8.8 bolts",
                "quantity": 10,
                "unitPrice": 1200.00,
                "taxType": "vat_16",
            }
        },
    }


class PurchaseOrderLineItemResponse(BaseModel):
    """Line item in API responses — mirrors ExpenseLineItemResponse."""

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


# REQUEST SCHEMAS


class PurchaseOrderCreate(BaseModel):
    """Payload for creating a new purchase order (always starts as DRAFT).

    Mirrors ExpenseCreate adapted to the PO domain: ``order_date`` replaces
    ``expense_date`` and ``delivery_date`` is optional (and, when set, must be
    on or after ``order_date`` — also DB-enforced via a CHECK constraint).

    Deliberately excluded from the writable surface (derived server-side):
    - ``currency`` — pinned to the selected vendor's currency in the service;
    - ``compliance_ref`` — the eTIMS / compliance reference is taken from the
      vendor (its tax_id_pin) in the service, never client-supplied;
    - ``is_recurring`` — the recurring flag has been removed from the PO
      create flow (always persists False).
    """

    vendor_id: UUID = Field(
        ...,
        alias="vendorId",
        description="Vendor this purchase order is raised against — required",
    )
    order_date: date = Field(
        default_factory=date.today,
        alias="orderDate",
        description="Date the purchase order was raised",
    )
    delivery_date: date | None = Field(
        None,
        alias="deliveryDate",
        description="Expected delivery date — if set must be on or after order_date",
    )
    line_items: list[PurchaseOrderLineItemCreate] = Field(
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
    terms_and_conditions: str | None = Field(
        None,
        max_length=MAX_TERMS_AND_CONDITIONS_LENGTH,
        alias="termsAndConditions",
        description="Terms & conditions — max 2000 characters",
    )
    vat_enabled: bool = Field(
        default=False,
        alias="vatEnabled",
        description="Enable PO-level VAT computed on the subtotal",
    )
    vat_rate: Decimal | None = Field(
        None,
        ge=0,
        le=1,
        alias="vatRate",
        description=(
            "VAT rate as a fraction (e.g. 0.16 for 16%). Required when "
            "vatEnabled is true; sourced from the shared tax-rate table."
        ),
    )
    vat_compliance_ref: str | None = Field(
        None,
        max_length=255,
        alias="vatComplianceRef",
        description=(
            "VAT/compliance reference printed on the VAT line. Defaults from "
            "the owner profile's tax_pin when omitted; editable per PO."
        ),
    )

    @field_validator(
        "notes", "terms_and_conditions", "vat_compliance_ref", mode="before"
    )
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        return normalize_empty_str(v)

    @model_validator(mode="after")
    def validate_delivery_date(self) -> "PurchaseOrderCreate":
        """delivery_date, when supplied, must be on or after order_date."""
        if self.delivery_date is not None and self.delivery_date < self.order_date:
            raise ValueError("delivery_date must be on or after order_date")
        return self

    @model_validator(mode="after")
    def validate_vat(self) -> "PurchaseOrderCreate":
        """A VAT rate is required when VAT is enabled."""
        if self.vat_enabled and self.vat_rate is None:
            raise ValueError("vat_rate is required when vat_enabled is true")
        return self

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "vendorId": "123e4567-e89b-12d3-a456-426614174000",
                "orderDate": "2026-06-16",
                "deliveryDate": "2026-06-30",
                "lineItems": [
                    {
                        "itemName": "Steel bolts M8",
                        "description": "Box of 500 grade-8.8 bolts",
                        "quantity": 10,
                        "unitPrice": 1200.00,
                        "taxType": "vat_16",
                    }
                ],
                "notes": "Deliver to the Upper Hill warehouse.",
                "termsAndConditions": "Net 30. Goods remain returnable for 14 days.",
            }
        },
    }


class PurchaseOrderUpdate(BaseModel):
    """Payload for updating an existing purchase order.

    PATCH semantics via model_dump(exclude_unset=True) in the service —
    identical to ExpenseUpdate / QuoteUpdate.

    Deliberately excluded from the writable surface:
    - ``currency`` — locked after first save (never editable);
    - ``compliance_ref`` — derived from the vendor; a vendor change re-derives
      it in the service, so it is not client-editable;
    - ``is_recurring`` — the recurring field has been removed from the PO flow.

    The service update() only applies fields present in the payload, so a
    field absent here can never be changed through this endpoint.

    Editing gate enforced in service:
        DRAFT          -> editable
        SENT / PAID    -> BadRequestException (§13 inline banner)
    """

    vendor_id: UUID | None = Field(None, alias="vendorId")
    order_date: date | None = Field(None, alias="orderDate")
    delivery_date: date | None = Field(None, alias="deliveryDate")
    line_items: list[PurchaseOrderLineItemCreate] | None = Field(
        None,
        min_length=1,
        alias="lineItems",
    )
    notes: str | None = Field(None, max_length=5000)
    terms_and_conditions: str | None = Field(
        None,
        max_length=MAX_TERMS_AND_CONDITIONS_LENGTH,
        alias="termsAndConditions",
    )
    vat_enabled: bool | None = Field(None, alias="vatEnabled")
    vat_rate: Decimal | None = Field(None, ge=0, le=1, alias="vatRate")
    vat_compliance_ref: str | None = Field(
        None,
        max_length=255,
        alias="vatComplianceRef",
    )

    @field_validator(
        "notes", "terms_and_conditions", "vat_compliance_ref", mode="before"
    )
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        return normalize_empty_str(v)

    @model_validator(mode="after")
    def validate_delivery_date(self) -> "PurchaseOrderUpdate":
        """Validate date ordering only when both dates are explicitly supplied.

        The mixed case (one date in the payload, one from the DB) is guarded
        in the service against the persisted values.
        """
        if (
            self.delivery_date is not None
            and self.order_date is not None
            and self.delivery_date < self.order_date
        ):
            raise ValueError("delivery_date must be on or after order_date")
        return self

    model_config = {"populate_by_name": True}


# RESPONSE SCHEMAS


class PurchaseOrderResponse(BaseModel):
    """Full purchase-order detail — returned by create, update, and get.

    Mirrors ExpenseResponse adapted to the PO domain. ``storage_key`` is
    never present on any nested schema, per the security gate.
    """

    id: UUID
    po_number: str
    po_reference: str
    vendor_id: UUID

    order_date: date
    delivery_date: date | None = None
    status: str
    currency: str
    is_recurring: bool

    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    amount_paid: Decimal
    balance_due: Decimal

    vat_enabled: bool = False
    vat_rate: Decimal | None = None
    vat_compliance_ref: str | None = None

    compliance_ref: str | None = None
    notes: str | None = None
    terms_and_conditions: str | None = None

    converted_bill_id: UUID | None = None

    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None = None
    paid_at: datetime | None = None
    created_by: UUID | None = None
    version: int

    # Relationships
    vendor: VendorSummary
    line_items: list[PurchaseOrderLineItemResponse] = Field(default_factory=list)
    payments: list[PurchaseOrderPaymentResponse] = Field(default_factory=list)
    documents: list[PurchaseOrderDocumentResponse] = Field(default_factory=list)

    @computed_field
    @property
    def is_editable(self) -> bool:
        """Only DRAFT purchase orders are freely editable."""
        return self.status == PurchaseOrderStatus.DRAFT

    @computed_field
    @property
    def is_paid(self) -> bool:
        """Fully settled by explicit PAID status or a cleared balance on a SENT PO.

        A DRAFT PO with a zero total must NOT be considered paid — it has
        never been sent and no payment has been recorded against it. Only
        a PO that has been explicitly transitioned to PAID, or a SENT PO
        whose balance_due has been reduced to zero by recorded payments,
        is treated as settled.
        """
        return self.status == PurchaseOrderStatus.PAID or (
            self.status == PurchaseOrderStatus.SENT and self.balance_due <= 0
        )

    model_config = {"from_attributes": True}


# LIST / FILTER SCHEMAS


class PurchaseOrderSummary(BaseModel):
    """Lightweight row for the paginated list view (PRD §6.1).

    Built from a summary-column projection query joined to the vendor name
    — never a full ORM instance, so the list never loads line items (no
    N+1). Mirrors ExpenseSummary adapted to the PO domain.
    """

    id: UUID
    po_number: str
    po_reference: str
    vendor_id: UUID
    vendor_name: str  # joined from the vendors table in the list query

    order_date: date
    delivery_date: date | None = None
    status: str
    currency: str
    total: Decimal
    balance_due: Decimal
    is_recurring: bool
    converted_bill_id: UUID | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PurchaseOrderStatusCounts(BaseModel):
    """Status counts for the filter-tab bar ("All (160)").

    The PO lifecycle is DRAFT -> SENT -> PAID, so every status is included
    in ``all``. PAID is reached via record_payment (SENT -> PAID).
    """

    all: int = 0
    draft: int = 0
    sent: int = 0
    paid: int = 0  # fully settled POs (included in `all`)


class PurchaseOrderFilterParams(BaseModel):
    """Query parameters for filtering the purchase-orders list.

    Single contract shared by the list view and the Excel export so the two
    can never drift. Mirrors ExpenseFilterParams adapted to PO dates
    (order_date / delivery_date).
    """

    status: PurchaseOrderStatus | None = Field(None, description="Filter by status")
    vendor_id: UUID | None = Field(
        None,
        alias="vendorId",
        description="Filter by vendor",
    )
    date_from: date | None = Field(
        None,
        alias="dateFrom",
        description="order_date >= this value",
    )
    date_to: date | None = Field(
        None,
        alias="dateTo",
        description="order_date <= this value",
    )
    delivery_date_from: date | None = Field(
        None,
        alias="deliveryDateFrom",
        description="delivery_date >= this value",
    )
    delivery_date_to: date | None = Field(
        None,
        alias="deliveryDateTo",
        description="delivery_date <= this value",
    )
    search: str | None = Field(
        None,
        max_length=100,
        description="Search po_number, po_reference, or vendor name",
    )

    model_config = {"populate_by_name": True}


# SEND ACTION SCHEMAS


class PurchaseOrderSendRequest(BaseModel):
    """Payload for sending a purchase order to its vendor by email (PRD §6.6).

    All fields are optional: ``toEmail`` defaults to the vendor's email
    (resolved server-side), and ``subject`` / ``body`` fall back to the
    service-generated vendor-facing template when omitted.
    """

    to_email: str | None = Field(
        None,
        description="Override recipient email (defaults to the vendor email)",
        alias="toEmail",
    )
    subject: str | None = Field(
        None,
        max_length=200,
        description="Email subject (auto-generated if not provided)",
    )
    body: str | None = Field(
        None,
        max_length=5000,
        description="Email body (uses the template if not provided)",
    )
    attach_pdf: bool = Field(
        default=True,
        description="Whether to attach the purchase-order PDF",
        alias="attachPdf",
    )

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "toEmail": "vendor@example.com",
                "subject": "Purchase Order PO-000042 from Priori Technologies",
                "body": "Please find attached our purchase order.",
                "attachPdf": True,
            }
        },
    }


class PurchaseOrderSendResponse(BaseModel):
    """Response returned after sending a purchase order."""

    purchase_order_id: UUID
    sent_to: str
    sent_at: datetime
    message: str = "Purchase order sent successfully"


# DUPLICATE ACTION SCHEMA


class PurchaseOrderDuplicateResponse(BaseModel):
    """Response returned after duplicating a purchase order (PRD §6.10).

    Mirrors the Quote duplicate response: returns the original and the new
    DRAFT identifiers plus the new system reference so the frontend can
    redirect straight to the editable copy.
    """

    original_po_id: UUID
    new_po_id: UUID
    new_po_reference: str
    message: str = "Purchase order duplicated successfully"


# CALCULATION PREVIEW


class PurchaseOrderCalculationResponse(BaseModel):
    """
    Totals preview — POST /purchase-orders/calculate.

    No discount in v1: total = subtotal + tax_total. The field is named
    ``total`` (not ``total_due``) because a purchase order is a commitment
    to pay a vendor, not a receivable with a running balance
    """

    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    vat_enabled: bool = False
    vat_rate: Decimal | None = None
    line_items: list[dict] = Field(
        default_factory=list,
        description="Line items with calculated totals",
    )
