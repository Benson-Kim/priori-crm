"""Add Sales Desk onboarding module: onboardings + onboarding_tasks

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-12 12:00:00.000000

Sales Desk onboarding (issue #41):

- onboardings: one delivery checklist per WON deal (unique deal FK +
  customer FK), created automatically inside the same transaction as the
  close-won flow. ``plan_label`` is the display line captured from the
  deal at creation ("{product} · {seats} seats"); ``template_version``
  snapshots the owner template version the tasks were copied from, so a
  later template edit never mutates existing checklists (template
  versioning). Percent complete and the "N of 7" counts are computed
  server-side and never stored.
- onboarding_tasks: the ordered steps ({position, label, done,
  completed_at}), copied verbatim from the org's task template at create
  time. A CHECK keeps done/completed_at consistent.
- owner_profiles: the configurable onboarding task template (NULL = the
  built-in 7-task default seeded from Onboarding.svg) and its monotonic
  version counter.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Owner settings: configurable onboarding task template (issue #41).
    # NULL template = built-in DEFAULT_ONBOARDING_TASKS, so existing rows
    # need no backfill; version starts at 1 for every org.
    op.add_column(
        "owner_profiles",
        sa.Column(
            "onboarding_task_template",
            sa.JSON(),
            nullable=True,
            comment=(
                "Org onboarding checklist template (ordered task labels); "
                "NULL = built-in 7-task default"
            ),
        ),
    )
    op.add_column(
        "owner_profiles",
        sa.Column(
            "onboarding_template_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Monotonic version of the onboarding task template",
        ),
    )

    op.create_table(
        "onboardings",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "deal_id",
            UUID(as_uuid=True),
            nullable=False,
            comment="Won deal this checklist was created from",
        ),
        sa.Column(
            "customer_id",
            UUID(as_uuid=True),
            nullable=False,
            comment="Customer (company) being onboarded",
        ),
        sa.Column(
            "plan_label",
            sa.String(length=300),
            nullable=False,
            comment=(
                "Display plan line captured from the deal at creation: "
                "'{product} · {seats} seats'"
            ),
        ),
        sa.Column(
            "template_version",
            sa.Integer(),
            nullable=False,
            comment=(
                "Owner onboarding-template version this checklist was created "
                "from (template versioning: later edits never mutate it)"
            ),
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When every task was done (cleared if a task is re-opened)",
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
            ["deal_id"],
            ["deals.id"],
            name="fk_onboardings_deal_id_deals",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_onboardings_customer_id_customers",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_onboardings"),
        sa.UniqueConstraint("deal_id", name="uq_onboardings_deal_id"),
        sa.CheckConstraint(
            "template_version > 0",
            name="ck_onboardings_template_version_positive",
        ),
    )
    op.create_index("ix_onboardings_deal_id", "onboardings", ["deal_id"])
    op.create_index("ix_onboardings_customer_id", "onboardings", ["customer_id"])
    op.create_index(
        "ix_onboardings_customer_created",
        "onboardings",
        ["customer_id", "created_at"],
    )

    op.create_table(
        "onboarding_tasks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("onboarding_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            comment="1-based step order ('Step N' in the design)",
        ),
        sa.Column(
            "label",
            sa.String(length=255),
            nullable=False,
            comment="Task label copied from the template at creation",
        ),
        sa.Column(
            "done",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="Whether this step is complete",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the step was completed (null while not done)",
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
            ["onboarding_id"],
            ["onboardings.id"],
            name="fk_onboarding_tasks_onboarding_id_onboardings",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_onboarding_tasks"),
        sa.UniqueConstraint(
            "onboarding_id", "position", name="uq_onboarding_tasks_position"
        ),
        sa.CheckConstraint(
            "position > 0", name="ck_onboarding_tasks_position_positive"
        ),
        sa.CheckConstraint(
            "length(trim(label)) > 0",
            name="ck_onboarding_tasks_label_not_empty",
        ),
        sa.CheckConstraint(
            "(done = true AND completed_at IS NOT NULL) OR "
            "(done = false AND completed_at IS NULL)",
            name="ck_onboarding_tasks_done_completed_at_consistent",
        ),
    )
    op.create_index(
        "ix_onboarding_tasks_onboarding_id", "onboarding_tasks", ["onboarding_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_onboarding_tasks_onboarding_id", table_name="onboarding_tasks")
    op.drop_table("onboarding_tasks")
    op.drop_index("ix_onboardings_customer_created", table_name="onboardings")
    op.drop_index("ix_onboardings_customer_id", table_name="onboardings")
    op.drop_index("ix_onboardings_deal_id", table_name="onboardings")
    op.drop_table("onboardings")
    op.drop_column("owner_profiles", "onboarding_template_version")
    op.drop_column("owner_profiles", "onboarding_task_template")
