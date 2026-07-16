import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
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
    DiscountType,
    InvoiceStatus,
    TaxType,
)


class Invoice(Base):
    """
    Invoice entity with financial calculations and state machine.

    Lifecycle: DRAFT → SENT → (PARTIAL →) PAID | OVERDUE | CANCELED

    Financial Formula:
        subtotal = SUM(line_items.line_total)
        discount_value = discount_amount OR (subtotal * discount_percentage / 100)
        tax_total = SUM(line_items.tax_amount)
        total_due = subtotal - discount_value + tax_total
        balance_due = total_due - amount_paid
    """

    __tablename__ = "invoices"

    __table_args__ = (
        # Check constraints for data integrity
        CheckConstraint(
            "status IN ('draft', 'sent', 'partial', 'paid', 'overdue', 'canceled')",
            name="ck_invoices_valid_status",
        ),
        CheckConstraint(
            "due_date >= transaction_date",
            name="ck_invoices_due_after_transaction",
        ),
        CheckConstraint(
            "subtotal >= 0",
            name="ck_invoices_subtotal_non_negative",
        ),
        CheckConstraint(
            "total_due >= 0",
            name="ck_invoices_total_non_negative",
        ),
        CheckConstraint(
            "amount_paid >= 0",
            name="ck_invoices_amount_paid_non_negative",
        ),
        # NOTE: there is deliberately no ``balance_due >= 0`` CHECK. This app
        # records payments rather than taking them, so an overpayment is a
        # legitimate recordable event and balance_due (= total_due -
        # amount_paid) is allowed to go negative to represent a credit owed
        # back. See migration a6b7c8d9e0f1.
        CheckConstraint(
            "currency IN ('KES', 'USD', 'EUR', 'GBP')",
            name="ck_invoices_valid_currency",
        ),
        CheckConstraint(
            "(discount_type = 'amount' AND discount_percentage IS NULL) OR "
            "(discount_type = 'percentage' AND discount_amount IS NULL) OR "
            "(discount_type IS NULL AND discount_amount IS NULL AND discount_percentage IS NULL)",
            name="ck_invoices_discount_type_consistency",
        ),
        CheckConstraint(
            "vat_rate IS NULL OR (vat_rate >= 0 AND vat_rate <= 1)",
            name="ck_invoices_vat_rate_range",
        ),
        CheckConstraint(
            "vat_enabled = false OR vat_rate IS NOT NULL",
            name="ck_invoices_vat_rate_present_when_enabled",
        ),
        # Indexes for common queries
        Index("ix_invoices_customer_status", "customer_id", "status"),
        Index("ix_invoices_status_due_date", "status", "due_date"),
        Index("ix_invoices_transaction_date", "transaction_date"),
        Index("ix_invoices_created_at", "created_at"),
        # Unique constraint on invoice number
        UniqueConstraint("invoice_number", name="uq_invoices_invoice_number"),
    )

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Invoice Identification
    invoice_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="System-generated invoice number (e.g., INV-20240707)",
    )

    invoice_reference: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="User-facing invoice reference (e.g., IN-0101)",
    )

    # Foreign Keys
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Customer receiving this invoice",
    )

    # Dates
    transaction_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Invoice issue date",
    )

    due_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
        comment="Payment due date",
    )

    # Status & Currency
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=InvoiceStatus.DRAFT,
        server_default=text("'draft'"),
        index=True,
        comment="Current invoice status",
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=Currency.KES,
        server_default=text("'KES'"),
        comment="Invoice currency (ISO 4217)",
    )

    # Financial Amounts
    subtotal: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="Sum of all line items before discount/tax",
    )

    # Discount
    discount_type: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Type of discount: 'amount' or 'percentage'",
    )

    discount_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 2),
        nullable=True,
        comment="Fixed discount amount (if discount_type = 'amount')",
    )

    discount_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
        comment="Discount percentage 0-100 (if discount_type = 'percentage')",
    )

    tax_total: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="Sum of all tax amounts",
    )

    total_due: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="Final invoice amount: subtotal - discount + tax",
    )

    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="Total amount paid against this invoice",
    )

    balance_due: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="Remaining balance: total_due - amount_paid",
    )

    # Optional Fields
    rfq_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Purchase order or RFQ reference number",
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Customer-facing notes (appears on invoice)",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        comment="Record creation timestamp",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
        comment="Last update timestamp",
    )

    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When invoice was sent to customer",
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When invoice was fully paid",
    )

    # Audit Fields
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who created this invoice",
    )

    # Immutable owner-header snapshot captured at issue time. NULL
    # for never-issued (DRAFT) invoices, which render from the live profile.
    owner_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("owner_profile_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
        comment="Owner profile as it was when this invoice was issued",
    )

    # Optimistic Locking
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Version number for optimistic locking",
    )

    # Relationships
    customer = relationship(
        "Customer",
        back_populates="invoices",
        lazy="joined",
    )

    line_items = relationship(
        "InvoiceLineItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLineItem.line_number",
        lazy="selectin",
    )

    payments = relationship(
        "Payment",
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="Payment.payment_date.desc()",
        lazy="selectin",
    )

    # Properties
    @property
    def is_editable(self) -> bool:
        """Check if invoice can be edited."""
        return self.status in [InvoiceStatus.DRAFT, InvoiceStatus.SENT]

    @property
    def is_paid(self) -> bool:
        """Check if invoice is fully paid."""
        return self.status == InvoiceStatus.PAID or self.balance_due <= 0

    @property
    def is_overdue(self) -> bool:
        """Check if invoice is past due date (centralized predicate)."""
        from app.common.financial import check_is_overdue

        # is_paid also covers balance_due <= 0, which the status-only predicate
        # cannot see, so keep it as an additional guard.
        return not self.is_paid and check_is_overdue(
            self.status,
            self.due_date,
            terminal_statuses={InvoiceStatus.PAID, InvoiceStatus.CANCELED},
        )

    @property
    def days_overdue(self) -> int:
        """Calculate days overdue (0 if not overdue)."""
        from datetime import date

        if not self.is_overdue:
            return 0
        return (date.today() - self.due_date).days

    @property
    def discount_value(self) -> Decimal:
        """Calculate actual discount value."""
        if self.discount_type == DiscountType.AMOUNT and self.discount_amount:
            return self.discount_amount
        elif self.discount_type == DiscountType.PERCENTAGE and self.discount_percentage:
            return self.subtotal * (self.discount_percentage / Decimal("100"))
        return Decimal("0.00")

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<Invoice(id={self.id}, number={self.invoice_number}, "
            f"status={self.status}, total={self.total_due})>"
        )


class InvoiceLineItem(Base):
    """
    Individual line item within an invoice.

    """

    __tablename__ = "invoice_line_items"

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="ck_line_items_quantity_positive",
        ),
        CheckConstraint(
            "unit_price >= 0",
            name="ck_line_items_price_non_negative",
        ),
        CheckConstraint(
            "line_total >= 0",
            name="ck_line_items_total_non_negative",
        ),
        CheckConstraint(
            "tax_amount >= 0",
            name="ck_line_items_tax_non_negative",
        ),
        Index("ix_line_items_invoice_id", "invoice_id"),
        UniqueConstraint(
            "invoice_id", "line_number", name="uq_line_items_invoice_line_number"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )

    # TODO: Add product_id when Products module exists
    # product_id: Mapped[uuid.UUID | None] = mapped_column(...)

    line_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Order of line items (1-based)",
    )

    item_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Product/service name (preserved from quote on conversion)",
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Line item description",
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        comment="Quantity of items",
    )

    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        comment="Price per unit",
    )

    line_total: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        comment="Total before tax: quantity x unit_price",
    )

    tax_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=TaxType.VAT_16,
        comment="Tax type for this line",
    )

    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="Tax amount for this line",
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

    # Relationships
    invoice = relationship("Invoice", back_populates="line_items")

    @validates("quantity")
    def validate_quantity_positive(self, key: str, value: Decimal) -> Decimal:
        """Ensure quantity is strictly positive."""
        if value <= 0:
            raise ValueError(f"{key} must be greater than 0")
        return value

    @property
    def tax_rate(self) -> Decimal:
        """Get tax rate as decimal (e.g., 0.16 for 16% VAT)."""
        return get_tax_rate(TaxType(self.tax_type))

    def __repr__(self) -> str:
        return (
            f"<InvoiceLineItem(id={self.id}, invoice={self.invoice_id}, "
            f"line={self.line_number}, total={self.line_total})>"
        )


class Payment(Base):
    """
    Payment record against an invoice.

    Multiple payments can be applied to a single invoice (partial payments).
    """

    __tablename__ = "payments"

    __table_args__ = (
        CheckConstraint(
            "amount > 0",
            name="ck_payments_amount_positive",
        ),
        Index("ix_payments_invoice_id", "invoice_id"),
        Index("ix_payments_payment_date", "payment_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        comment="Payment amount",
    )

    payment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Date payment was received",
    )

    payment_method: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="Payment method used",
    )

    reference: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Payment reference (check number, transaction ID, etc.)",
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Internal notes about this payment",
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

    # Relationships
    invoice = relationship("Invoice", back_populates="payments")

    def __repr__(self) -> str:
        return (
            f"<Payment(id={self.id}, invoice={self.invoice_id}, "
            f"amount={self.amount}, method={self.payment_method})>"
        )
