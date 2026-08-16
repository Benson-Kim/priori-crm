"""Session-start soft signals stop decaying: risk floor (issue #67, H10)

Revision ID: b2e7d4a1c9f3
Revises: 1bd0e4226232
Create Date: 2026-08-16 18:00:00.000000

Score decay exists to forgive TRANSIENT noise (a browser update, one busy
minute), but the session-start soft signals are not transient: a session
that began on an unknown device in an unknown country does not stop
having begun there. Decaying them let an attacker pace anomalies —
wait out the decay after the session-start batch, then trigger the next
signal from a clean score — accumulating below the challenge threshold
forever.

``risk_floor`` records the sum of the session-start soft points; the
effective score never decays below it for the session's lifetime. It can
only rise (set once, on the first scored request) and dies with the
session — re-authentication mints a fresh session whose context was
absorbed by the OTP step-up, so the floor for a legitimate user's next
session is zero.

Chained onto 1bd0e4226232 (last_geo_at), the single Alembic head of this
branch at the time of writing (verified by parsing revision /
down_revision). Downgrade drops the column: scores then decay as before,
which only weakens (never breaks) detection — the fail-safe direction.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2e7d4a1c9f3"
down_revision: Union[str, None] = "1bd0e4226232"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column(
            "risk_floor",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Non-decaying score floor from session-start soft signals",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_sessions", "risk_floor")
