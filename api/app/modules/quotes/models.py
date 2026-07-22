"""SQLAlchemy models for quotes module."""

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
from app.common.reporting_time import check_is_overdue, reporting_date
from app.constants.enums import (
    Currency,
    DiscountType,
    QuoteStatus,
    TaxType,
)


class Quote(Base):
    """
    Quote entity for sales quotations with financial calculations.

    Lifecycle: DRAFT → SENT → APPROVED → INVOICED
                             ↓
                          EXPIRED

    Conversion to an invoice requires the APPROVED state; a SENT quote must
    be approved first (no direct SENT → INVOICED transition).

    Financial Formula:
        subtotal = SUM(line_items.line_total)
        discount_value = discount_amount OR (subtotal * discount_percentage / 100)
        tax_total = SUM(line_items.tax_amount)
        total_due = subtotal - discount_value + tax_total
    """

    __tablename__ = "quotes"

    __table_args__ = (
        # Check constraints for data integrity
        CheckConstraint(
            "status IN ('draft', 'sent', 'approved', 'invoiced', 'expired')",
            name="ck_quotes_valid_status",
        ),
        CheckConstraint(
            "due_date >= transaction_date",
            name="ck_quotes_due_after_transaction",
        ),
        CheckConstraint(
            "subtotal >= 0",
            name="ck_quotes_subtotal_non_negative",
        ),
        CheckConstraint(
            "total_due >= 0",
            name="ck_quotes_total_non_negative",
        ),
        CheckConstraint(
            "currency IN ('KES', 'USD', 'EUR', 'GBP')",
            name="ck_quotes_valid_currency",
        ),
        CheckConstraint(
            "(discount_type = 'amount' AND discount_percentage IS NULL) OR "
            "(discount_type = 'percentage' AND discount_amount IS NULL) OR "
            "(discount_type IS NULL AND discount_amount IS NULL AND discount_percentage IS NULL)",
            name="ck_quotes_discount_type_consistency",
        ),
        CheckConstraint(
            "vat_rate IS NULL OR (vat_rate >= 0 AND vat_rate <= 1)",
            name="ck_quotes_vat_rate_range",
        ),
        CheckConstraint(
            "vat_enabled = false OR vat_rate IS NOT NULL",
            name="ck_quotes_vat_rate_present_when_enabled",
        ),
        # Indexes for common queries
        Index("ix_quotes_customer_status", "customer_id", "status"),
        Index("ix_quotes_status_due_date", "status", "due_date"),
        Index("ix_quotes_transaction_date", "transaction_date"),
        Index("ix_quotes_created_at", "created_at"),
        # Unique constraints
        UniqueConstraint("quote_number", name="uq_quotes_quote_number"),
        UniqueConstraint("quote_reference", name="uq_quotes_quote_reference"),
    )

    # Primary Key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Quote Identification
    quote_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="System-generated quote number (e.g., QTE-20260315)",
    )

    quote_reference: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="User-facing quote reference (e.g., QT-0101)",
    )

    # Foreign Keys
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Customer receiving this quote",
    )

    # Dates
    transaction_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Quote issue date",
    )

    due_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
        comment="Quote expiry date",
    )

    # Status & Currency
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=QuoteStatus.DRAFT,
        server_default=text("'draft'"),
        index=True,
        comment="Current quote status",
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=Currency.KES,
        server_default=text("'KES'"),
        comment="Quote currency (ISO 4217)",
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

    # Document-level VAT (sales VAT toggle — mirrors PO-level VAT). When
    # enabled, tax_total is computed once on the discounted subtotal at
    # vat_rate and line items persist tax_type=no_tax / tax_amount=0.
    vat_enabled: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Enable document-level VAT computed on the discounted subtotal",
    )

    vat_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
        nullable=True,
        comment="Document-level VAT rate as a fraction (e.g. 0.1600)",
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
        comment="Final quote amount: subtotal - discount + tax",
    )

    # Optional Fields
    rfq_rfp_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="RFQ/RFP reference number",
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Customer-facing notes/terms (appears on quote)",
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
        comment="When quote was sent to customer",
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When quote was approved",
    )

    invoiced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When quote was converted to invoice",
    )

    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When quote expired (auto-set by scheduled job)",
    )

    # Audit Fields
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who created this quote",
    )

    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who approved this quote",
    )

    # Immutable owner-header snapshot captured at issue time.
    owner_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("owner_profile_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
        comment="Owner profile as it was when this quote was issued",
    )

    # Optimistic Locking
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Version number for optimistic locking",
    )

    # Related Invoice (when converted)
    related_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Invoice created from this quote",
    )

    # Relationships
    customer = relationship(
        "Customer",
        back_populates="quotes",
        lazy="joined",
    )

    line_items = relationship(
        "QuoteLineItem",
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="QuoteLineItem.line_number",
        lazy="selectin",
    )

    related_invoice = relationship(
        "Invoice",
        foreign_keys=[related_invoice_id],
        lazy="select",
    )

    # Properties
    @property
    def is_editable(self) -> bool:
        """Check if quote can be edited."""
        return self.status in [QuoteStatus.DRAFT, QuoteStatus.SENT]

    @property
    def is_expired(self) -> bool:
        """Check if quote is past due date (centralized predicate)."""

        return check_is_overdue(
            self.status,
            self.due_date,
            terminal_statuses={QuoteStatus.APPROVED, QuoteStatus.INVOICED},
        )

    @property
    def days_until_expiry(self) -> int:
        """Calculate days until expiry (negative if expired)."""
        return (self.due_date - reporting_date()).days

    @property
    def can_convert_to_invoice(self) -> bool:
        """Check if quote can be converted to invoice.

        Accepts APPROVED (direct) and SENT (auto-approved during conversion)
        quotes that are not expired and have not already been converted.
        """
        return (
            self.status in (QuoteStatus.APPROVED, QuoteStatus.SENT)
            and not self.is_expired
            and self.related_invoice_id is None
        )

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
            f"<Quote(id={self.id}, number={self.quote_number}, "
            f"status={self.status}, total={self.total_due})>"
        )


class QuoteLineItem(Base):
    """
    Individual line item within a quote.

    Each line represents a product/service being quoted.
    """

    __tablename__ = "quote_line_items"

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="ck_quote_line_items_quantity_positive",
        ),
        CheckConstraint(
            "unit_price >= 0",
            name="ck_quote_line_items_price_non_negative",
        ),
        CheckConstraint(
            "line_total >= 0",
            name="ck_quote_line_items_total_non_negative",
        ),
        CheckConstraint(
            "tax_amount >= 0",
            name="ck_quote_line_items_tax_non_negative",
        ),
        UniqueConstraint(
            "quote_id", "line_number", name="uq_quote_line_items_quote_line_number"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    quote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quotes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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
        comment="Product/service name",
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Detailed line item description",
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
    quote = relationship("Quote", back_populates="line_items")

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
            f"<QuoteLineItem(id={self.id}, quote={self.quote_id}, "
            f"line={self.line_number}, item={self.item_name}, total={self.line_total})>"
        )
