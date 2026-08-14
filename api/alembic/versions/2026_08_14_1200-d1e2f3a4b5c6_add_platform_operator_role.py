"""Allow the platform_operator user role

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-08-14 12:00:00.000000

Issue #58 / ADR-0011: module entitlements become operator-granted, so a
platform-level role above ADMIN must be storable. Widens the
``ck_users_valid_role`` CHECK constraint to accept ``platform_operator``
alongside the existing tenant roles. The column itself (String(20)) already
fits the 17-character value; no data backfill is needed.

Chained onto c0d1e2f3a4b5 (owner_module_settings), the single Alembic head
on develop (verified by parsing revision/down_revision, per #55).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_users_valid_role", "users", type_="check")
    op.create_check_constraint(
        "ck_users_valid_role",
        "users",
        "role IN ('platform_operator', 'admin', 'manager', 'member')",
    )


def downgrade() -> None:
    # Demote any operator accounts before restoring the narrower constraint,
    # so the downgrade never fails on existing rows.
    op.execute("UPDATE users SET role = 'admin' WHERE role = 'platform_operator'")
    op.drop_constraint("ck_users_valid_role", "users", type_="check")
    op.create_check_constraint(
        "ck_users_valid_role",
        "users",
        "role IN ('admin', 'manager', 'member')",
    )
