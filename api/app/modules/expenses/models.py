"""
Expense ORM models — Purchases module.

Lifecycle: PENDING → PAID | OVERDUE
No discount field in v1
No CANCELED terminal state — removal is hard-delete.

Financial formula:
    line_total  = quantity x unit_price
    tax_amount  = line_total x tax_rate
    subtotal    = Σ line_total
    tax_total   = Σ tax_amount
    total_due   = subtotal + tax_total   ← no discount in v1
    balance_due = total_due - amount_paid
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
from app.common.financial import calculate_days_overdue, check_is_overdue, get_tax_rate
from app.constants.enums import Currency, DocumentSource, ExpenseStatus, TaxType


class Expense(Base):
    """
    Expense record payable to a vendor.
    """

    __tablename__ = "expenses"

    __table_args__ = (
        # Status integrity
        CheckConstraint(
            "status IN ('pending', 'paid', 'overdue', 'canceled')",
            name="ck_expenses_valid_status",
        ),
        # Date ordering
        CheckConstraint(
            "due_date >= expense_date",
            name="ck_expenses_due_after_expense_date",
        ),
        # Financial non-negativity
        CheckConstraint(
            "subtotal >= 0",
            name="ck_expenses_subtotal_non_negative",
        ),
        CheckConstraint(
            "tax_total >= 0",
            name="ck_expenses_tax_total_non_negative",
        ),
        CheckConstraint(
            "total_due >= 0",
            name="ck_expenses_total_due_non_negative",
        ),
        CheckConstraint(
            "amount_paid >= 0",
            name="ck_expenses_amount_paid_non_negative",
        ),
        CheckConstraint(
            "balance_due >= 0",
            name="ck_expenses_balance_due_non_negative",
        ),
        CheckConstraint(
            "currency IN ('KES', 'USD', 'EUR', 'GBP')",
            name="ck_expenses_valid_currency",
        ),
        # Query-path indexes
        Index("ix_expenses_vendor_status", "vendor_id", "status"),
        Index("ix_expenses_status_due_date", "status", "due_date"),
        Index("ix_expenses_expense_date", "expense_date"),
        Index("ix_expenses_created_at", "created_at"),
        # Unique references
        UniqueConstraint("expense_number", name="uq_expenses_expense_number"),
        UniqueConstraint("expense_reference", name="uq_expenses_expense_reference"),
    )

    # Primary key
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Identification
    expense_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="Sortable system number e.g. EXP-20260519-001",
    )

    expense_reference: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
        comment="Short human-facing reference e.g. EXP-0042",
    )

    # Foreign keys
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Vendor this expense is payable to",
    )

    # Dates
    expense_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Date the expense was incurred",
    )

    due_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
        comment="Payment due date — must be >= expense_date (DB enforced)",
    )

    # Status & currency
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ExpenseStatus.PENDING,
        server_default=text("'pending'"),
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

    # Recurring flag
    is_recurring: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment=("Recurring-bill flag. Informational only in v1 — no auto-generation."),
    )

    # Financial amounts
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

    total_due: Mapped[Decimal] = mapped_column(
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
        comment="Cumulative amount recorded via ExpensePayment records",
    )

    balance_due: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="total_due - amount_paid",
    )

    # Optional metadata
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Vendor-facing or internal notes",
    )

    # Timestamps
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

    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp when expense transitioned to PAID",
    )

    # Audit
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="User who created this expense record",
    )

    # Optimistic locking
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Incremented on every mutating operation — optimistic lock",
    )

    # Relationships
    vendor = relationship(
        "Vendor",
        back_populates="expenses",
        lazy="joined",
    )

    line_items = relationship(
        "ExpenseLineItem",
        back_populates="expense",
        cascade="all, delete-orphan",
        order_by="ExpenseLineItem.line_number",
        lazy="selectin",
    )

    payments = relationship(
        "ExpensePayment",
        back_populates="expense",
        cascade="all, delete-orphan",
        order_by="ExpensePayment.payment_date",
        lazy="selectin",
    )

    documents = relationship(
        "ExpenseDocument",
        back_populates="expense",
        cascade="all, delete-orphan",
        order_by="ExpenseDocument.uploaded_at",
        lazy="selectin",
        foreign_keys="ExpenseDocument.expense_id",
    )

    # Computed properties ─

    @property
    def is_editable(self) -> bool:
        """
        PENDING and OVERDUE expenses are editable.
        PAID is terminal — read-only.
        Consistent with InvoiceService.is_editable pattern.
        """
        return self.status in (ExpenseStatus.PENDING, ExpenseStatus.OVERDUE)

    @property
    def is_paid(self) -> bool:
        return self.status == ExpenseStatus.PAID or self.balance_due <= 0

    @property
    def is_overdue(self) -> bool:
        return check_is_overdue(self.status, self.due_date)

    @property
    def days_overdue(self) -> int:
        return calculate_days_overdue(self.status, self.due_date)

    def __repr__(self) -> str:
        return (
            f"<Expense(id={self.id}, ref={self.expense_reference}, "
            f"status={self.status}, total={self.total_due})>"
        )


class ExpenseLineItem(Base):
    """
    Individual line item within an expense.

    line_total = quantity x unit_price
    tax_amount = line_total x tax_rate  (derived from tax_type via get_tax_rate)

    Mirrors InvoiceLineItem exactly, scoped to expenses.
    """

    __tablename__ = "expense_line_items"

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="ck_expense_line_items_quantity_positive",
        ),
        CheckConstraint(
            "unit_price >= 0",
            name="ck_expense_line_items_price_non_negative",
        ),
        CheckConstraint(
            "line_total >= 0",
            name="ck_expense_line_items_total_non_negative",
        ),
        CheckConstraint(
            "tax_amount >= 0",
            name="ck_expense_line_items_tax_non_negative",
        ),
        UniqueConstraint(
            "expense_id",
            "line_number",
            name="uq_expense_line_items_expense_line_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expenses.id", ondelete="CASCADE"),
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

    # Relationships
    expense = relationship("Expense", back_populates="line_items")

    # Validators
    @validates("quantity")
    def validate_quantity(self, key: str, value: Decimal) -> Decimal:
        """Mirror of InvoiceLineItem.validate_quantity_positive."""
        if value <= 0:
            raise ValueError(f"{key} must be greater than 0")
        return value

    @property
    def tax_rate(self) -> Decimal:
        """Convenience accessor — consistent with InvoiceLineItem."""
        return get_tax_rate(TaxType(self.tax_type))

    def __repr__(self) -> str:
        return (
            f"<ExpenseLineItem(id={self.id}, expense={self.expense_id}, "
            f"line={self.line_number}, total={self.line_total})>"
        )


class ExpenseDocument(Base):
    """
    Supporting document attached to an expense.

    Defined BEFORE ExpensePayment so the FK from ExpensePayment.document_id
    resolves without forward-reference issues at mapper configuration time.

    source tracks upload context for audit:
        form          → Create / Edit form
        view          → View screen attach button
        payment_modal → Record Payment modal (proof-of-payment)
    """

    __tablename__ = "expense_documents"

    __table_args__ = (
        CheckConstraint(
            "source IN ('form', 'view', 'payment_modal')",
            name="ck_expense_documents_valid_source",
        ),
        CheckConstraint(
            "file_size_bytes > 0",
            name="ck_expense_documents_size_positive",
        ),
        Index("ix_expense_documents_expense_id", "expense_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expenses.id", ondelete="CASCADE"),
        nullable=False,
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

    # Relationships
    expense = relationship(
        "Expense",
        back_populates="documents",
        foreign_keys=[expense_id],
    )

    # Computed helpers
    @property
    def file_size_kb(self) -> float:
        """Kilobytes rounded to one decimal — used by ExpenseDocumentResponse."""
        return round(self.file_size_bytes / 1024, 1)

    def __repr__(self) -> str:
        return (
            f"<ExpenseDocument(id={self.id}, filename={self.filename}, "
            f"source={self.source})>"
        )


class ExpensePayment(Base):
    """
    Auditable payment record against an expense.

    Distinct from InvoicePayment:
    - No payment_method field
    - Has optional document_id FK for proof-of-payment uploaded in modal

    Multiple payments accumulate. ExpenseService.record_payment() updates
    Expense.amount_paid and Expense.balance_due atomically.
    """

    __tablename__ = "expense_payments"

    __table_args__ = (
        CheckConstraint(
            "amount > 0",
            name="ck_expense_payments_amount_positive",
        ),
        Index("ix_expense_payments_expense_id", "expense_id"),
        Index("ix_expense_payments_payment_date", "payment_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    expense_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expenses.id", ondelete="CASCADE"),
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

    # Proof-of-payment document uploaded in Record Payment modal
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # References expense_documents — table defined above this class
        ForeignKey("expense_documents.id", ondelete="SET NULL"),
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

    # Relationships
    expense = relationship("Expense", back_populates="payments")

    proof_document = relationship(
        "ExpenseDocument",
        foreign_keys=[document_id],
    )

    def __repr__(self) -> str:
        return (
            f"<ExpensePayment(id={self.id}, expense={self.expense_id}, "
            f"amount={self.amount}, date={self.payment_date})>"
        )
