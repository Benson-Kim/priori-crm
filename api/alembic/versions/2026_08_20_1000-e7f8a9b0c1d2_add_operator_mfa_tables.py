"""Add operator MFA tables (TOTP enrollment + recovery codes)

Revision ID: e7f8a9b0c1d2
Revises: b0c1d2e3f4a5
Create Date: 2026-08-20 10:00:00.000000

Issue #73 / ADR-0014: TOTP second factor for platform_operator accounts.
``operator_mfa_totp`` holds one enrollment per operator (Fernet-encrypted
seed, pending|active status, monotonic replay fence);
``operator_mfa_recovery_codes`` holds the hashed single-use recovery codes.
No plaintext secret is ever persisted. Rows are written only by the
authenticated operator acting on itself — no creation/promotion path
(QA finding 09).

Chained onto b0c1d2e3f4a5 (audit_events_append_only_trigger), the single
head at the time of writing (verified by walking down_revision links).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operator_mfa_totp",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "secret_encrypted",
            sa.String(length=512),
            nullable=False,
            comment="Fernet ciphertext of the base32 TOTP seed (never plaintext)",
        ),
        sa.Column(
            "status",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="pending (unconfirmed) | active (enforced)",
        ),
        sa.Column(
            "last_used_counter",
            sa.BigInteger(),
            nullable=True,
            comment=(
                "Highest accepted TOTP time-step counter (replay fence "
                "shared across login and step-up); NULL until first "
                "acceptance"
            ),
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_operator_mfa_totp_valid_status",
        "operator_mfa_totp",
        "status IN ('pending', 'active')",
    )

    op.create_table(
        "operator_mfa_recovery_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "code_hash",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256 digest of the normalized recovery code",
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_operator_recovery_user_used",
        "operator_mfa_recovery_codes",
        ["user_id", "used_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operator_recovery_user_used", table_name="operator_mfa_recovery_codes"
    )
    op.drop_table("operator_mfa_recovery_codes")
    op.drop_constraint(
        "ck_operator_mfa_totp_valid_status", "operator_mfa_totp", type_="check"
    )
    op.drop_table("operator_mfa_totp")
