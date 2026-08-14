"""customer optional contact fields and drop province

Makes the customer email and phone columns nullable and removes the
province column entirely.

postal_code and website are not touched: both were already nullable (the
former since 25134e7109ca), so the "four optional fields" contract needs no
DDL for them.

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-08-14 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # email keeps its unique index. Postgres treats NULLs as distinct, so
    # dropping NOT NULL lets any number of customers exist without an email
    # while supplied addresses stay unique.
    op.alter_column(
        "customers",
        "email",
        existing_type=sa.VARCHAR(length=255),
        nullable=True,
        existing_comment="Primary email address",
    )
    op.alter_column(
        "customers",
        "phone",
        existing_type=sa.VARCHAR(length=20),
        nullable=True,
        existing_comment="Primary phone number",
    )

    # province carries no index and no foreign key, so the column drops
    # cleanly. Any values held there are discarded by design - the concept is
    # being removed, not relocated.
    op.drop_column("customers", "province")


def downgrade() -> None:
    op.add_column(
        "customers",
        sa.Column(
            "province",
            sa.VARCHAR(length=100),
            nullable=True,
            comment="State/Province/Region",
        ),
    )

    # Restoring NOT NULL will fail if any customer was saved without an email
    # or phone while this revision was applied. That is deliberate: the
    # alternative is inventing contact details to satisfy the constraint.
    # Backfill those rows before downgrading.
    op.alter_column(
        "customers",
        "phone",
        existing_type=sa.VARCHAR(length=20),
        nullable=False,
        existing_comment="Primary phone number",
    )
    op.alter_column(
        "customers",
        "email",
        existing_type=sa.VARCHAR(length=255),
        nullable=False,
        existing_comment="Primary email address",
    )
