"""Phase 1 critical-correctness regression tests (docs/architecture-review.md).

Covers:
- customer optimistic locking (stale expectedVersion -> 409,
  version bump on success) and the currency freeze once documents exist.
- convert_to_invoice re-reads the quote under a row lock, so a
  second conversion of the same quote is rejected instead of creating a
  duplicate invoice.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)
from app.constants.enums import (
    Currency,
    CustomerStatus,
    InvoiceStatus,
    QuoteStatus,
    TaxType,
)
from app.modules.customers.models import Customer
from app.modules.customers.schemas import CustomerUpdate
from app.modules.customers.service import CustomerService
from app.modules.invoices.models import Invoice
from app.modules.invoices.service import InvoiceService
from app.modules.quotes.models import Quote, QuoteLineItem
from app.modules.quotes.service import QuoteService


def _customer(db, email="phase1@acme.test", currency=Currency.KES) -> Customer:
    c = Customer(
        customer_type="business",
        company_name="Acme",
        first_name="Ada",
        last_name="L",
        email=email,
        phone="+254700000000",
        currency=currency,
        status=CustomerStatus.ACTIVE,
    )
    db.add(c)
    db.flush()
    return c


def _invoice(db, customer, number="INV-P1-1") -> Invoice:
    today = date.today()
    inv = Invoice(
        invoice_number=number,
        invoice_reference=number.replace("INV", "IN"),
        customer_id=customer.id,
        transaction_date=today,
        due_date=today + timedelta(days=30),
        currency=customer.currency,
        status=InvoiceStatus.DRAFT,
        subtotal=Decimal("10.00"),
        tax_total=Decimal("0.00"),
        total_due=Decimal("10.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("10.00"),
        version=1,
    )
    db.add(inv)
    db.flush()
    return inv


def _approved_quote(db, customer, number="QTE-P1-1") -> Quote:
    today = date.today()
    q = Quote(
        quote_number=number,
        quote_reference=number.replace("QTE", "QT"),
        customer_id=customer.id,
        transaction_date=today,
        due_date=today + timedelta(days=30),
        currency=customer.currency,
        status=QuoteStatus.APPROVED,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("16.00"),
        total_due=Decimal("116.00"),
        version=1,
    )
    db.add(q)
    db.flush()
    db.add(
        QuoteLineItem(
            quote_id=q.id,
            line_number=1,
            item_name="Widget",
            description="A widget",
            quantity=Decimal("1.00"),
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
            tax_type=TaxType.VAT_16,
            tax_amount=Decimal("16.00"),
        )
    )
    db.flush()
    return q


class TestCustomerOptimisticLocking:
    def test_stale_version_conflicts(self, db):
        customer = _customer(db, email="cust-lock@acme.test")
        service = CustomerService(db)

        service.update(customer.id, CustomerUpdate(city="Nairobi"), expected_version=1)
        assert customer.version == 2

        with pytest.raises(ConflictException):
            service.update(
                customer.id, CustomerUpdate(city="Mombasa"), expected_version=1
            )

    def test_update_without_expected_version_still_bumps(self, db):
        customer = _customer(db, email="cust-nolock@acme.test")
        service = CustomerService(db)

        service.update(customer.id, CustomerUpdate(city="Nakuru"))
        assert customer.version == 2

    def test_matching_version_succeeds_after_reread(self, db):
        customer = _customer(db, email="cust-reread@acme.test")
        service = CustomerService(db)

        service.update(customer.id, CustomerUpdate(city="Eldoret"), expected_version=1)
        service.update(
            customer.id,
            CustomerUpdate(city="Thika"),
            expected_version=customer.version,
        )
        assert customer.version == 3
        assert customer.city == "Thika"


class TestCustomerCurrencyFreeze:
    def test_currency_change_blocked_with_invoices(self, db):
        customer = _customer(db, email="cur-frozen@acme.test")
        _invoice(db, customer)
        service = CustomerService(db)

        with pytest.raises(BadRequestException):
            service.update(customer.id, CustomerUpdate(currency=Currency.USD))
        assert customer.currency == Currency.KES

    def test_currency_change_blocked_with_quotes(self, db):
        customer = _customer(db, email="cur-frozen-q@acme.test")
        _approved_quote(db, customer, number="QTE-P1-FRZ")
        service = CustomerService(db)

        with pytest.raises(BadRequestException):
            service.update(customer.id, CustomerUpdate(currency=Currency.USD))

    def test_currency_change_allowed_without_documents(self, db):
        customer = _customer(db, email="cur-free@acme.test")
        service = CustomerService(db)

        updated = service.update(customer.id, CustomerUpdate(currency=Currency.USD))
        assert updated.currency == Currency.USD

    def test_same_currency_noop_allowed_with_documents(self, db):
        customer = _customer(db, email="cur-same@acme.test")
        _invoice(db, customer, number="INV-P1-SAME")
        service = CustomerService(db)

        updated = service.update(
            customer.id, CustomerUpdate(currency=Currency.KES, city="Kisumu")
        )
        assert updated.city == "Kisumu"
        assert updated.currency == Currency.KES


class TestQuoteConvertGuard:
    @pytest.fixture(autouse=True)
    def _stub_reference_generation(self, monkeypatch):
        """Pin invoice number generation so the test exercises the conversion
        guard, not the (Postgres-only) advisory-lock reference generator."""
        counter = iter(range(1, 100))

        monkeypatch.setattr(
            InvoiceService,
            "generate_invoice_number",
            lambda self: f"INV-CONV-{next(counter):03d}",
        )
        monkeypatch.setattr(
            InvoiceService,
            "generate_invoice_reference",
            lambda self: f"IN-CONV-{next(counter):04d}",
        )

    def test_second_conversion_rejected(self, db):
        customer = _customer(db, email="convert@acme.test")
        quote = _approved_quote(db, customer)
        service = QuoteService(db)

        result = service.convert_to_invoice(quote.id)
        assert result["invoice_id"] is not None
        assert quote.status == QuoteStatus.INVOICED
        assert quote.related_invoice_id == result["invoice_id"]

        # The locked re-read observes INVOICED + related_invoice_id and
        # rejects, so one quote can never mint two invoices.
        with pytest.raises(BadRequestException):
            service.convert_to_invoice(quote.id)

        count = db.query(Invoice).filter(Invoice.customer_id == customer.id).count()
        assert count == 1

    def test_missing_quote_raises_not_found(self, db):
        service = QuoteService(db)
        with pytest.raises(NotFoundException):
            service.convert_to_invoice(uuid.uuid4())
