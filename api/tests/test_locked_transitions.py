"""Locked-load regression tests for status transitions (issue #43).

Contract (ISSUE-005): every status transition loads its row through
SELECT ... FOR UPDATE (the shared _get_locked helper) so concurrent
transitions serialize on the row lock instead of racing check-then-act.

- Unit tests (any backend): the transition entry points acquire the row
  lock (with_for_update) before their eligibility checks.
- PostgreSQL-gated tests: two real sessions; the second transition blocks
  on the first one's row lock and only proceeds after commit.
"""

import threading
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.constants.enums import (
    Currency,
    CustomerStatus,
    CustomerType,
    ExpenseStatus,
    InvoiceStatus,
    QuoteStatus,
    VendorStatus,
)
from tests.conftest import USING_POSTGRES, TestingSessionLocal

# unit: transition entry points acquire the row lock


def _locked_db(entity) -> tuple[MagicMock, MagicMock]:
    """MagicMock session whose FOR UPDATE chain returns ``entity``.

    Returns (db, with_for_update mock) so tests can assert the lock was
    acquired. Mirrors the pattern in test_record_payment.py.
    """
    db = MagicMock()
    chain = MagicMock()
    db.query.return_value = chain
    chain.options.return_value = chain
    chain.filter.return_value = chain
    for_update = MagicMock()
    chain.with_for_update.return_value = for_update
    for_update.first.return_value = entity
    return db, chain.with_for_update


class TestApproveQuoteAcquiresLock:
    def _fake_quote(self):
        return SimpleNamespace(
            id=uuid.uuid4(),
            # deal_id None: standalone quote, so the billing-profile sync
            # gate (issue #44) is skipped — this stub isolates the lock.
            deal_id=None,
            status=QuoteStatus.SENT,
            is_expired=False,
            version=1,
            quote_number="QTE-0001",
            approved_at=None,
            approved_by=None,
        )

    def test_approve_loads_for_update(self):
        from app.modules.quotes.service import QuoteService

        quote = self._fake_quote()
        db, for_update = _locked_db(quote)
        svc = QuoteService(db)
        svc._capture_owner_snapshot = MagicMock()

        result = svc.approve_quote(quote.id)

        for_update.assert_called()
        assert result.status == QuoteStatus.APPROVED
        assert result.version == 2  # _transition bumps exactly once


class TestCancelInvoiceAcquiresLock:
    def _fake_invoice(self):
        return SimpleNamespace(
            id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            status=InvoiceStatus.SENT,
            version=1,
            invoice_number="INV-0001",
        )

    def test_cancel_loads_for_update(self):
        from app.modules.invoices.service import InvoiceService

        invoice = self._fake_invoice()
        db, for_update = _locked_db(invoice)
        svc = InvoiceService(db)
        # Isolate from the (mocked) customer-balance recompute query;
        # the real resync is covered by the Postgres tests below.
        svc._update_customer_balance = MagicMock()  # type: ignore[method-assign]

        result = svc.cancel_invoice(invoice.id)

        for_update.assert_called()
        assert result.status == InvoiceStatus.CANCELED


class TestExpenseCancelDeleteAcquireLock:
    def _fake_expense(self, status=ExpenseStatus.PENDING):
        return SimpleNamespace(
            id=uuid.uuid4(),
            status=status,
            version=1,
            expense_number="EXP-0001",
            expense_reference="EXP-0001",
            total_due=Decimal("100.00"),
            payments=[],
            documents=[],
        )

    def test_cancel_loads_for_update(self):
        from app.modules.expenses.service import ExpenseService

        expense = self._fake_expense()
        db, for_update = _locked_db(expense)
        svc = ExpenseService(db)

        result = svc.cancel(expense.id)

        for_update.assert_called()
        assert result.status == ExpenseStatus.CANCELED

    def test_delete_loads_for_update(self):
        from app.modules.expenses.service import ExpenseService

        expense = self._fake_expense()
        db, for_update = _locked_db(expense)
        svc = ExpenseService(db)
        svc._purge_storage_objects = MagicMock()  # type: ignore[method-assign]

        svc.delete(expense.id)

        for_update.assert_called()


# fixtures for the two-session concurrency tests


def _make_customer(session):
    from app.modules.customers.models import Customer

    customer = Customer(
        customer_type=CustomerType.INDIVIDUAL,
        status=CustomerStatus.ACTIVE,
        first_name="Lock",
        last_name="Race",
        email=f"lock-{uuid.uuid4().hex[:8]}@example.com",
        phone="+254700000000",
        currency=Currency.KES,
        balance=Decimal("0.00"),
    )
    session.add(customer)
    session.flush()
    return customer


def _make_quote(session, customer, status=QuoteStatus.DRAFT):
    from app.modules.quotes.models import Quote

    today = date.today()
    quote = Quote(
        quote_number=f"QTE-{uuid.uuid4().hex[:8]}",
        quote_reference=f"QT-{uuid.uuid4().hex[:6]}",
        customer_id=customer.id,
        transaction_date=today,
        due_date=today + timedelta(days=30),
        currency=Currency.KES,
        status=status,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("0.00"),
        total_due=Decimal("100.00"),
        version=1,
    )
    session.add(quote)
    session.flush()
    return quote


def _make_invoice(session, customer, status=InvoiceStatus.SENT):
    from app.modules.invoices.models import Invoice

    today = date.today()
    total = Decimal("100.00")
    invoice = Invoice(
        invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
        invoice_reference=f"IN-{uuid.uuid4().hex[:6]}",
        customer_id=customer.id,
        transaction_date=today,
        due_date=today + timedelta(days=30),
        currency=Currency.KES,
        status=status,
        subtotal=total,
        tax_total=Decimal("0.00"),
        total_due=total,
        amount_paid=Decimal("0.00"),
        balance_due=total,
        version=1,
    )
    session.add(invoice)
    session.flush()
    return invoice


@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="Requires PostgreSQL row locks; SQLite ignores FOR UPDATE",
)
class TestLockedTransitionConcurrency:
    """Two sessions: the second transition blocks on the first one's lock."""

    JOIN_TIMEOUT = 15

    def _run_blocked(self, target):
        """Start ``target`` in a thread and assert it blocks on the lock."""
        thread = threading.Thread(target=target)
        thread.start()
        time.sleep(0.5)  # give the worker time to reach the row lock
        assert thread.is_alive(), "expected the transition to block on the lock"
        return thread

    def test_approve_blocks_on_concurrent_send(self, db):
        from app.modules.quotes.service import QuoteService

        customer = _make_customer(db)
        quote = _make_quote(db, customer, status=QuoteStatus.DRAFT)
        db.commit()

        errors: list[Exception] = []

        def approve_worker() -> None:
            session = TestingSessionLocal()
            try:
                QuoteService(session).approve_quote(quote.id)
                session.commit()
            except Exception as exc:  # surfaced after join
                session.rollback()
                errors.append(exc)
            finally:
                session.close()

        # Session 1 transitions DRAFT -> SENT and holds the row lock.
        QuoteService(db).mark_as_sent(quote.id)

        thread = self._run_blocked(approve_worker)

        db.commit()  # release the lock; approve now observes SENT
        thread.join(timeout=self.JOIN_TIMEOUT)
        assert not thread.is_alive()
        assert errors == []

        check = TestingSessionLocal()
        try:
            from app.modules.quotes.models import Quote

            fresh = check.query(Quote).filter(Quote.id == quote.id).one()
            # approve saw the committed SENT row, not the stale DRAFT one:
            # both version bumps survive (no lost update).
            assert fresh.status == QuoteStatus.APPROVED
            assert fresh.version == 3
        finally:
            check.close()

    def test_cancel_blocks_on_concurrent_record_payment(self, db):
        from app.modules.invoices.service import InvoiceService

        customer = _make_customer(db)
        invoice = _make_invoice(db, customer, status=InvoiceStatus.SENT)
        db.commit()

        errors: list[Exception] = []

        def cancel_worker() -> None:
            session = TestingSessionLocal()
            try:
                InvoiceService(session).cancel_invoice(invoice.id)
                session.commit()
            except Exception as exc:
                session.rollback()
                errors.append(exc)
            finally:
                session.close()

        # Session 1 records a full payment and holds the row lock
        # (record_payment flushes only; get_db owns the commit).
        payment = SimpleNamespace(
            amount=Decimal("100.00"),
            payment_date=date.today(),
            payment_method="bank_transfer",
            reference=None,
            notes=None,
        )
        InvoiceService(db).record_payment(invoice.id, payment)

        thread = self._run_blocked(cancel_worker)

        db.commit()  # release the lock; cancel now observes PAID
        thread.join(timeout=self.JOIN_TIMEOUT)
        assert not thread.is_alive()
        assert errors == []

        check = TestingSessionLocal()
        try:
            from app.modules.customers.models import Customer
            from app.modules.invoices.models import Invoice

            fresh = check.query(Invoice).filter(Invoice.id == invoice.id).one()
            # cancel observed the committed PAID row (no stale read) and
            # took the legal PAID -> CANCELED edge; the payment survived.
            assert fresh.status == InvoiceStatus.CANCELED
            assert fresh.amount_paid == Decimal("100.00")
            # Balance resyncs applied in lock order: a canceled invoice
            # carries no outstanding customer balance.
            fresh_customer = (
                check.query(Customer).filter(Customer.id == customer.id).one()
            )
            assert fresh_customer.balance == Decimal("0.00")
        finally:
            check.close()


@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="Requires PostgreSQL row locks; SQLite ignores FOR UPDATE",
)
class TestExpenseLockedTransitionConcurrency:
    """Expense cancel serializes with a concurrent payment."""

    JOIN_TIMEOUT = 15

    def test_cancel_blocks_on_concurrent_mark_as_paid(self, db):
        from app.modules.expenses.models import Expense
        from app.modules.expenses.service import ExpenseService
        from app.modules.vendors.models import Vendor

        vendor = Vendor(
            vendor_name=f"Lock Vendor {uuid.uuid4().hex[:6]}",
            currency=Currency.KES,
            status=VendorStatus.ACTIVE,
            version=1,
        )
        db.add(vendor)
        db.flush()

        today = date.today()
        total = Decimal("100.00")
        expense = Expense(
            expense_number=f"EXP-{uuid.uuid4().hex[:8]}",
            expense_reference=f"EXP-{uuid.uuid4().hex[:6]}",
            vendor_id=vendor.id,
            expense_date=today,
            due_date=today + timedelta(days=30),
            currency=Currency.KES,
            status=ExpenseStatus.PENDING,
            subtotal=total,
            tax_total=Decimal("0.00"),
            total_due=total,
            amount_paid=Decimal("0.00"),
            balance_due=total,
            version=1,
        )
        db.add(expense)
        db.commit()

        errors: list[Exception] = []

        def cancel_worker() -> None:
            session = TestingSessionLocal()
            try:
                ExpenseService(session).cancel(expense.id)
                session.commit()
            except Exception as exc:
                session.rollback()
                errors.append(exc)
            finally:
                session.close()

        # Session 1 settles the expense and holds the row lock.
        ExpenseService(db).mark_as_paid(expense.id)

        thread = threading.Thread(target=cancel_worker)
        thread.start()
        time.sleep(0.5)
        assert thread.is_alive(), "expected cancel to block on the lock"

        db.commit()  # release the lock; cancel now observes PAID
        thread.join(timeout=self.JOIN_TIMEOUT)
        assert not thread.is_alive()
        assert errors == []

        check = TestingSessionLocal()
        try:
            fresh = check.query(Expense).filter(Expense.id == expense.id).one()
            # cancel saw the committed PAID row and voided it (legal edge);
            # the settlement payment is preserved on the audit trail.
            assert fresh.status == ExpenseStatus.CANCELED
            assert fresh.amount_paid == total
        finally:
            check.close()
