"""Absorbed baseline trust ages out: per-entry verified_at stamps (#67 §4)

Revision ID: a7c31f08d2e4
Revises: b2e7d4a1c9f3
Create Date: 2026-08-18 12:00:00.000000

``user_behavior_baselines.known_devices`` / ``known_countries`` stored
bare strings, which made absorbed trust permanent: an attacker who once
passed an OTP step-up (inbox compromise) stayed whitelisted forever, and
the H13 LRU-touch kept their entry fresh indefinitely. Entries now carry
``{"value": ..., "verified_at": <ISO-8601 UTC>}`` so
``RISK_BASELINE_TRUST_TTL_DAYS`` can age them out of "known".

This is a data migration over the stored JSON:

- upgrade stamps every legacy string entry with the row's ``updated_at``
  (falling back to ``created_at``, then the migration instant) — the best
  available approximation of when that trust was last verified. Already-
  dict entries (partially migrated rows) are left untouched, so the
  migration is idempotent.
- downgrade flattens entries back to bare values (the stamps are lost,
  which only restores the previous — weaker — behaviour).

The application also tolerates unstamped legacy entries at runtime
(treated as fresh until their next verified touch), so this migration is
a hygiene pass, not a correctness prerequisite: rows written between
deploy and migration are handled either way.
"""

from datetime import UTC, datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c31f08d2e4"
down_revision: Union[str, None] = "b2e7d4a1c9f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _baselines_table() -> sa.Table:
    metadata = sa.MetaData()
    return sa.Table(
        "user_behavior_baselines",
        metadata,
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("known_devices", sa.JSON(), nullable=False),
        sa.Column("known_countries", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )


def _stamp_entries(entries: list, stamp: str) -> tuple[list, bool]:
    """Convert legacy string entries to stamped dicts; True when changed."""
    converted: list = []
    changed = False
    for entry in entries or []:
        if isinstance(entry, str):
            converted.append({"value": entry, "verified_at": stamp})
            changed = True
        else:
            converted.append(entry)
    return converted, changed


def _flatten_entries(entries: list) -> tuple[list, bool]:
    """Reduce dict entries back to bare values; True when changed."""
    flattened: list = []
    changed = False
    for entry in entries or []:
        if isinstance(entry, dict):
            value = entry.get("value")
            if isinstance(value, str):
                flattened.append(value)
            changed = True
        else:
            flattened.append(entry)
    return flattened, changed


def upgrade() -> None:
    bind = op.get_bind()
    table = _baselines_table()
    fallback = datetime.now(UTC)
    for row in bind.execute(sa.select(table)).mappings():
        stamp = (row["updated_at"] or row["created_at"] or fallback).isoformat()
        devices, devices_changed = _stamp_entries(row["known_devices"], stamp)
        countries, countries_changed = _stamp_entries(row["known_countries"], stamp)
        if devices_changed or countries_changed:
            bind.execute(
                table.update()
                .where(table.c.user_id == row["user_id"])
                .values(known_devices=devices, known_countries=countries)
            )


def downgrade() -> None:
    bind = op.get_bind()
    table = _baselines_table()
    for row in bind.execute(sa.select(table)).mappings():
        devices, devices_changed = _flatten_entries(row["known_devices"])
        countries, countries_changed = _flatten_entries(row["known_countries"])
        if devices_changed or countries_changed:
            bind.execute(
                table.update()
                .where(table.c.user_id == row["user_id"])
                .values(known_devices=devices, known_countries=countries)
            )
