"""Phase 3 structural regression tests (docs/architecture-review.md).

Covers ISSUE-005 (locked mark_as_sent), ISSUE-015 (reference generation
from the persisted high-water mark) and ISSUE-021 (statement opening
balance parity).
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.exceptions import BadRequestException, NotFoundException
from app.common.reference import ReferenceGenerator
from app.common.reference_sequence import ReferenceSequence
from app.constants.enums import Currency, CustomerStatus, InvoiceStatus, QuoteStatus
from app.modules.customers.models import Customer
from app.modules.customers.service import CustomerService
from app.modules.invoices.models import Invoice, Payment
from app.modules.quotes.models import Quote
from app.modules.quotes.service import QuoteService


def _customer(db, email="phase3@acme.test") -> Customer:
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


def _quote(db, customer, number="QTE-P3-1", status=QuoteStatus.DRAFT) -> Quote:
    today = date.today()
    q = Quote(
        quote_number=number,
        quote_reference=number.replace("QTE", "QT"),
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
    db.add(q)
    db.flush()
    return q


def _invoice(db, customer, number="INV-P3-1", status=InvoiceStatus.SENT) -> Invoice:
    today = date.today()
    inv = Invoice(
        invoice_number=number,
        invoice_reference=number.replace("INV", "IN"),
        customer_id=customer.id,
        transaction_date=today - timedelta(days=30),
        due_date=today + timedelta(days=30),
        currency=Currency.KES,
        status=status,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("0.00"),
        total_due=Decimal("100.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("100.00"),
        version=1,
    )
    db.add(inv)
    db.flush()
    return inv


class TestMarkAsSentLocked:
    def test_mark_as_sent_transitions_once(self, db, monkeypatch):
        monkeypatch.setattr(
            QuoteService, "_capture_owner_snapshot", lambda self, e: None
        )
        customer = _customer(db, email="mark@acme.test")
        quote = _quote(db, customer)
        service = QuoteService(db)

        result = service.mark_as_sent(quote.id)
        assert result.status == QuoteStatus.SENT
        assert result.sent_at is not None
        assert result.version == 2  # exactly one bump, owned by _transition

        # SENT -> SENT is not a legal edge; the locked load + state machine
        # rejects the repeat instead of double-stamping.
        with pytest.raises(BadRequestException):
            service.mark_as_sent(quote.id)

    def test_missing_id_404s_from_locked_load(self, db):
        service = QuoteService(db)
        with pytest.raises(NotFoundException):
            service.mark_as_sent(uuid.uuid4())


class TestReferenceHighWaterMark:
    def test_bootstrap_consults_table_max_once(self, db):
        gen = ReferenceGenerator(db)
        calls: list[int] = []

        def table_max() -> int:
            calls.append(1)
            return 7

        # No sequence row yet: the table max bootstraps the counter.
        assert gen._next_with_high_water_mark("p3_scope", table_max) == 8
        assert len(calls) == 1

        row = db.get(ReferenceSequence, "p3_scope")
        assert row.last_value == 8

    def test_steady_state_skips_table_scan(self, db):
        gen = ReferenceGenerator(db)
        db.add(ReferenceSequence(scope_key="p3_steady", last_value=8))
        db.flush()

        def table_max() -> int:  # pragma: no cover - must never run
            raise AssertionError("table-max scan ran in steady state (ISSUE-015)")

        assert gen._next_with_high_water_mark("p3_steady", table_max) == 9
        assert gen._next_with_high_water_mark("p3_steady", table_max) == 10


class TestStatementOpeningBalanceParity:
    def test_canceled_invoice_and_its_payment_are_both_excluded(self, db):
        customer = _customer(db, email="stmt@acme.test")
        invoice = _invoice(db, customer)
        db.add(
            Payment(
                invoice_id=invoice.id,
                amount=Decimal("40.00"),
                payment_date=date.today() - timedelta(days=10),
                payment_method="cash",
            )
        )
        db.flush()
        service = CustomerService(db)

        as_of = date.today() + timedelta(days=1)

        # Live invoice: 100 invoiced - 40 paid = 60.
        assert service._calculate_balance_at_date(customer.id, as_of) == Decimal(
            "60.00"
        )

        # Cancel the invoice: BOTH the debit and its payment drop out, so the
        # balance is 0 — previously the orphaned credit drove it to -40.
        invoice.status = InvoiceStatus.CANCELED
        db.flush()
        assert service._calculate_balance_at_date(customer.id, as_of) == Decimal("0.00")

    def test_draft_invoices_do_not_move_the_balance(self, db):
        customer = _customer(db, email="stmt-draft@acme.test")
        _invoice(db, customer, number="INV-P3-D", status=InvoiceStatus.DRAFT)
        service = CustomerService(db)

        as_of = date.today() + timedelta(days=1)
        assert service._calculate_balance_at_date(customer.id, as_of) == Decimal("0.00")
