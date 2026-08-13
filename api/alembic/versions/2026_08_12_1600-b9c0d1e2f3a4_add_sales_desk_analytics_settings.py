"""Sales Desk analytics settings: stage probabilities + rep quarterly targets

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-08-12 16:00:00.000000

Sales Desk analytics backend (issue #45):

- owner_profiles.deal_stage_probabilities: per-stage win probabilities for
  the weighted pipeline (stage value -> decimal string 0..1). NULL means
  "use the built-in 0.10/0.25/0.50/0.75 defaults"
  (app.constants.settings_defaults.DEFAULT_DEAL_STAGE_PROBABILITIES), so no
  backfill is needed.
- owner_profiles.rep_quarterly_targets: per-rep quarterly won-value targets
  in USD (user id -> decimal string) driving the Dashboard's "Rep pipeline
  vs. quota" panel. Reps without an entry fall back to
  DEFAULT_REP_QUARTERLY_TARGET_USD.

Both are org-scoped settings on the singleton profile (acceptance
criterion: probabilities/targets configurable via settings, not constants).
Chained onto a8b9c0d1e2f3 (#44 quotes link) to keep a single Alembic head.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "owner_profiles",
        sa.Column(
            "deal_stage_probabilities",
            sa.JSON(),
            nullable=True,
            comment=(
                "Per-stage win probabilities for the weighted pipeline "
                "(stage value -> decimal string 0..1); NULL = built-in "
                "0.10/0.25/0.50/0.75 defaults"
            ),
        ),
    )
    op.add_column(
        "owner_profiles",
        sa.Column(
            "rep_quarterly_targets",
            sa.JSON(),
            nullable=True,
            comment=(
                "Per-rep quarterly won-value targets in USD (user id -> "
                "decimal string); reps without an entry fall back to the "
                "built-in default"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("owner_profiles", "rep_quarterly_targets")
    op.drop_column("owner_profiles", "deal_stage_probabilities")
