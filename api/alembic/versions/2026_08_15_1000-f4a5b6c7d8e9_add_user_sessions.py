"""Continuous session risk scoring: user_sessions table (issue #67)

Revision ID: f4a5b6c7d8e9
Revises: d1e2f3a4b5c6
Create Date: 2026-08-15 10:00:00.000000

One row per authenticated session, minted by verify_otp (its id travels as
the ``sid`` claim in both JWTs) and re-scored on every request by the
zero-trust gate:

- risk_score accumulates behavioural anomalies (impossible travel, data
  access volume, device change, privilege-escalation attempts);
- status moves active -> challenge_required (step-up via the existing
  login -> OTP flow) or -> terminated; non-active states are never cleared
  in place — re-authentication mints a NEW session row.

Chained onto d1e2f3a4b5c6 (platform operator role), the single Alembic
head at branch time (verified by parsing revision/down_revision, per #55).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "risk_score",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "device_fingerprint",
            sa.String(160),
            nullable=True,
            comment="Last presented device fingerprint (client: or derived: form)",
        ),
        sa.Column("last_ip", sa.String(64), nullable=True),
        sa.Column("last_country", sa.String(2), nullable=True),
        sa.Column("last_lat", sa.Float(), nullable=True),
        sa.Column("last_lon", sa.Float(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "window_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Start of the current data-access volume window",
        ),
        sa.Column(
            "window_request_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "escalation_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Privilege-escalation attempts (RBAC 403s) on this session",
        ),
        sa.Column("termination_reason", sa.String(60), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'challenge_required', 'terminated')",
            name="ck_user_sessions_valid_status",
        ),
    )
    op.create_index(
        "ix_user_sessions_user_status",
        "user_sessions",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_sessions_user_status", table_name="user_sessions")
    op.drop_table("user_sessions")
