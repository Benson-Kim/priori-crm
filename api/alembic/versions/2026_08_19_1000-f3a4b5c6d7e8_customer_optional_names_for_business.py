"""customer optional names for business customers

Makes first_name and last_name nullable so a business customer can be saved
without contact-person names, matching how company_name is already optional
and required only for business customers
(ck_customers_business_requires_company_name). Adds the mirror-image
constraint: an individual customer must still supply both names.

Revision ID: f3a4b5c6d7e8
Revises: d1e2f3a4b5c6
Create Date: 2026-08-19 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "customers",
        "first_name",
        existing_type=sa.VARCHAR(length=100),
        nullable=True,
        existing_comment="Contact person first name",
    )
    op.alter_column(
        "customers",
        "last_name",
        existing_type=sa.VARCHAR(length=100),
        nullable=True,
        existing_comment="Contact person last name",
    )
    op.create_check_constraint(
        "ck_customers_individual_requires_name",
        "customers",
        "(customer_type = 'individual' AND first_name IS NOT NULL AND last_name IS NOT NULL) "
        "OR (customer_type = 'business')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_customers_individual_requires_name", "customers", type_="check"
    )

    # Restoring NOT NULL will fail if any business customer was saved without
    # a name while this revision was applied. That is deliberate: the
    # alternative is inventing contact names to satisfy the constraint.
    # Backfill those rows before downgrading.
    op.alter_column(
        "customers",
        "last_name",
        existing_type=sa.VARCHAR(length=100),
        nullable=False,
        existing_comment="Contact person last name",
    )
    op.alter_column(
        "customers",
        "first_name",
        existing_type=sa.VARCHAR(length=100),
        nullable=False,
        existing_comment="Contact person first name",
    )
