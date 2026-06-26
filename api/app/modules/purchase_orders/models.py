"""
Purchase Order ORM models — Purchases module.

Lifecycle: DRAFT → SENT → PAID
No discount field in v1.

Mirrors the Expense models (vendor-facing, document attachments) rather
than the Quote/Invoice (customer-facing) models. Purchase Orders are
raised against a Vendor.

Financial formula:
    line_total = quantity x unit_price
    tax_amount = line_total x tax_rate   (derived from tax_type via get_tax_rate)
    subtotal   = Σ line_total
    tax_total  = Σ tax_amount
    total      = subtotal + tax_total    ← no discount in v1

NOTE on converted_bill_id: the Bills module/table does not exist yet, so
this is a nullable plain UUID column WITHOUT a database foreign-key
constraint. It is forward-tolerant of the Bills module being absent (see
PO-01 acceptance criteria / coordinate with PO-07). When the Bills module
lands, a later migration can add the FK constraint and ondelete=SET NULL
behaviour.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.common.database import Base
from app.common.financial import get_tax_rate
from app.constants.enums import (
    Currency,
    DocumentSource,
    PurchaseOrderStatus,
    TaxType,
)


class PurchaseOrder(Base):
    """
    Purchase Order raised against a vendor.

    total = subtotal + tax_total  (no discount in v1)
    """

    __tablename__ = "purchase_orders"

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'sent', 'paid')",
            name="ck_purchase_orders_valid_status",
        ),
        CheckConstraint(
            "delivery_date IS NULL OR delivery_date >= order_date",
            name="ck_purchase_orders_delivery_after_order_date",
        ),
        CheckConstraint(
            "subtotal >= 0",
            name="ck_purchase_orders_subtotal_non_negative",
        ),
        CheckConstraint(
            "tax_total >= 0",
            name="ck_purchase_orders_tax_total_non_negative",
        ),
        CheckConstraint(
            "total >= 0",
            name="ck_purchase_orders_total_non_negative",
        ),
        CheckConstraint(
            "currency IN ('KES', 'USD', 'EUR', 'GBP')",
            name="ck_purchase_orders_valid_currency",
        ),
        CheckConstraint(
            "amount_paid >= 0",
            name="ck_purchase_orders_amount_paid_non_negative",
        ),
        # NOTE: no ``balance_due >= 0`` CHECK on purpose. Overpayment is
        # recordable and balance_due (= total - amount_paid) may go negative
        # to represent a credit owed back. See migration a6b7c8d9e0f1.
        Index("ix_purchase_orders_vendor_status", "vendor_id", "status"),
        Index("ix_purchase_orders_status_delivery_date", "status", "delivery_date"),
        Index("ix_purchase_orders_order_date", "order_date"),
        Index("ix_purchase_orders_created_at", "created_at"),
        UniqueConstraint("po_number", name="uq_purchase_orders_po_number"),
        UniqueConstraint("po_reference", name="uq_purchase_orders_po_reference"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    po_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="Sortable system number e.g. PO-20260616-001",
    )

    po_reference: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
        comment="Short human-facing reference e.g. PO-000042",
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Vendor this purchase order is raised against",
    )

    order_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Date the purchase order was raised",
    )

    delivery_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Expected delivery date — if set must be >= order_date (DB enforced)",
    )

    compliance_ref: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Jurisdiction-aware compliance reference (no format validation v1)",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PurchaseOrderStatus.DRAFT,
        server_default=text("'draft'"),
        index=True,
        comment="Current lifecycle status",
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=Currency.KES,
        server_default=text("'KES'"),
        comment="ISO 4217 currency — locked after first save",
    )

    is_recurring: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Recurring-PO flag. Informational only in v1 — no auto-generation.",
    )

    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="Σ line_total across all line items",
    )

    tax_total: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="Σ tax_amount across all line items",
    )

    total: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="subtotal + tax_total (no discount in v1)",
    )

    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="Cumulative amount recorded via PurchaseOrderPayment records",
    )

    balance_due: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="total - amount_paid",
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Vendor-facing or internal notes",
    )

    terms_and_conditions: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Terms & conditions (max 2000 chars enforced in schema layer)",
    )

    converted_bill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment=(
            "Bill produced by Convert-to-Bill (PO-07). Plain UUID, no FK yet "
            "— Bills module not present; FK added in a later migration."
        ),
    )

    owner_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("owner_profile_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
        comment="Immutable owner-profile snapshot used for this PO's header",
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the PO transitioned to SENT",
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when the PO was fully settled (transitioned to PAID)",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who created this purchase order",
    )

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Incremented on every mutating operation — optimistic lock",
    )

    vendor = relationship(
        "Vendor",
        back_populates="purchase_orders",
        lazy="joined",
    )

    line_items = relationship(
        "PurchaseOrderLineItem",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderLineItem.line_number",
        lazy="selectin",
    )

    documents = relationship(
        "PurchaseOrderDocument",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderDocument.uploaded_at",
        lazy="selectin",
        foreign_keys="PurchaseOrderDocument.po_id",
    )

    payments = relationship(
        "PurchaseOrderPayment",
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderPayment.payment_date",
        lazy="selectin",
    )

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

    @property
    def is_editable(self) -> bool:
        """Only DRAFT purchase orders are freely editable."""
        return self.status == PurchaseOrderStatus.DRAFT

    def __repr__(self) -> str:
        return (
            f"<PurchaseOrder(id={self.id}, ref={self.po_reference}, "
            f"status={self.status}, total={self.total})>"
        )


class PurchaseOrderLineItem(Base):
    """
    Individual line item within a purchase order.

    line_total = quantity x unit_price
    tax_amount = line_total x tax_rate  (derived from tax_type via get_tax_rate)

    Mirrors ExpenseLineItem exactly, scoped to purchase orders.
    """

    __tablename__ = "purchase_order_line_items"

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="ck_po_line_items_quantity_positive",
        ),
        CheckConstraint(
            "unit_price >= 0",
            name="ck_po_line_items_price_non_negative",
        ),
        CheckConstraint(
            "line_total >= 0",
            name="ck_po_line_items_total_non_negative",
        ),
        CheckConstraint(
            "tax_amount >= 0",
            name="ck_po_line_items_tax_non_negative",
        ),
        UniqueConstraint(
            "po_id",
            "line_number",
            name="uq_po_line_items_po_line_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    line_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Display order (1-based)",
    )

    item_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Product or service name",
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Detailed line item description",
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )

    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
    )

    line_total: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        comment="quantity x unit_price",
    )

    tax_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=TaxType.VAT_16,
        comment="Tax type applied to this line (e.g. vat_16)",
    )

    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="line_total x tax_rate",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    purchase_order = relationship("PurchaseOrder", back_populates="line_items")

    @validates("quantity")
    def validate_quantity(self, key: str, value: Decimal) -> Decimal:
        """Mirror of ExpenseLineItem.validate_quantity."""
        if value <= 0:
            raise ValueError(f"{key} must be greater than 0")
        return value

    @property
    def tax_rate(self) -> Decimal:
        """Convenience accessor — consistent with ExpenseLineItem."""
        return get_tax_rate(TaxType(self.tax_type))

    def __repr__(self) -> str:
        return (
            f"<PurchaseOrderLineItem(id={self.id}, po={self.po_id}, "
            f"line={self.line_number}, total={self.line_total})>"
        )


class PurchaseOrderDocument(Base):
    """
    Supporting document attached to a purchase order.

    source tracks upload context for audit:
        form          → Create / Edit form
        view          → View screen attach button
        payment_modal → Record Payment modal (proof-of-payment)
    """

    __tablename__ = "purchase_order_documents"

    __table_args__ = (
        CheckConstraint(
            "source IN ('form', 'view', 'payment_modal')",
            name="ck_po_documents_valid_source",
        ),
        CheckConstraint(
            "file_size_bytes > 0",
            name="ck_po_documents_size_positive",
        ),
        Index("ix_purchase_order_documents_po_id", "po_id"),
        Index("ix_purchase_order_documents_payment_id", "payment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # purchase_order_payments.document_id already points back here, so this
        # FK closes a mutual cycle between the two tables. use_alter defers it
        # to an ALTER TABLE after both tables exist, so create_all (used by the
        # test schema builder) can order the tables without a
        # CircularDependencyError.
        ForeignKey(
            "purchase_order_payments.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_po_documents_payment_id",
        ),
        nullable=True,
        comment=(
            "Payment this document is proof-of-payment for. Set for documents "
            "uploaded in the Record Payment / payment-detail modal so a payment "
            "can carry any number of proof documents; NULL for PO-level (form / "
            "view) attachments."
        ),
    )

    filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Original filename as uploaded by user",
    )

    file_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="File size in bytes — displayed as KB in UI",
    )

    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="MIME type for correct Content-Type header on download",
    )

    storage_key: Mapped[str] = mapped_column(
        String(1000),
        nullable=False,
        comment="Object storage path / key — never exposed in API responses",
    )

    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DocumentSource.FORM,
        server_default=text("'form'"),
        comment="Upload context: form | view | payment_modal",
    )

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who attached the document",
    )

    purchase_order = relationship(
        "PurchaseOrder",
        back_populates="documents",
        foreign_keys=[po_id],
    )

    @property
    def file_size_kb(self) -> float:
        """Kilobytes rounded to one decimal — used by the document response schema."""
        return round(self.file_size_bytes / 1024, 1)

    def __repr__(self) -> str:
        return (
            f"<PurchaseOrderDocument(id={self.id}, filename={self.filename}, "
            f"source={self.source})>"
        )


class PurchaseOrderPayment(Base):
    """
    Auditable payment recorded against a purchase order.

    Mirrors ExpensePayment: no payment_method field, optional document_id
    FK to a proof-of-payment attachment uploaded in the Record Payment
    modal. Multiple payments accumulate; PurchaseOrderService.record_payment
    advances PurchaseOrder.amount_paid / balance_due atomically and settles
    the PO to PAID once the balance is cleared.

    Defined AFTER PurchaseOrderDocument so the document_id FK resolves
    without a forward reference at mapper-configuration time (mirrors the
    Expense models' ordering).
    """

    __tablename__ = "purchase_order_payments"

    __table_args__ = (
        CheckConstraint(
            "amount > 0",
            name="ck_po_payments_amount_positive",
        ),
        Index("ix_purchase_order_payments_po_id", "po_id"),
        Index("ix_purchase_order_payments_payment_date", "payment_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        comment="Amount settled in this payment transaction",
    )

    payment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Date payment was made — required",
    )

    reference: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Transaction ID, cheque number, or remittance reference",
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Internal notes for this payment entry",
    )

    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_order_documents.id", ondelete="SET NULL"),
        nullable=True,
        comment="Optional proof-of-payment document",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who recorded this payment",
    )

    purchase_order = relationship("PurchaseOrder", back_populates="payments")

    proof_document = relationship(
        "PurchaseOrderDocument",
        foreign_keys=[document_id],
    )

    # All proof-of-payment documents grouped under this payment (via
    # PurchaseOrderDocument.payment_id). A payment may carry several; the
    # collection is loaded eagerly so the payment view can list them all.
    documents = relationship(
        "PurchaseOrderDocument",
        foreign_keys="PurchaseOrderDocument.payment_id",
        order_by="PurchaseOrderDocument.uploaded_at",
        lazy="selectin",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return (
            f"<PurchaseOrderPayment(id={self.id}, po={self.po_id}, "
            f"amount={self.amount}, date={self.payment_date})>"
        )
