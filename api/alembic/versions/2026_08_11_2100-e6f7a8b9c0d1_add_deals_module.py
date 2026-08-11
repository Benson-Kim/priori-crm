"""Add Sales Desk deals + deal_activities tables

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-08-11 21:00:00.000000

Sales Desk deals module (issue #39):

- deals: FK customer (RESTRICT — deals are history), owner (users, SET
  NULL), product/seats/value, currency reusing !38's billing-currency pair
  (USD|KES CHECK — the deal value currency follows the customer's
  primary_currency), the four ordered pipeline stages, open|won|lost|parked
  status, parked_until + resume_stage for the future pipeline, and the
  enumerated close reason + note. Lifecycle invariants are enforced in the
  DB too: close_reason iff closed, parked_until iff parked.
- deal_activities: the append-only stage record (display label, mandatory
  non-blank note, organisation-local date, author). The first/last activity
  dates drive the derived age/idle metrics, which are computed at read time
  and never stored.

Indexes on owner, customer, status, stage (plus status+stage composite and
the timeline's (deal_id, activity_date)) per the issue's acceptance
criteria.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STAGES = ("activation", "qualification", "proposal_quote", "negotiation")
_STATUSES = ("open", "won", "lost", "parked")
_WON_REASONS = (
    "Price & packaging",
    "Relationship / trust",
    "Product fit",
    "Migration & support offer",
    "Faster response than competitor",
)
_LOST_REASONS = (
    "Price too high",
    "Lost to competitor",
    "Bad timing",
    "No budget",
    "Went direct to Microsoft",
    "No decision / went silent",
)

_STAGE_IN_LIST = ", ".join(f"'{s}'" for s in _STAGES)
_STATUS_IN_LIST = ", ".join(f"'{s}'" for s in _STATUSES)
_CLOSE_REASON_IN_LIST = ", ".join(f"'{r}'" for r in (*_WON_REASONS, *_LOST_REASONS))


def upgrade() -> None:
    op.create_table(
        "deals",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "customer_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "customers.id",
                ondelete="RESTRICT",
                name="fk_deals_customer_id_customers",
            ),
            nullable=False,
            comment="Customer (company) this deal belongs to",
        ),
        sa.Column(
            "owner_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_deals_owner_id_users"
            ),
            nullable=True,
            comment="Sales rep who owns this deal (null after user deletion)",
        ),
        sa.Column(
            "product",
            sa.String(length=100),
            nullable=False,
            comment="Product / plan being sold (e.g. 'Microsoft 365 E5')",
        ),
        sa.Column(
            "seats",
            sa.Integer(),
            nullable=False,
            comment="Number of seats/licences",
        ),
        sa.Column(
            "value",
            sa.Numeric(15, 2),
            nullable=False,
            comment="Annual deal value in the deal's own currency (no floats)",
        ),
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            comment=(
                "Deal value currency — follows the customer's primary_currency "
                "billing profile (USD|KES); EUR/GBP are display-only elsewhere"
            ),
        ),
        sa.Column(
            "stage",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'activation'"),
            comment="Current pipeline stage (only meaningful while open)",
        ),
        sa.Column(
            "status",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("'open'"),
            comment="Lifecycle status: open | won | lost | parked",
        ),
        sa.Column(
            "parked_until",
            sa.Date(),
            nullable=True,
            comment="Re-engage date while parked in the future pipeline",
        ),
        sa.Column(
            "resume_stage",
            sa.String(length=20),
            nullable=True,
            comment="Stage the deal returns to when resumed from parked",
        ),
        sa.Column(
            "close_reason",
            sa.String(length=50),
            nullable=True,
            comment="Enumerated won/lost reason (prototype lists); closed only",
        ),
        sa.Column(
            "close_note",
            sa.Text(),
            nullable=True,
            comment="Free-text note recorded when the deal was closed",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
            comment="Optimistic-locking version counter",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="Record creation timestamp",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="Record last update timestamp",
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
            "currency IN ('USD', 'KES')",
            name="ck_deals_valid_currency",
        ),
        sa.CheckConstraint(
            "seats > 0",
            name="ck_deals_seats_positive",
        ),
        sa.CheckConstraint(
            "value >= 0",
            name="ck_deals_value_non_negative",
        ),
        sa.CheckConstraint(
            f"resume_stage IS NULL OR resume_stage IN ({_STAGE_IN_LIST})",
            name="ck_deals_valid_resume_stage",
        ),
        sa.CheckConstraint(
            f"close_reason IS NULL OR close_reason IN ({_CLOSE_REASON_IN_LIST})",
            name="ck_deals_valid_close_reason",
        ),
        sa.CheckConstraint(
            "(status IN ('won', 'lost')) = (close_reason IS NOT NULL)",
            name="ck_deals_close_reason_iff_closed",
        ),
        sa.CheckConstraint(
            "(status = 'parked') = (parked_until IS NOT NULL)",
            name="ck_deals_parked_until_iff_parked",
        ),
    )
    op.create_index("ix_deals_customer_id", "deals", ["customer_id"])
    op.create_index("ix_deals_owner_id", "deals", ["owner_id"])
    op.create_index("ix_deals_status", "deals", ["status"])
    op.create_index("ix_deals_stage", "deals", ["stage"])
    op.create_index("ix_deals_status_stage", "deals", ["status", "stage"])

    op.create_table(
        "deal_activities",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "deal_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "deals.id",
                ondelete="CASCADE",
                name="fk_deal_activities_deal_id_deals",
            ),
            nullable=False,
            comment="Deal this record belongs to",
        ),
        sa.Column(
            "stage_label",
            sa.String(length=50),
            nullable=False,
            comment="Display label of the stage/event at the time of the record",
        ),
        sa.Column(
            "note",
            sa.Text(),
            nullable=False,
            comment="Mandatory non-blank record note",
        ),
        sa.Column(
            "activity_date",
            sa.Date(),
            nullable=False,
            comment="Organisation-local date of the record (drives age/idle)",
        ),
        sa.Column(
            "author_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_deal_activities_author_id_users",
            ),
            nullable=True,
            comment="User who wrote the record (null = system or deleted user)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="Exact creation instant (orders same-day records)",
        ),
        sa.CheckConstraint(
            "TRIM(note) <> ''",
            name="ck_deal_activities_note_not_blank",
        ),
    )
    op.create_index("ix_deal_activities_deal_id", "deal_activities", ["deal_id"])
    op.create_index(
        "ix_deal_activities_deal_date",
        "deal_activities",
        ["deal_id", "activity_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_deal_activities_deal_date", table_name="deal_activities")
    op.drop_index("ix_deal_activities_deal_id", table_name="deal_activities")
    op.drop_table("deal_activities")
    op.drop_index("ix_deals_status_stage", table_name="deals")
    op.drop_index("ix_deals_stage", table_name="deals")
    op.drop_index("ix_deals_status", table_name="deals")
    op.drop_index("ix_deals_owner_id", table_name="deals")
    op.drop_index("ix_deals_customer_id", table_name="deals")
    op.drop_table("deals")
