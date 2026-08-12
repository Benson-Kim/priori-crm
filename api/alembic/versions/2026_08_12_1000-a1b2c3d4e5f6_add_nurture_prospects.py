"""Add Sales Desk future pipeline: nurture_prospects

Revision ID: a1b2c3d4e5f6
Revises: f8a9b0c1d2e3
Create Date: 2026-08-12 10:00:00.000000

Sales Desk future pipeline (issue #40):

- nurture_prospects: companies we plan to engage on a future date but who
  are NOT yet customers. Each row carries an optional owner (users FK), the
  company name, a contact person, a free-text note, the engage-by date
  (``engage_on``) and an estimated annual value (est. ARR). The "Engage"
  action atomically creates a customer + an open deal at stage Activation
  and removes the prospect, so a prospect never coexists with its customer.

The ``due_state`` badges (Overdue / Today / Nd) and the notification counts
(due nurture, upcoming <=14d, due parked) are computed server-side against
the org-local accounting date boundary (common/reporting_time.py); nothing
about "due" is persisted. Indexes cover the due/notification hot path
(engage_on) and the owner rail.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nurture_prospects",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="Sales rep who owns this prospect (null = unassigned)",
        ),
        sa.Column(
            "company_name",
            sa.String(length=255),
            nullable=False,
            comment="Company we plan to engage (not yet a customer)",
        ),
        sa.Column(
            "contact",
            sa.String(length=255),
            nullable=True,
            comment="Contact person at the company",
        ),
        sa.Column(
            "note",
            sa.Text(),
            nullable=True,
            comment="Why/when to engage (rendered verbatim on the list)",
        ),
        sa.Column(
            "engage_on",
            sa.Date(),
            nullable=False,
            comment="Engage-by date driving the due badges and notifications",
        ),
        sa.Column(
            "est_annual_value",
            sa.Numeric(precision=15, scale=2),
            nullable=True,
            comment="Estimated annual value (est. ARR) if we win the company",
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            nullable=True,
            comment="User who created this prospect",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_nurture_prospects_owner_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nurture_prospects"),
        sa.CheckConstraint(
            "length(trim(company_name)) > 0",
            name="ck_nurture_prospects_company_not_empty",
        ),
        sa.CheckConstraint(
            "est_annual_value IS NULL OR est_annual_value >= 0",
            name="ck_nurture_prospects_est_value_non_negative",
        ),
    )
    # Due/notification hot path: engage_on <= today and the <=14d window.
    op.create_index(
        "ix_nurture_prospects_engage_on", "nurture_prospects", ["engage_on"]
    )
    op.create_index("ix_nurture_prospects_owner_id", "nurture_prospects", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_nurture_prospects_owner_id", table_name="nurture_prospects")
    op.drop_index("ix_nurture_prospects_engage_on", table_name="nurture_prospects")
    op.drop_table("nurture_prospects")
