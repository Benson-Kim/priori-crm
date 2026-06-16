"""
Vendor entity and supporting models for the Purchases module.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.common.database import Base
from app.constants.enums import Currency, VendorStatus


class Vendor(Base):
    """
    Vendor master record.

    Lifecycle:
        [Created] → active ↔ inactive → [Deleted — service-layer enforced]

    Payables Formula (computed at query time, never stored):
        payables     = SUM(balance) WHERE transaction.status IN ('pending', 'overdue')
        overdue_total = SUM(balance) WHERE transaction.status = 'overdue'
    """

    __tablename__ = "vendors"

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_vendors_valid_status",
        ),
        CheckConstraint(
            "char_length(trim(vendor_name)) > 0",
            name="ck_vendors_name_non_empty",
        ),
        CheckConstraint(
            "currency IN ('KES', 'USD', 'EUR', 'GBP')",
            name="ck_vendors_valid_currency",
        ),
        Index("ix_vendors_status_name", "status", "vendor_name"),
        Index("ix_vendors_email", "email"),
        Index("ix_vendors_contact_id", "contact_id"),
        Index("ix_vendors_created_at", "created_at"),
        Index(
            "uq_vendors_email_not_null",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        comment="System-generated vendor UUID",
    )

    vendor_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Trading name of the vendor — the only required field",
    )

    address: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Vendor street / mailing address",
    )

    email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Primary email address. Unique across non-NULL values",
    )

    country: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="ISO 3166-1 country name or alpha-2 code",
    )

    phone_primary: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        comment="Primary phone number with country dialling code prefix",
    )

    phone_secondary: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        comment="Secondary phone number with country dialling code prefix",
    )

    website: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Vendor website URL — normalised to include https:// on save",
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=Currency.KES,
        server_default=text("'KES'"),
        comment=(
            "Vendor's preferred billing currency. "
            "Defaults to the organisation account currency."
        ),
    )

    vat_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="VAT registration number",
    )

    tax_id_pin: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="KRA PIN or equivalent tax ID for the vendor's jurisdiction",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=VendorStatus.ACTIVE,
        server_default=text("'active'"),
        index=True,
    )

    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Source CRM contact when created via Search Existing Vendor path",
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Internal notes about this vendor (not shown on documents)",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        comment="Record creation timestamp. ",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
        comment="Last update timestamp",
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="UUID of the user who created this vendor record",
    )

    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="UUID of the user who last updated this vendor record",
    )

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment=(
            "Incremented on every write. Used for optimistic concurrency control."
        ),
    )

    # Relationships
    expenses = relationship(
        "Expense",
        back_populates="vendor",
        lazy="noload",
    )

    purchase_orders = relationship(
        "PurchaseOrder",
        back_populates="vendor",
        lazy="noload",
    )

    @validates("vendor_name")
    def validate_vendor_name_not_blank(self, key: str, value: str) -> str:
        """Reject blank-whitespace-only vendor names"""
        if not value or not value.strip():
            raise ValueError("vendor_name must not be blank")
        return value.strip()

    @validates("email")
    def validate_email_lowercase(self, key: str, value: str | None) -> str | None:
        """Normalise email to lowercase."""
        if value is None:
            return None
        normalised = value.strip().lower()
        return normalised if normalised else None

    @validates("website")
    def validate_website_normalise(self, key: str, value: str | None) -> str | None:
        """
        Normalise website URLs: strip whitespace, add https:// if no scheme.
        """
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not stripped.startswith(("http://", "https://")):
            stripped = f"https://{stripped}"
        return stripped

    @property
    def vendor_since_year(self) -> int:
        return self.created_at.year

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def display_initials(self) -> str:
        """
        Two-letter initials for the avatar circle in the vendor detail view.
        """
        parts = self.vendor_name.split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        name = self.vendor_name.strip()
        return (name[:2]).upper() if len(name) >= 2 else name[0].upper()

    def __repr__(self) -> str:
        return (
            f"<Vendor(id={self.id}, name={self.vendor_name!r}, "
            f"status={self.status}, version={self.version})>"
        )
