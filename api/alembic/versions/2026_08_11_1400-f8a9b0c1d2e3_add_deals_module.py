"""Add Sales Desk deals module: deals + deal_activities

Revision ID: f8a9b0c1d2e3
Revises: d5e6f7a8b9c0
Create Date: 2026-08-11 14:00:00.000000

Sales Desk pipeline core (issue #39):

- deals: first-class pipeline entities linked to existing customers, with
  owner (users FK), product/seats/value, USD|KES currency (validated in the
  service against the customer's billing-profile currencies from #43), the
  four open stages (activation → qualification → proposal_quote →
  negotiation), lifecycle status (open | won | lost | parked), parking
  bookkeeping (parked_until + resume_stage), enumerated close reason +
  mandatory close note, and an optimistic-lock version.
- deal_activities: the append-only STAGE RECORD history. Every create,
  advance, close, park and resume writes one row; notes can never be empty
  (DB CHECK backstopping the "A note is required to advance or close."
  service/schema validation). The derived age_days/idle_days values are
  computed from the first/latest record.

CHECK constraints enumerate stages, statuses and close reasons; indexes
cover the pipeline hot paths (owner, customer, status, stage and the
per-deal history read).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CURRENCY_IN_LIST = "'USD', 'KES'"
_STAGE_IN_LIST = "'activation', 'qualification', 'proposal_quote', 'negotiation'"
_STATUS_IN_LIST = "'open', 'won', 'lost', 'parked'"
_CLOSE_REASON_IN_LIST = (
    "'Price & packaging', 'Relationship / trust', 'Product fit', "
    "'Migration & support offer', 'Faster response than competitor', "
    "'Price too high', 'Lost to competitor', 'Bad timing', 'No budget', "
    "'Went direct to Microsoft', 'No decision / went silent'"
)


def upgrade() -> None:
    op.create_table(
        "deals",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            UUID(as_uuid=True),
            nullable=False,
            comment="Customer (company) this deal belongs to",
        ),
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            nullable=False,
            comment="Sales rep who owns this deal",
        ),
        sa.Column(
            "product",
            sa.String(length=255),
            nullable=False,
            comment="Product being sold (e.g. Microsoft 365 Business Premium)",
        ),
        sa.Column(
            "seats",
            sa.Integer(),
            nullable=False,
            comment="Number of seats/licences",
        ),
        sa.Column(
            "value",
            sa.Numeric(precision=15, scale=2),
            nullable=False,
            comment="Annual deal value (ARR) in the deal currency",
        ),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            comment=(
                "Deal currency (USD|KES) — must match one of the customer's "
                "billing-profile currencies"
            ),
        ),
        sa.Column(
            "stage",
            sa.String(length=20),
            server_default=sa.text("'activation'"),
            nullable=False,
            comment="Current (or last open) pipeline stage",
        ),
        sa.Column(
            "status",
            sa.String(length=10),
            server_default=sa.text("'open'"),
            nullable=False,
            comment="Deal lifecycle status: open | won | lost | parked",
        ),
        sa.Column(
            "parked_until",
            sa.Date(),
            nullable=True,
            comment="Re-engage date while the deal sits in the future pipeline",
        ),
        sa.Column(
            "resume_stage",
            sa.String(length=20),
            nullable=True,
            comment="Stage the deal returns to when re-engaged from parking",
        ),
        sa.Column(
            "close_reason",
            sa.String(length=50),
            nullable=True,
            comment="Enumerated main reason the deal was won/lost",
        ),
        sa.Column(
            "close_note",
            sa.Text(),
            nullable=True,
            comment="Mandatory free-text note recorded at close",
        ),
        sa.Column(
            "closed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the deal was closed (won or lost)",
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            nullable=True,
            comment="User who created this deal",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Optimistic-locking version counter",
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
            ["customer_id"],
            ["customers.id"],
            name="fk_deals_customer_id_customers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_deals_owner_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deals"),
        sa.CheckConstraint(
            f"currency IN ({_CURRENCY_IN_LIST})",
            name="ck_deals_valid_currency",
        ),
        sa.CheckConstraint(
            f"stage IN ({_STAGE_IN_LIST})",
            name="ck_deals_valid_stage",
        ),
        sa.CheckConstraint(
            f"status IN ({_STATUS_IN_LIST})",
            name="ck_deals_valid_status",
        ),
        sa.CheckConstraint(
            f"resume_stage IS NULL OR resume_stage IN ({_STAGE_IN_LIST})",
            name="ck_deals_valid_resume_stage",
        ),
        sa.CheckConstraint(
            f"close_reason IS NULL OR close_reason IN ({_CLOSE_REASON_IN_LIST})",
            name="ck_deals_valid_close_reason",
        ),
        sa.CheckConstraint("seats > 0", name="ck_deals_seats_positive"),
        sa.CheckConstraint("value >= 0", name="ck_deals_value_non_negative"),
        sa.CheckConstraint(
            "status != 'parked' OR "
            "(parked_until IS NOT NULL AND resume_stage IS NOT NULL)",
            name="ck_deals_parked_fields_present",
        ),
        sa.CheckConstraint(
            "status = 'parked' OR (parked_until IS NULL AND resume_stage IS NULL)",
            name="ck_deals_parked_fields_absent",
        ),
        sa.CheckConstraint(
            "status NOT IN ('won', 'lost') OR "
            "(close_reason IS NOT NULL AND close_note IS NOT NULL "
            "AND closed_at IS NOT NULL)",
            name="ck_deals_closed_fields_present",
        ),
        sa.CheckConstraint(
            "status IN ('won', 'lost') OR "
            "(close_reason IS NULL AND close_note IS NULL AND closed_at IS NULL)",
            name="ck_deals_closed_fields_absent",
        ),
    )
    op.create_index("ix_deals_customer_id", "deals", ["customer_id"])
    op.create_index("ix_deals_owner_id", "deals", ["owner_id"])
    op.create_index("ix_deals_status", "deals", ["status"])
    op.create_index("ix_deals_stage", "deals", ["stage"])
    op.create_index("ix_deals_status_stage", "deals", ["status", "stage"])
    op.create_index("ix_deals_owner_status", "deals", ["owner_id", "status"])
    op.create_index("ix_deals_customer_status", "deals", ["customer_id", "status"])

    op.create_table(
        "deal_activities",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("deal_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "stage_label",
            sa.String(length=50),
            nullable=False,
            comment="Display label of the record (stage name or lifecycle event)",
        ),
        sa.Column(
            "note",
            sa.Text(),
            nullable=False,
            comment="Mandatory note — every stage keeps a record",
        ),
        sa.Column(
            "activity_date",
            sa.Date(),
            nullable=False,
            comment="Organization-local date of the record (drives age/idle)",
        ),
        sa.Column(
            "author_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="User who wrote the record (null = system)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["deal_id"],
            ["deals.id"],
            name="fk_deal_activities_deal_id_deals",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name="fk_deal_activities_author_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deal_activities"),
        sa.CheckConstraint(
            "length(trim(note)) > 0",
            name="ck_deal_activities_note_not_empty",
        ),
    )
    op.create_index("ix_deal_activities_deal_id", "deal_activities", ["deal_id"])
    op.create_index(
        "ix_deal_activities_deal_date",
        "deal_activities",
        ["deal_id", "activity_date", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_deal_activities_deal_date", table_name="deal_activities")
    op.drop_index("ix_deal_activities_deal_id", table_name="deal_activities")
    op.drop_table("deal_activities")
    op.drop_index("ix_deals_customer_status", table_name="deals")
    op.drop_index("ix_deals_owner_status", table_name="deals")
    op.drop_index("ix_deals_status_stage", table_name="deals")
    op.drop_index("ix_deals_stage", table_name="deals")
    op.drop_index("ix_deals_status", table_name="deals")
    op.drop_index("ix_deals_owner_id", table_name="deals")
    op.drop_index("ix_deals_customer_id", table_name="deals")
    op.drop_table("deals")
