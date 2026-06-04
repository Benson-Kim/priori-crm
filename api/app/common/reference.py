"""
Deep module for generating unique, sequential reference numbers.

Hides: advisory lock acquisition, count/max query, prefix formatting,
and zero-padded numbering behind a single generate() method.
"""
from datetime import date

from sqlalchemy import Integer, func, text
from sqlalchemy.orm import InstrumentedAttribute, Session


class ReferenceGenerator:
    """Generate unique, sequential reference numbers with advisory-lock protection."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def _advisory_lock(self, key: str) -> None:
        """Transaction-scoped PostgreSQL advisory lock."""
        self._db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": key},
        )

    def generate(
        self,
        *,
        model: type,
        column: InstrumentedAttribute,
        prefix: str,
        lock_key: str,
        width: int = 3,
        use_date_scope: bool = True,
        use_max_strategy: bool = True,
        strip_prefix_len: int | None = None,
    ) -> str:
        """
        Generate the next sequential reference number.

        Args:
            model: ORM model class (e.g. Invoice, Expense).
            column: The column holding the reference string.
            prefix: Short prefix (e.g. "INV", "QTE", "EXP").
            lock_key: Advisory lock key for serialisation.
            width: Zero-pad width for the numeric suffix.
            use_date_scope: If True, produces PREFIX-YYYYMMDD-NNN format.
                            If False, produces PREFIX-NNNN format.
            use_max_strategy: Default True. Uses MAX(numeric_suffix)+1, which
                              never reuses a number after the latest record is
                              deleted. Set False only to fall back to the
                              legacy COUNT(*) strategy (P-4).
            strip_prefix_len: Number of leading characters to strip when
                              extracting the numeric suffix for MAX strategy.
                              Optional: when omitted it is derived from the
                              full prefix (PREFIX-YYYYMMDD- or PREFIX-).

        Returns:
            The next reference string (e.g. "INV-20260527-003").
        """
        if use_date_scope:
            today = date.today()
            full_prefix = f"{prefix}-{today.strftime('%Y%m%d')}"
            self._advisory_lock(f"{lock_key}_{full_prefix}")

            if use_max_strategy:
                # Numeric suffix follows "PREFIX-YYYYMMDD-".
                offset = strip_prefix_len if strip_prefix_len is not None else len(full_prefix) + 1
                max_suffix = (
                    self._db.query(
                        func.max(
                            func.cast(func.substring(column, offset + 1), Integer)
                        )
                    )
                    .filter(column.like(f"{full_prefix}%"))
                    .scalar()
                ) or 0
                return f"{full_prefix}-{max_suffix + 1:0{width}d}"

            # Legacy COUNT strategy (opt-out only).
            count = (
                self._db.query(func.count(model.id))
                .filter(column.like(f"{full_prefix}%"))
                .scalar()
            )
            return f"{full_prefix}-{count + 1:0{width}d}"

        # Global (non-date-scoped) reference
        self._advisory_lock(lock_key)

        if use_max_strategy:
            # Numeric suffix follows "PREFIX-".
            offset = strip_prefix_len if strip_prefix_len is not None else len(prefix) + 1
            max_suffix = (
                self._db.query(
                    func.max(
                        func.cast(
                            func.substring(column, offset + 1),
                            Integer,
                        )
                    )
                ).scalar()
            ) or 0
            return f"{prefix}-{max_suffix + 1:0{width}d}"

        # Legacy COUNT strategy (opt-out only).
        count = self._db.query(func.count(model.id)).scalar()
        return f"{prefix}-{count + 1:0{width}d}"
