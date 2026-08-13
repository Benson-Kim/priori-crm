"""Per-owner module entitlements: owner_module_settings table

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-08-13 10:00:00.000000

Per-owner module entitlements / feature toggles (session 6341101 restore):

- owner_module_settings: one row per (owner_profile, module_key) override.
  A MISSING row means the module is ENABLED (default-on), so no seeding
  is needed and a fresh install exposes every module.
- unique (owner_profile_id, module_key) so a module has at most one
  override per owner; FK cascades with the owner profile.

Chained onto a8b9c0d1e2f3 (quotes<->deals link), the single Alembic head
at branch time (verified by parsing revision/down_revision, per #55).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "owner_module_settings",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("owner_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "module_key",
            sa.String(30),
            nullable=False,
            comment="ModuleKey value this row overrides (missing row = enabled)",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_by",
            UUID(as_uuid=True),
            nullable=True,
            comment="Admin user who last changed this toggle (null = system)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "owner_profile_id",
            "module_key",
            name="uq_owner_module_settings_profile_module",
        ),
    )
    op.create_index(
        "ix_owner_module_settings_owner_profile_id",
        "owner_module_settings",
        ["owner_profile_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_owner_module_settings_owner_profile_id",
        table_name="owner_module_settings",
    )
    op.drop_table("owner_module_settings")
