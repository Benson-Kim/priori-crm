"""Add owner_profiles.status (tenant lifecycle: active | suspended)

Revision ID: f3a4b5c6d7e8
Revises: d1e2f3a4b5c6
Create Date: 2026-08-18 12:00:00.000000

Issue #71 / ADR-0014 Phase A: tenant lifecycle state on the owner profile.
``status`` is org state, never a role mutation (QA finding 09): suspension
is set by the platform operator via the audited
``PATCH /platform/owners/{owner_id}/status`` and enforced at the module
gate and at token issuance. Default ``'active'`` (server default, so
existing rows need no backfill); values constrained to
``('active', 'suspended')`` by CHECK.

Chained onto d1e2f3a4b5c6 (add_platform_operator_role), the single head at
the time of writing (verified with ``alembic heads``).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "owner_profiles",
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'active'"),
            comment="Tenant lifecycle state: active | suspended (operator-set)",
        ),
    )
    op.create_check_constraint(
        "ck_owner_profiles_valid_status",
        "owner_profiles",
        "status IN ('active', 'suspended')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_owner_profiles_valid_status", "owner_profiles", type_="check"
    )
    op.drop_column("owner_profiles", "status")
