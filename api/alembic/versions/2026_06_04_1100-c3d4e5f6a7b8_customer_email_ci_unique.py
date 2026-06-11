"""case-insensitive unique customer email

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-04 11:00:00.000000

Normalizes existing customer emails to lowercase, then adds a unique
functional index on lower(email) so addresses differing only in case
cannot coexist.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Normalize existing data first so the functional unique index can build.
    op.execute("UPDATE customers SET email = lower(email)")
    op.execute(
        "CREATE UNIQUE INDEX uq_customers_email_lower ON customers (lower(email))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_customers_email_lower")
