"""
Tests for the mark_as_paid audit trail fix.

Ensure a payment record is always created so the audit trail is complete.
"""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.common.exceptions import BadRequestException, NotFoundException
from app.constants.enums import ExpenseStatus


class _FakeExpense:
    """Minimal Expense stand-in for unit tests."""

    def __init__(
        self,
        *,
        id: uuid.UUID | None = None,
        status: str = ExpenseStatus.PENDING,
        total_due: Decimal = Decimal("1000.00"),
        amount_paid: Decimal = Decimal("0.00"),
        balance_due: Decimal = Decimal("1000.00"),
        version: int = 1,
    ):
        self.id = id or uuid.uuid4()
        self.status = status
        self.total_due = total_due
        self.amount_paid = amount_paid
        self.balance_due = balance_due
        self.version = version
        self.paid_at = None
        self.expense_reference = "EXP-0001"


class TestMarkAsPaidCreatesPaymentRecord:
    """mark_as_paid must create an ExpensePayment for audit trail."""

    def test_payment_record_is_added_to_session(self):
        """An ExpensePayment row must be added to the DB session."""
        from app.modules.expenses.service import ExpenseService

        db = MagicMock()
        expense = _FakeExpense()

        # Mock query chain:
        # .query().options().filter().with_for_update().first()
        db.query.return_value.options.return_value.filter.return_value.with_for_update.return_value.first.return_value = expense

        svc = ExpenseService(db)
        svc.mark_as_paid(expense.id)

        # Verify db.add was called with an ExpensePayment
        from app.modules.expenses.models import ExpensePayment

        add_calls = db.add.call_args_list
        payment_adds = [c for c in add_calls if isinstance(c[0][0], ExpensePayment)]
        assert len(payment_adds) == 1, "Expected exactly one ExpensePayment to be added"

        payment = payment_adds[0][0][0]
        assert payment.amount == expense.total_due
        assert payment.expense_id == expense.id


class TestMarkAsPaidSetsFinancialFields:
    """Financial fields must be correctly updated."""

    def test_balance_zeroed_and_status_paid(self):
        from app.modules.expenses.service import ExpenseService

        db = MagicMock()
        expense = _FakeExpense(
            total_due=Decimal("500.00"),
            balance_due=Decimal("500.00"),
        )
        db.query.return_value.options.return_value.filter.return_value.with_for_update.return_value.first.return_value = expense

        svc = ExpenseService(db)
        result = svc.mark_as_paid(expense.id)

        assert result.status == ExpenseStatus.PAID
        assert result.balance_due == Decimal("0.00")
        assert result.amount_paid == result.total_due
        assert result.paid_at is not None


class TestMarkAsPaidAlreadyPaid:
    """Cannot mark a PAID expense as paid again."""

    def test_raises_on_already_paid(self):
        from app.modules.expenses.service import ExpenseService

        db = MagicMock()
        expense = _FakeExpense(status=ExpenseStatus.PAID)
        db.query.return_value.options.return_value.filter.return_value.with_for_update.return_value.first.return_value = expense

        svc = ExpenseService(db)
        with pytest.raises(BadRequestException):
            svc.mark_as_paid(expense.id)


class TestMarkAsPaidNotFound:
    """Non-existent expense raises NotFoundException."""

    def test_raises_not_found(self):
        from app.modules.expenses.service import ExpenseService

        db = MagicMock()
        db.query.return_value.options.return_value.filter.return_value.with_for_update.return_value.first.return_value = None

        svc = ExpenseService(db)
        with pytest.raises(NotFoundException):
            svc.mark_as_paid(uuid.uuid4())
