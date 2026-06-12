"""widen otp_codes.code to hold a SHA-256 digest

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-11 16:00:00.000000

OTP codes were stored in plaintext (ISSUE-023): any database read — a
backup, a dump, an injection — yielded live login codes. The service now
stores a SHA-256 hex digest, which needs 64 characters. Existing plaintext
rows simply fail the digest comparison and expire within their 5-minute
window, so no data migration is required.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "otp_codes",
        "code",
        existing_type=sa.String(length=6),
        type_=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "otp_codes",
        "code",
        existing_type=sa.String(length=64),
        type_=sa.String(length=6),
        existing_nullable=False,
    )
