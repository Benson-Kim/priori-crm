import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import (
    UUID,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.database import Base
from app.constants.enums import (
    BillingCurrency,
    BillingPaymentTerms,
    Currency,
    CustomerStatus,
    CustomerType,
    Industry,
    TaxTreatment,
)

#: SQL IN-list literals shared by the ORM CHECK constraints and the Alembic
#: migration, derived from the enums so the two layers cannot drift.
_BILLING_CURRENCY_CHECK_VALUES = ", ".join(f"'{m.value}'" for m in BillingCurrency)
_INDUSTRY_CHECK_VALUES = ", ".join(f"'{m.value}'" for m in Industry)
_TAX_TREATMENT_CHECK_VALUES = ", ".join(f"'{m.value}'" for m in TaxTreatment)
_PAYMENT_TERMS_CHECK_VALUES = ", ".join(f"'{m.value}'" for m in BillingPaymentTerms)


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
        CheckConstraint(
            "(customer_type = 'individual' AND first_name IS NOT NULL AND last_name IS NOT NULL) OR "
            "(customer_type = 'business')",
            name="ck_customers_individual_requires_name",
        ),
        CheckConstraint(
            f"industry IS NULL OR industry IN ({_INDUSTRY_CHECK_VALUES})",
            name="ck_customers_valid_industry",
        ),
        CheckConstraint(
            "primary_currency IS NULL OR "
            f"primary_currency IN ({_BILLING_CURRENCY_CHECK_VALUES})",
            name="ck_customers_valid_primary_currency",
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

    first_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Contact person first name (required for individual customers)",
    )

    last_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Contact person last name (required for individual customers)",
    )

    # Optional: a customer may be recorded from a phone call or a walk-in with
    # no email on file. The unique constraint still holds for supplied
    # addresses - Postgres treats NULLs as distinct, so any number of rows may
    # omit one.
    email: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
        comment="Primary email address",
    )

    phone: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
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

    version: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Optimistic-locking version counter",
    )

    industry: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Industry classification (ratified design vocabulary, #53)",
    )

    tenant_domain: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Microsoft 365 tenant domain (e.g. acme.onmicrosoft.com)",
    )

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Account owner (sales rep) responsible for this customer",
    )

    primary_currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
        comment="Which billing profile (USD|KES) is the customer's default",
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

    postal_code: Mapped[str | None] = mapped_column(
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

    billing_profiles = relationship(
        "CustomerBillingProfile",
        back_populates="customer",
        # Profiles are owned by the customer row: they carry no independent
        # financial history, so they live and die with it (the DB-level
        # ondelete=CASCADE mirrors this for out-of-ORM deletes).
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CustomerBillingProfile.currency",
        lazy="selectin",
    )

    def __init__(self, *args, **kwargs):
        """Support legacy `is_active` constructor arguments for tests and callers."""
        if "is_active" in kwargs:
            is_active = kwargs.pop("is_active")
            if "status" not in kwargs:
                kwargs["status"] = (
                    CustomerStatus.ACTIVE if is_active else CustomerStatus.INACTIVE
                )
        super().__init__(*args, **kwargs)

    def __repr__(self) -> str:
        """String representation for debugging."""
        if self.customer_type == CustomerType.BUSINESS:
            name = self.company_name
        else:
            name = self.full_name
        return f"<Customer(id={self.id}, type={self.customer_type}, name='{name}')>"

    @property
    def full_name(self) -> str:
        """Get customer's full name.

        Business customers may have no contact-person name on file — the
        names are optional there (only individuals are required to supply
        both), so this falls back to an empty string per part rather than
        rendering the literal "None".
        """
        return f"{self.first_name or ''} {self.last_name or ''}".strip()

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
            self.postal_code,
            self.country,
        ]
        return ", ".join(filter(None, parts))


class CustomerBillingProfile(Base):
    """Per-currency (USD/KES) billing profile for a customer.

    Every customer carries exactly one profile per billing currency, created
    atomically with the customer (and backfilled for pre-existing rows).
    ``synced``/``synced_at`` form the internal posting/readiness flag toward
    accounting inside Business Central (see ADR 0010): any edit flips the
    profile back to unsynced until it is pushed again.
    """

    __tablename__ = "customer_billing_profiles"

    __table_args__ = (
        UniqueConstraint(
            "customer_id",
            "currency",
            name="uq_customer_billing_profiles_customer_id_currency",
        ),
        CheckConstraint(
            f"currency IN ({_BILLING_CURRENCY_CHECK_VALUES})",
            name="ck_customer_billing_profiles_valid_currency",
        ),
        CheckConstraint(
            f"tax_treatment IN ({_TAX_TREATMENT_CHECK_VALUES})",
            name="ck_customer_billing_profiles_valid_tax_treatment",
        ),
        CheckConstraint(
            f"payment_terms IN ({_PAYMENT_TERMS_CHECK_VALUES})",
            name="ck_customer_billing_profiles_valid_payment_terms",
        ),
        CheckConstraint(
            "credit_limit >= 0",
            name="ck_customer_billing_profiles_credit_limit_non_negative",
        ),
        # Hygiene/notification query ("profiles not yet pushed to
        # accounting"): a partial index over the customer_id of unsynced
        # rows serves the ?unsynced=true EXISTS probe directly, unlike a
        # low-selectivity full boolean index.
        Index(
            "ix_customer_billing_profiles_unsynced_customer",
            "customer_id",
            postgresql_where=text("NOT synced"),
            sqlite_where=text("NOT synced"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        comment="Billing currency of this profile (USD or KES)",
    )

    code: Mapped[str] = mapped_column(
        String(30),
        unique=True,
        nullable=False,
        comment=(
            "Accounting profile code: name slug + monotonic reference-"
            "sequence suffix + currency (e.g. BAR-0007-USD)"
        ),
    )

    payment_terms: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Payment terms label (e.g. 'On receipt', '30 days')",
    )

    tax_treatment: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="Tax treatment display value; maps 1:1 onto TaxType",
    )

    credit_limit: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        nullable=False,
        comment="Credit limit expressed in the profile's own currency",
    )

    synced: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        comment="Whether the profile has been pushed to accounting",
    )

    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the profile was last pushed to accounting",
    )

    version: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Optimistic-locking version counter",
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

    customer = relationship("Customer", back_populates="billing_profiles")

    def __repr__(self) -> str:
        return (
            f"<CustomerBillingProfile(code='{self.code}', "
            f"customer_id={self.customer_id}, synced={self.synced})>"
        )
