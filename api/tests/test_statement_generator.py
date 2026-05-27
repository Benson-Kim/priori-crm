"""
Tests for the StatementGenerator deep module in common/statement.py.

RED→GREEN cycle: statement generation for generic entities (Customer/Vendor).
"""
from datetime import date
from decimal import Decimal

import pytest

from app.common.statement import (
    CreditEntry,
    DebitEntry,
    StatementGenerator,
)


# Tests


class TestStatementGeneratorEmptyPeriod:
    """Opening balance carried through when no transactions exist."""

    def test_empty_period_returns_opening_row_only(self):
        txns, summary = StatementGenerator.generate(
            opening_balance=Decimal("1000.00"),
            period_start=date(2026, 1, 1),
            debits=[],
            credits=[],
        )

        assert len(txns) == 1
        assert txns[0]["description"] == "Opening Balance"
        assert txns[0]["balance"] == Decimal("1000.00")

        assert summary["opening_balance"] == Decimal("1000.00")
        assert summary["invoiced_amount"] == Decimal("0.00")
        assert summary["amount_paid"] == Decimal("0.00")
        assert summary["balance_due"] == Decimal("1000.00")


class TestStatementGeneratorDebitsOnly:
    """Debits increase running balance; no credits."""

    def test_debits_accumulate(self):
        debits = [
            DebitEntry(date=date(2026, 1, 5), description="Invoice INV-001", amount=Decimal("500.00")),
            DebitEntry(date=date(2026, 1, 15), description="Invoice INV-002", amount=Decimal("300.00")),
        ]

        txns, summary = StatementGenerator.generate(
            opening_balance=Decimal("0.00"),
            period_start=date(2026, 1, 1),
            debits=debits,
            credits=[],
        )

        # Opening + 2 debits
        assert len(txns) == 3
        assert txns[1]["amount"] == Decimal("500.00")
        assert txns[1]["balance"] == Decimal("500.00")
        assert txns[2]["amount"] == Decimal("300.00")
        assert txns[2]["balance"] == Decimal("800.00")

        assert summary["invoiced_amount"] == Decimal("800.00")
        assert summary["balance_due"] == Decimal("800.00")


class TestStatementGeneratorCreditsOnly:
    """Credits reduce running balance from opening."""

    def test_credits_reduce_balance(self):
        credits = [
            CreditEntry(date=date(2026, 2, 10), description="Payment for INV-001", amount=Decimal("400.00")),
        ]

        txns, summary = StatementGenerator.generate(
            opening_balance=Decimal("1000.00"),
            period_start=date(2026, 2, 1),
            debits=[],
            credits=credits,
        )

        assert len(txns) == 2
        assert txns[1]["payment"] == Decimal("400.00")
        assert txns[1]["balance"] == Decimal("600.00")

        assert summary["amount_paid"] == Decimal("400.00")
        assert summary["balance_due"] == Decimal("600.00")


class TestStatementGeneratorChronologicalMerge:
    """Interleaved debits and credits sort by date correctly."""

    def test_mixed_transactions_sort_chronologically(self):
        debits = [
            DebitEntry(date=date(2026, 3, 5), description="Expense EXP-001", amount=Decimal("200.00")),
            DebitEntry(date=date(2026, 3, 20), description="Expense EXP-002", amount=Decimal("150.00")),
        ]
        credits = [
            CreditEntry(date=date(2026, 3, 10), description="Payment Made", amount=Decimal("100.00")),
        ]

        txns, summary = StatementGenerator.generate(
            opening_balance=Decimal("500.00"),
            period_start=date(2026, 3, 1),
            debits=debits,
            credits=credits,
        )

        # Opening + 3 transactions
        assert len(txns) == 4

        # Check chronological order: EXP-001 (3/5), Payment (3/10), EXP-002 (3/20)
        assert txns[1]["description"] == "Expense EXP-001"
        assert txns[1]["balance"] == Decimal("700.00")

        assert txns[2]["description"] == "Payment Made"
        assert txns[2]["balance"] == Decimal("600.00")

        assert txns[3]["description"] == "Expense EXP-002"
        assert txns[3]["balance"] == Decimal("750.00")

        assert summary["opening_balance"] == Decimal("500.00")
        assert summary["invoiced_amount"] == Decimal("350.00")
        assert summary["amount_paid"] == Decimal("100.00")
        assert summary["balance_due"] == Decimal("750.00")


class TestStatementGeneratorSameDateOrdering:
    """Entries on the same date preserve debit-before-credit order."""

    def test_same_date_debits_come_first(self):
        debits = [
            DebitEntry(date=date(2026, 4, 1), description="Invoice INV-010", amount=Decimal("1000.00")),
        ]
        credits = [
            CreditEntry(date=date(2026, 4, 1), description="Payment for INV-010", amount=Decimal("1000.00")),
        ]

        txns, summary = StatementGenerator.generate(
            opening_balance=Decimal("0.00"),
            period_start=date(2026, 4, 1),
            debits=debits,
            credits=credits,
        )

        assert len(txns) == 3

        # Debit should come before credit on same date
        assert txns[1]["description"] == "Invoice INV-010"
        assert txns[1]["balance"] == Decimal("1000.00")

        assert txns[2]["description"] == "Payment for INV-010"
        assert txns[2]["balance"] == Decimal("0.00")

        assert summary["balance_due"] == Decimal("0.00")
