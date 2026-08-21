"""Session risk decay + volume counter move (issue #67 hardening)

Revision ID: 789f74a8c75b
Revises: f4a5b6c7d8e9
Create Date: 2026-08-16 06:00:00.000000

Two changes to ``user_sessions``:

- ``risk_updated_at`` records when ``risk_score`` was last raised, so decay
  (``RISK_DECAY_PER_HOUR``) can be computed on read. Without decay the score
  is a ratchet: benign noise (a browser auto-update, one busy minute, a
  stray 403) accumulates past the challenge threshold on any long-lived
  session and eventually challenges a legitimate user for nothing.

- ``window_started_at`` / ``window_request_count`` are dropped. The
  data-access volume counter moves to the shared RateLimitStore, because a
  Postgres counter is rolled back along with any failing request — an
  attacker probing endpoints that error could reset their own window for
  free, which is exactly the signal the counter exists to catch.

Chained onto f4a5b6c7d8e9 (the user_sessions table) rather than editing it
in place: that revision is already pushed, so amending it would strand
anyone who has run it. Verified as the single head at branch time (#55).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "789f74a8c75b"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column(
            "risk_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When risk_score was last raised; decay accrues from here",
        ),
    )
    # Existing rows: anchor decay at the session's creation so a score
    # carried over from before this migration starts shedding immediately
    # rather than reading as "just now" and staying at full weight.
    op.execute(
        "UPDATE user_sessions SET risk_updated_at = created_at "
        "WHERE risk_score > 0 AND risk_updated_at IS NULL"
    )

    op.drop_column("user_sessions", "window_started_at")
    op.drop_column("user_sessions", "window_request_count")


def downgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column(
            "window_request_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "user_sessions",
        sa.Column(
            "window_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Start of the current data-access volume window",
        ),
    )
    op.drop_column("user_sessions", "risk_updated_at")
