"""add OTP hot-path composite index (W3.7 / AUTH-DBA-1)

Revision ID: e5f6a7b8c9d0
Revises: c3d4e5f6a7b8
Create Date: 2026-06-04 12:00:00.000000

Adds a composite index on otp_codes(user_id, is_used, created_at) to back
the verify_otp lookup and _invalidate_pending_otps update, which currently
scan a user's OTP history on every login/verify.

Chained after the search migration (d4e5f6a7b8c9) to keep a single linear
history now that both have merged.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_otp_user_unused_created",
        "otp_codes",
        ["user_id", "is_used", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_otp_user_unused_created", table_name="otp_codes")
