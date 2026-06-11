import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import (
    UUID,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.database import Base
from app.constants.enums import Currency, CustomerStatus, CustomerType


class Customer(Base):
    """Customer entity for the accounting platform."""

    __tablename__ = "customers"

    __table_args__ = (
        # Check constraints for data integrity
        CheckConstraint(
            "customer_type IN ('individual', 'business')",
            name="ck_customers_valid_customer_type",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive', 'suspended', 'deleted')",
            name="ck_customers_valid_status",
        ),
        CheckConstraint(
            "currency IN ('KES', 'USD', 'EUR', 'GBP')",
            name="ck_customers_valid_currency",
        ),
        CheckConstraint(
            "balance >= 0",
            name="ck_customers_balance_non_negative",
        ),
        CheckConstraint(
            "(customer_type = 'business' AND company_name IS NOT NULL) OR "
            "(customer_type = 'individual')",
            name="ck_customers_business_requires_company_name",
        ),
        # Composite indexes for common query patterns
        Index("ix_customers_status_created", "status", "created_at"),
        Index("ix_customers_type_status", "customer_type", "status"),
        Index(
            "ix_customers_search",
            "first_name",
            "last_name",
            "company_name",
            postgresql_using="gin",
            postgresql_ops={
                "first_name": "gin_trgm_ops",
                "last_name": "gin_trgm_ops",
                "company_name": "gin_trgm_ops",
            },
        ),
        # Full-text search index (requires pg_trgm extension)
        Index(
            "ix_customers_fulltext_search",
            text(
                "to_tsvector('english', coalesce(company_name, '') || ' ' || coalesce(first_name, '') || ' ' || coalesce(last_name, '') || ' ' || coalesce(email, ''))"
            ),
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    customer_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        comment="Customer type: individual or business",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CustomerStatus.ACTIVE,
        server_default=text("'active'"),
        index=True,
        comment="Customer account status",
    )

    company_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="Company name for business customers",
    )

    vat_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="VAT/Tax registration number",
    )

    website: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Company website URL",
    )

    first_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Contact person first name",
    )

    last_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Contact person last name",
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        comment="Primary email address",
    )

    phone: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Primary phone number",
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=Currency.KES,
        server_default=text("'KES'"),
        comment="Default currency for transactions",
    )

    balance: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
        comment="Current account balance",
    )

    address: Mapped[str] = mapped_column(
        Text,
        nullable=True,
        comment="Primary address line",
    )

    address2: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Secondary address line",
    )

    city: Mapped[str] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="City",
    )

    province: Mapped[str] = mapped_column(
        String(100),
        nullable=True,
        comment="State/Province/Region",
    )

    postal_code: Mapped[str] = mapped_column(
        String(20),
        nullable=True,
        comment="Postal/ZIP code",
    )

    country: Mapped[str] = mapped_column(
        String(2),
        nullable=True,
        index=True,
        comment="ISO 3166-1 alpha-2 country code",
    )

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
        comment="Record last update timestamp",
    )

    # Relationships
    invoices = relationship(
        "Invoice",
        back_populates="customer",
        cascade="save-update, merge",
        lazy="dynamic",
    )

    quotes = relationship(
        "Quote",
        back_populates="customer",
        # Non-destructive cascade to match `invoices` and the DB-level
        # FK ondelete=RESTRICT on Quote.customer_id. A destructive
        # "all, delete-orphan" here would silently wipe quote history on
        # customer hard-delete.
        cascade="save-update, merge",
        # passive_deletes lets the database FK (ondelete=RESTRICT) reject the
        # delete instead of SQLAlchemy first emitting UPDATE quotes SET
        # customer_id=NULL (which violates the NOT NULL column). The RESTRICT
        # then surfaces as a clean IntegrityError on the customers DELETE.
        passive_deletes="all",
        lazy="select",
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        if self.customer_type == CustomerType.BUSINESS:
            name = self.company_name
        else:
            name = f"{self.first_name} {self.last_name}"
        return f"<Customer(id={self.id}, type={self.customer_type}, name='{name}')>"

    @property
    def full_name(self) -> str:
        """Get customer's full name."""
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def display_name(self) -> str:
        """Get display name (company name for businesses, full name for individuals)."""
        if self.customer_type == CustomerType.BUSINESS and self.company_name:
            return self.company_name
        return self.full_name

    @property
    def is_active(self) -> bool:
        """Check if customer account is active."""
        return self.status == CustomerStatus.ACTIVE

    @property
    def customer_since_year(self) -> int:
        """Get year customer was created (for 'Customer since YYYY' display)."""
        return self.created_at.year

    @property
    def formatted_address(self) -> str:
        """Get formatted full address for display."""
        parts = [
            self.address,
            self.address2,
            self.city,
            self.province,
            self.postal_code,
            self.country,
        ]
        return ", ".join(filter(None, parts))
