"""
Tests for the ReferenceGenerator deep module in common/reference.py.

RED→GREEN cycle: generate() with date-scoped and non-date-scoped prefixes.
"""
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Column, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID

from app.common.reference import ReferenceGenerator


# Helpers

class _FakeModel:
    """Minimal stand-in for an ORM model with a reference column."""
    __tablename__ = "fakes"
    id = Column(UUID(as_uuid=True), primary_key=True)
    ref = Column(String(50))


# Tests

class TestReferenceGeneratorDateScoped:
    """Behaviors for date-scoped reference generation (e.g. INV-20260527-001)."""

    def test_date_scoped_format(self):
        """generate() with use_date_scope=True produces PREFIX-YYYYMMDD-NNN format."""
        db = MagicMock()
        # Simulate: no existing rows → count returns 0
        db.execute.return_value = None  # advisory lock
        db.query.return_value.filter.return_value.scalar.return_value = 0

        gen = ReferenceGenerator(db)
        result = gen.generate(
            model=_FakeModel,
            column=_FakeModel.ref,
            prefix="INV",
            lock_key="invoice_number",
            width=3,
            use_date_scope=True,
        )

        today_str = date.today().strftime("%Y%m%d")
        assert result == f"INV-{today_str}-001"

    def test_sequential_increment(self):
        """Sequential calls with existing count produce incrementing numbers."""
        db = MagicMock()
        db.execute.return_value = None
        db.query.return_value.filter.return_value.scalar.return_value = 5

        gen = ReferenceGenerator(db)
        result = gen.generate(
            model=_FakeModel,
            column=_FakeModel.ref,
            prefix="EXP",
            lock_key="expense_number",
            width=3,
            use_date_scope=True,
        )

        today_str = date.today().strftime("%Y%m%d")
        assert result == f"EXP-{today_str}-006"


class TestReferenceGeneratorGlobal:
    """Behaviors for non-date-scoped reference generation (e.g. IN-0042)."""

    def test_global_format(self):
        """generate() with use_date_scope=False produces PREFIX-NNNN format."""
        db = MagicMock()
        db.execute.return_value = None
        db.query.return_value.scalar.return_value = 0

        gen = ReferenceGenerator(db)
        result = gen.generate(
            model=_FakeModel,
            column=_FakeModel.ref,
            prefix="IN",
            lock_key="invoice_reference_gen",
            width=4,
            use_date_scope=False,
        )

        assert result == "IN-0001"

    def test_advisory_lock_called(self):
        """Advisory lock is acquired with the correct key."""
        db = MagicMock()
        db.query.return_value.scalar.return_value = 0

        gen = ReferenceGenerator(db)
        gen.generate(
            model=_FakeModel,
            column=_FakeModel.ref,
            prefix="QT",
            lock_key="quote_reference_gen",
            width=4,
            use_date_scope=False,
        )

        # Verify advisory lock was called
        db.execute.assert_called_once()
        call_args = db.execute.call_args
        sql_text = str(call_args[0][0])
        assert "pg_advisory_xact_lock" in sql_text
        assert call_args[1]["key"] == "quote_reference_gen"


class TestReferenceGeneratorMaxStrategy:
    """Behaviors for MAX(suffix) strategy (used by expense references)."""

    def test_max_strategy_format(self):
        """generate() with use_max_strategy=True uses MAX instead of COUNT."""
        db = MagicMock()
        db.execute.return_value = None
        # MAX query returns 41 (the highest existing suffix)
        db.query.return_value.scalar.return_value = 41

        gen = ReferenceGenerator(db)
        result = gen.generate(
            model=_FakeModel,
            column=_FakeModel.ref,
            prefix="EXP",
            lock_key="expense_reference_gen",
            width=4,
            use_date_scope=False,
            use_max_strategy=True,
            strip_prefix_len=4,  # len("EXP-")
        )

        assert result == "EXP-0042"

    def test_max_strategy_with_no_existing(self):
        """MAX strategy with no existing rows (NULL) starts at 001."""
        db = MagicMock()
        db.execute.return_value = None
        db.query.return_value.scalar.return_value = None  # no rows

        gen = ReferenceGenerator(db)
        result = gen.generate(
            model=_FakeModel,
            column=_FakeModel.ref,
            prefix="EXP",
            lock_key="expense_reference_gen",
            width=4,
            use_date_scope=False,
            use_max_strategy=True,
            strip_prefix_len=4,
        )

        assert result == "EXP-0001"
