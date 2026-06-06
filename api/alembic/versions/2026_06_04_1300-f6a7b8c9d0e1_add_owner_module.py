"""add owner module: profiles + immutable snapshots (W3.6 / Section 0)

Revision ID: f6a7b8c9d0e1
Revises: c3d4e5f6a7b8
Create Date: 2026-06-04 13:00:00.000000

Creates the live singleton owner profile and the append-only, content-
addressed snapshot table that backs immutable historical document headers.

Chained after the OTP-index migration (e5f6a7b8c9d0) to keep a single linear
history now that the Wave 3 migrations have merged.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "owner_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("location_watermark", sa.String(length=255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("tax_pin", sa.String(length=50), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("logo_storage_key", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(full_name) > 0",
            name="ck_owner_profiles_full_name_not_blank",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "owner_profile_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("location_watermark", sa.String(length=255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("tax_pin", sa.String(length=50), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("logo_storage_key", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(content_hash) > 0",
            name="ck_owner_snapshots_hash_not_blank",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_owner_snapshots_content_hash"),
    )
    op.create_index(
        "ix_owner_profile_snapshots_content_hash",
        "owner_profile_snapshots",
        ["content_hash"],
        unique=False,
    )

    # Immutable owner-header snapshot reference on issued documents (V-DI-4).
    op.add_column(
        "invoices",
        sa.Column("owner_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_invoices_owner_snapshot",
        "invoices",
        "owner_profile_snapshots",
        ["owner_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "quotes",
        sa.Column("owner_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_quotes_owner_snapshot",
        "quotes",
        "owner_profile_snapshots",
        ["owner_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_quotes_owner_snapshot", "quotes", type_="foreignkey")
    op.drop_column("quotes", "owner_snapshot_id")
    op.drop_constraint("fk_invoices_owner_snapshot", "invoices", type_="foreignkey")
    op.drop_column("invoices", "owner_snapshot_id")
    op.drop_index(
        "ix_owner_profile_snapshots_content_hash",
        table_name="owner_profile_snapshots",
    )
    op.drop_table("owner_profile_snapshots")
    op.drop_table("owner_profiles")
