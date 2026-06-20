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
    > 0 and is additionally validated server-side against the current
    balance_due (overpayment is rejected with a 400).

    ``document_id`` optionally links a proof-of-payment document that was
    previously uploaded to this purchase order (source: payment_modal).
    The service validates that the document exists and belongs to the same
    PO; an invalid or foreign document_id is rejected with a 404/400.
    """

    amount: Decimal = Field(
        ...,
        gt=0,
        decimal_places=2,
        description="Payment amount — must be > 0 and <= current balance_due",
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
    document_id: UUID | None = Field(
        None,
        alias="documentId",
        description=(
            "Optional proof-of-payment document previously uploaded to this "
            "purchase order (source: payment_modal). The document must belong "
            "to the same PO; a foreign or non-existent document_id is rejected."
        ),
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
                "paymentDate": "2026-06-18",
                "reference": "TXN-9981234",
                "notes": "Paid via bank transfer",
                "documentId": "123e4567-e89b-12d3-a456-426614174000",
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
    filename: str
    file_size_bytes: int
    file_size_kb: float  # @property on the model — readable via from_attributes
    mime_type: str
    source: str
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
    currency: Currency = Field(
        default=Currency.KES,
        description="ISO 4217 currency code — locked after first save",
    )
    is_recurring: bool = Field(
        default=False,
        alias="isRecurring",
        description="Recurring-PO flag — informational only in v1",
    )
    line_items: list[PurchaseOrderLineItemCreate] = Field(
        ...,
        min_length=1,
        alias="lineItems",
        description="At least one line item required",
    )
    compliance_ref: str | None = Field(
        None,
        max_length=255,
        alias="complianceRef",
        description="Jurisdiction-aware compliance reference (free text)",
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

    @field_validator("compliance_ref", "notes", "terms_and_conditions", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        return normalize_empty_str(v)

    @model_validator(mode="after")
    def validate_delivery_date(self) -> "PurchaseOrderCreate":
        """delivery_date, when supplied, must be on or after order_date."""
        if self.delivery_date is not None and self.delivery_date < self.order_date:
            raise ValueError("delivery_date must be on or after order_date")
        return self

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "vendorId": "123e4567-e89b-12d3-a456-426614174000",
                "orderDate": "2026-06-16",
                "deliveryDate": "2026-06-30",
                "currency": "KES",
                "isRecurring": False,
                "lineItems": [
                    {
                        "itemName": "Steel bolts M8",
                        "description": "Box of 500 grade-8.8 bolts",
                        "quantity": 10,
                        "unitPrice": 1200.00,
                        "taxType": "vat_16",
                    }
                ],
                "complianceRef": "LPO-2026-0042",
                "notes": "Deliver to the Upper Hill warehouse.",
                "termsAndConditions": "Net 30. Goods remain returnable for 14 days.",
            }
        },
    }


class PurchaseOrderUpdate(BaseModel):
    """Payload for updating an existing purchase order.

    PATCH semantics via model_dump(exclude_unset=True) in the service —
    identical to ExpenseUpdate / QuoteUpdate.

    currency is deliberately excluded — it is locked after first save. The
    service update() enforces this by only applying fields present in the
    payload; since currency never appears here it can never be changed.

    Editing gate enforced in service:
        DRAFT          -> editable
        SENT / PAID    -> BadRequestException (§13 inline banner)
    """

    vendor_id: UUID | None = Field(None, alias="vendorId")
    order_date: date | None = Field(None, alias="orderDate")
    delivery_date: date | None = Field(None, alias="deliveryDate")
    is_recurring: bool | None = Field(None, alias="isRecurring")
    line_items: list[PurchaseOrderLineItemCreate] | None = Field(
        None,
        min_length=1,
        alias="lineItems",
    )
    compliance_ref: str | None = Field(None, max_length=255, alias="complianceRef")
    notes: str | None = Field(None, max_length=5000)
    terms_and_conditions: str | None = Field(
        None,
        max_length=MAX_TERMS_AND_CONDITIONS_LENGTH,
        alias="termsAndConditions",
    )

    @field_validator("compliance_ref", "notes", "terms_and_conditions", mode="before")
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
    line_items: list[dict] = Field(
        default_factory=list,
        description="Line items with calculated totals",
    )
