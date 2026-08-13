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
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
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
    vat_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vat_rate: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    tax_pin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Opaque, sanitized storage key (not a servable path) for the logo, or
    # NULL when no logo is set. Served/streamed via StorageService.
    logo_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Org-scoped document-settings defaults
    # Stored once on the singleton (org scope), NOT per document. Applied to a
    # Purchase Order at create time only, so editing a default never alters an
    # existing PO. NULL means "use the built-in default" (see
    # app.constants.settings_defaults), so a never-touched profile still yields
    # the out-of-the-box values without a data migration backfill.
    default_terms_and_conditions: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Org default Terms & Conditions pre-filled on new POs "
            "(<= 2000 chars, enforced in the schema layer)"
        ),
    )
    default_send_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Org default Send-email body pre-filled in the PO Send modal",
    )
    # ISO 3166-1 alpha-2 jurisdiction driving the jurisdiction-aware
    # compliance-reference label (PO-10). NULL falls back to the built-in
    # DEFAULT_ORG_JURISDICTION.
    jurisdiction: Mapped[str | None] = mapped_column(
        String(2),
        nullable=True,
        comment="Org jurisdiction (ISO 3166-1 alpha-2), e.g. 'KE'",
    )

    # Onboarding task template (issue #41). Ordered list of task labels
    # copied onto each new onboarding checklist at CREATE TIME ONLY. NULL
    # means "use the built-in DEFAULT_ONBOARDING_TASKS", so a never-touched
    # profile still yields the design's 7 seeded tasks without a backfill.
    onboarding_task_template: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
        comment=(
            "Org onboarding checklist template (ordered task labels); "
            "NULL = built-in 7-task default"
        ),
    )
    # Bumped every time the resolved template CHANGES (template versioning):
    # each onboarding snapshots the version it was created from, so editing
    # the template never mutates existing checklists and the provenance of
    # every checklist stays auditable.
    onboarding_template_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        comment="Monotonic version of the onboarding task template",
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

    def __repr__(self) -> str:
        return f"<OwnerProfile {self.full_name!r}>"


class OwnerModuleSetting(Base):
    """Per-owner module entitlement (feature toggle) override.

    One row per (owner profile, module key) — but only when the default is
    overridden: a MISSING row means the module is ENABLED (default-on), so a
    fresh install exposes every module without any seeding. Essential
    modules (see ``ESSENTIAL_MODULES``) never get a disabled row; the admin
    API rejects such attempts with a 422.
    """

    __tablename__ = "owner_module_settings"

    __table_args__ = (
        UniqueConstraint(
            "owner_profile_id",
            "module_key",
            name="uq_owner_module_settings_profile_module",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    owner_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("owner_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    module_key: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="ModuleKey value this row overrides (missing row = enabled)",
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Admin user who last changed this toggle (null = system)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<OwnerModuleSetting {self.module_key}={self.enabled}>"


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
    vat_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vat_rate: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    tax_pin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Snapshotted so the issued document's jurisdiction-aware compliance label
    # is frozen at issue time and a later Settings change cannot re-label
    # an already-sent document. The two PO-create defaults
    # (default_terms_and_conditions / default_send_message) are NOT snapshotted:
    # they are create-time inputs copied onto the PO row itself, not part of the rendered header.
    jurisdiction: Mapped[str | None] = mapped_column(String(2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __repr__(self) -> str:
        return f"<OwnerProfileSnapshot {self.content_hash[:8]}>"
