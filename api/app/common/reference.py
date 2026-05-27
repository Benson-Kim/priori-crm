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
        use_max_strategy: bool = False,
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
            use_max_strategy: If True, uses MAX(numeric_suffix) instead of
                              COUNT(*) to prevent reuse after deletions.
            strip_prefix_len: Number of leading characters to strip when
                              extracting the numeric suffix for MAX strategy.
                              Required when use_max_strategy=True.

        Returns:
            The next reference string (e.g. "INV-20260527-003").
        """
        if use_date_scope:
            today = date.today()
            full_prefix = f"{prefix}-{today.strftime('%Y%m%d')}"
            self._advisory_lock(f"{lock_key}_{full_prefix}")

            count = (
                self._db.query(func.count(model.id))
                .filter(column.like(f"{full_prefix}%"))
                .scalar()
            )
            return f"{full_prefix}-{count + 1:0{width}d}"

        # Global (non-date-scoped) reference
        self._advisory_lock(lock_key)

        if use_max_strategy:
            if strip_prefix_len is None:
                raise ValueError("strip_prefix_len is required for MAX strategy")

            max_suffix = (
                self._db.query(
                    func.max(
                        func.cast(
                            func.substring(column, strip_prefix_len + 1),
                            Integer,
                        )
                    )
                ).scalar()
            ) or 0
            return f"{prefix}-{max_suffix + 1:0{width}d}"

        # COUNT strategy (default for global references)
        count = self._db.query(func.count(model.id)).scalar()
        return f"{prefix}-{count + 1:0{width}d}"
