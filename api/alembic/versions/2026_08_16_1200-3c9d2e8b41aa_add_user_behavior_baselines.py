"""Per-user behavioural baselines for graduated risk scoring (issue #67)

Revision ID: 3c9d2e8b41aa
Revises: 789f74a8c75b
Create Date: 2026-08-16 12:00:00.000000

One row per user recording what is NORMAL for them: known devices, typical
countries, typical active hours, and typical per-window request volume per
data-sensitivity class. The risk detectors weigh deviation from this
baseline as SOFT signals (low individual weight, escalating only in
combination), which is what keeps a legitimate user logging in from a new
place, on a new device, at night, from ever being hard-locked: soft
evidence alone can reach a step-up challenge but never termination.

Devices and countries enter the baseline exclusively through a passed OTP
step-up (verify_otp absorbs the verifying request's context), so an
attacker without inbox access can never launder their context into the
victim's baseline. Hour/volume statistics learn continuously from scored
requests; they suppress false positives rather than grant trust.

Chained onto 789f74a8c75b (session risk decay), the single Alembic head at
branch time (verified by parsing revision/down_revision, per #55).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "3c9d2e8b41aa"
down_revision: Union[str, None] = "789f74a8c75b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_behavior_baselines",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "known_devices",
            sa.JSON(),
            nullable=False,
            comment="Device fingerprints trusted via a passed OTP step-up",
        ),
        sa.Column(
            "known_countries",
            sa.JSON(),
            nullable=False,
            comment="ISO country codes trusted via a passed OTP step-up",
        ),
        sa.Column(
            "hour_counts",
            sa.JSON(),
            nullable=False,
            comment="Requests observed per tenant-local hour ('0'..'23')",
        ),
        sa.Column(
            "hour_observations",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Total observations behind hour_counts (min-sample gate)",
        ),
        sa.Column(
            "volume_baselines",
            sa.JSON(),
            nullable=False,
            comment=(
                "Per sensitivity class: EWMA of requests per volume window "
                "{class: {ewma, count, windows, window_started_at}}"
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("user_behavior_baselines")
