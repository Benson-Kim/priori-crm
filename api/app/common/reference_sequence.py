"""Persistent high-water-mark store for reference numbering (P-4 / V-DI-5).

A live ``MAX(numeric_suffix) + 1`` over a documents table reuses a number when
the most-recent row is hard-deleted (the MAX drops back). For a financial
ledger a reference must never be reused. ``reference_sequences`` records, per
scope, the highest suffix ever issued and is only ever incremented, so the
generator can take ``max(table_max, high_water_mark) + 1``.

The scope key encodes the numbering namespace:
- date-scoped: ``"<lock_key>_<PREFIX-YYYYMMDD>"`` (a counter per prefix per day)
- global:      ``"<lock_key>"`` (a single running counter)

This mirrors the advisory-lock key already used to serialise generation, so a
sequence row maps 1:1 to a lock scope.
"""

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.common.database import Base


class ReferenceSequence(Base):
    """Monotonic, never-decremented counter for a reference scope."""

    __tablename__ = "reference_sequences"

    scope_key: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
        comment="Numbering scope (advisory lock key + optional date prefix)",
    )

    last_value: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        comment="Highest numeric suffix ever issued for this scope",
    )
