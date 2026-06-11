"""Owner / document-header models.

Two tables, one bounded context:

- ``OwnerProfile`` — the live, editable singleton that drives every *live*
  document header (replaces the drifted ``COMPANY_INFO`` front-end constant
  and the backend ``settings.APP_NAME``-only rendering).
- ``OwnerProfileSnapshot`` — an append-only, immutable copy of the profile
  as it was when a document was issued. Documents reference a snapshot, so
  editing the live profile never retroactively re-brands an issued document
  (locked product decision). Snapshots are content-addressed via
  ``content_hash`` so re-issuing while the profile is unchanged reuses the
  existing row instead of growing the table.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.database import Base

# Fixed primary key for the singleton live profile row. A single, well-known
# id keeps "there is exactly one live profile" simple and race-free (the
# service upserts this row) without a separate "is_active" flag.
SINGLETON_PROFILE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class OwnerProfile(Base):
    """Live, editable organisation identity printed on documents."""

    __tablename__ = "owner_profiles"

    __table_args__ = (
        CheckConstraint(
            "char_length(full_name) > 0",
            name="ck_owner_profiles_full_name_not_blank",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=SINGLETON_PROFILE_ID,
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # "Location/Watermark": a short display string rendered as a subtle
    # location/watermark line on the document header (product-clarified as
    # a display-only field; snapshotted like the rest).
    location_watermark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    tax_pin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Opaque, sanitized storage key (not a servable path) for the logo, or
    # NULL when no logo is set. Served/streamed via StorageService.
    logo_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

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

    def __repr__(self) -> str:
        return f"<OwnerProfile {self.full_name!r}>"


class OwnerProfileSnapshot(Base):
    """Immutable, append-only snapshot of the profile at issue time.

    Never updated after creation. ``content_hash`` is a digest of the
    snapshotted fields so an unchanged profile maps to a single reusable row.
    """

    __tablename__ = "owner_profile_snapshots"

    __table_args__ = (
        CheckConstraint(
            "char_length(content_hash) > 0",
            name="ck_owner_snapshots_hash_not_blank",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # Content digest of all snapshotted fields; unique so identical profile
    # state collapses to one row (re-issuing doesn't grow the table).
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    location_watermark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    tax_pin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __repr__(self) -> str:
        return f"<OwnerProfileSnapshot {self.content_hash[:8]}>"
