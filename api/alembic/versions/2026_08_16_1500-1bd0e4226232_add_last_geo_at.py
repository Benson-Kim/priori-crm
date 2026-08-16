"""Anchor impossible travel on the previous geo fix (issue #67 review M2)

Revision ID: 1bd0e4226232
Revises: 3c9d2e8b41aa
Create Date: 2026-08-16 15:00:00.000000

``last_lat``/``last_lon`` update only on geolocated requests, but the
impossible-travel detector measured elapsed time from ``last_seen_at``,
which every request updates. With intermittent geo coverage (an edge
lookup fails, some requests miss the CDN path) the elapsed time is
underestimated, so the implied speed is OVERestimated — a pure
false-positive generator (+70 = instant challenge) for a user who
genuinely travelled while sending non-geolocated requests.

``last_geo_at`` records when the stored coordinates were captured, so
speed is computed between the two geo fixes it actually connects.
Backfilled from ``last_seen_at`` for rows that already hold coordinates
(the best available anchor; at worst it reproduces the old behaviour for
sessions that expire within 24h anyway).

Chained onto 3c9d2e8b41aa (user_behavior_baselines), the single Alembic
head at branch time (verified by parsing revision/down_revision, per #55).
Downgrade drops the column; the detector then has no anchor, which is the
fail-safe direction (no signal, never a penalty).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1bd0e4226232"
down_revision: Union[str, None] = "3c9d2e8b41aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column(
            "last_geo_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When last_lat/last_lon were captured (impossible-travel anchor)",
        ),
    )
    op.execute(
        "UPDATE user_sessions SET last_geo_at = last_seen_at "
        "WHERE last_lat IS NOT NULL AND last_seen_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("user_sessions", "last_geo_at")
