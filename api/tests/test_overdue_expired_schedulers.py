"""Tests for the overdue/expired schedulers and the centralized predicate"""

from datetime import date, timedelta
from decimal import Decimal

from app.common.financial import check_is_overdue
from app.constants.enums import (
    Currency,
    CustomerStatus,
    InvoiceStatus,
    QuoteStatus,
)
from app.modules.customers.models import Customer
from app.modules.invoices.models import Invoice
from app.modules.invoices.service import InvoiceService
from app.modules.quotes.models import Quote
from app.modules.quotes.service import QuoteService


def _customer(db, email="sched@acme.test") -> Customer:
    c = Customer(
        customer_type="business",
        company_name="Acme",
        first_name="Ada",
        last_name="L",
        email=email,
        phone="+254700000000",
        currency=Currency.KES,
        status=CustomerStatus.ACTIVE,
    )
    db.add(c)
    db.flush()
    return c


def _invoice(
    db, customer, *, number, status, due_offset_days, balance=Decimal("10.00")
):
    today = date.today()
    inv = Invoice(
        invoice_number=number,
        invoice_reference=number.replace("INV", "IN"),
        customer_id=customer.id,
        transaction_date=today - timedelta(days=60),
        due_date=today + timedelta(days=due_offset_days),
        currency=Currency.KES,
        status=status,
        subtotal=Decimal("10.00"),
        tax_total=Decimal("0.00"),
        total_due=Decimal("10.00"),
        amount_paid=Decimal("10.00") - balance,
        balance_due=balance,
    )
    db.add(inv)
    db.flush()
    return inv


def _quote(db, customer, *, number, status, due_offset_days):
    today = date.today()
    q = Quote(
        quote_number=number,
        quote_reference=number.replace("QTE", "QT"),
        customer_id=customer.id,
        transaction_date=today - timedelta(days=60),
        due_date=today + timedelta(days=due_offset_days),
        status=status,
        currency=Currency.KES,
        subtotal=Decimal("10.00"),
        tax_total=Decimal("0.00"),
        total_due=Decimal("10.00"),
    )
    db.add(q)
    db.flush()
    return q


class TestInvoiceOverdueScheduler:
    def test_past_due_sent_invoice_becomes_overdue(self, db):
        customer = _customer(db, email="inv-sched@acme.test")
        service = InvoiceService(db)

        due = _invoice(
            db,
            customer,
            number="INV-OD-1",
            status=InvoiceStatus.SENT,
            due_offset_days=-5,
        )
        current = _invoice(
            db,
            customer,
            number="INV-OD-2",
            status=InvoiceStatus.SENT,
            due_offset_days=5,
        )
        paid = _invoice(
            db,
            customer,
            number="INV-OD-3",
            status=InvoiceStatus.PAID,
            due_offset_days=-5,
            balance=Decimal("0.00"),
        )
        draft = _invoice(
            db,
            customer,
            number="INV-OD-4",
            status=InvoiceStatus.DRAFT,
            due_offset_days=-5,
        )

        count = service.bulk_transition_overdue()
        assert count == 1

        db.refresh(due)
        db.refresh(current)
        db.refresh(paid)
        db.refresh(draft)
        assert due.status == InvoiceStatus.OVERDUE
        assert current.status == InvoiceStatus.SENT
        assert paid.status == InvoiceStatus.PAID
        assert draft.status == InvoiceStatus.DRAFT


class TestQuoteExpiredScheduler:
    def test_past_due_sent_quote_becomes_expired(self, db):
        customer = _customer(db, email="qt-sched@acme.test")
        service = QuoteService(db)

        due = _quote(
            db, customer, number="QTE-EX-1", status=QuoteStatus.SENT, due_offset_days=-5
        )
        current = _quote(
            db, customer, number="QTE-EX-2", status=QuoteStatus.SENT, due_offset_days=5
        )
        approved = _quote(
            db,
            customer,
            number="QTE-EX-3",
            status=QuoteStatus.APPROVED,
            due_offset_days=-5,
        )

        count = service.bulk_transition_expired()
        assert count == 1

        db.refresh(due)
        db.refresh(current)
        db.refresh(approved)
        assert due.status == QuoteStatus.EXPIRED
        assert due.expired_at is not None
        assert current.status == QuoteStatus.SENT
        assert approved.status == QuoteStatus.APPROVED


class TestCentralizedPredicate:
    def test_terminal_statuses_are_not_overdue(self):
        yesterday = date.today() - timedelta(days=1)
        # Invoice terminal set
        assert check_is_overdue("sent", yesterday, {"paid", "canceled"}) is True
        assert check_is_overdue("paid", yesterday, {"paid", "canceled"}) is False
        # Quote terminal set
        assert check_is_overdue("sent", yesterday, {"approved", "invoiced"}) is True
        assert (
            check_is_overdue("approved", yesterday, {"approved", "invoiced"}) is False
        )

    def test_future_due_date_is_not_overdue(self):
        tomorrow = date.today() + timedelta(days=1)
        assert check_is_overdue("sent", tomorrow) is False
