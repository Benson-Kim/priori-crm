"""Tests for the currency domain + single-currency-per-customer policy and
customer-balance non-drift
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.exceptions import BadRequestException
from app.constants.enums import (
    Currency,
    CustomerStatus,
    InvoiceStatus,
    PaymentMethod,
    TaxType,
)
from app.modules.customers.models import Customer
from app.modules.invoices.models import Invoice
from app.modules.invoices.schemas import (
    InvoiceCreate,
    InvoiceLineItemCreate,
    InvoiceUpdate,
    PaymentCreate,
)
from app.modules.invoices.service import InvoiceService
from tests.conftest import USING_POSTGRES


def _customer(db, *, currency=Currency.KES, email="cur@acme.test") -> Customer:
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


def _line_items():
    return [
        InvoiceLineItemCreate(
            itemName="Widget",
            description="A widget",
            quantity=Decimal("1"),
            unitPrice=Decimal("100.00"),
            taxType=TaxType.NO_TAX,
        )
    ]


def _invoice_create(customer_id, **overrides) -> InvoiceCreate:
    today = date.today()
    payload = dict(
        customerId=customer_id,
        dueDate=today + timedelta(days=30),
        lineItems=_line_items(),
    )
    payload.update(overrides)
    return InvoiceCreate(**payload)


class TestSingleCurrencyPolicy:
    def test_mismatched_currency_rejected(self, db):
        customer = _customer(db, currency=Currency.USD, email="usd@acme.test")
        service = InvoiceService(db)
        # Explicit KES on a USD customer must be rejected.
        data = _invoice_create(customer.id, currency=Currency.KES)
        with pytest.raises(BadRequestException):
            service.create(data)

    def test_omitted_currency_pins_to_customer(self, db):
        customer = _customer(db, currency=Currency.USD, email="usd2@acme.test")
        service = InvoiceService(db)
        data = _invoice_create(customer.id)  # currency omitted
        invoice = service.create(data)
        assert invoice.currency == Currency.USD

    def test_matching_currency_accepted(self, db):
        customer = _customer(db, currency=Currency.EUR, email="eur@acme.test")
        service = InvoiceService(db)
        data = _invoice_create(customer.id, currency=Currency.EUR)
        invoice = service.create(data)
        assert invoice.currency == Currency.EUR


@pytest.mark.skipif(
    not USING_POSTGRES,
    reason="CHECK constraints are only enforced by PostgreSQL.",
)
class TestCurrencyCheckConstraint:
    def test_unknown_currency_rejected_at_db(self, db):
        from sqlalchemy.exc import IntegrityError

        customer = _customer(db, email="bad@acme.test")
        today = date.today()
        invoice = Invoice(
            invoice_number="INV-CUR-BAD",
            invoice_reference="IN-CURBAD",
            customer_id=customer.id,
            transaction_date=today,
            due_date=today + timedelta(days=30),
            currency="ZZZ",  # not in the supported domain
            status=InvoiceStatus.DRAFT,
            subtotal=Decimal("10.00"),
            tax_total=Decimal("0.00"),
            total_due=Decimal("10.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("10.00"),
        )
        db.add(invoice)
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


class TestBalanceNonDrift:
    def test_balance_resyncs_on_cancel_after_partial_payment(self, db):
        customer = _customer(db, email="bal1@acme.test")
        service = InvoiceService(db)

        invoice = service.create(_invoice_create(customer.id))
        service.mark_as_sent(invoice.id)
        db.flush()

        service.record_payment(
            invoice.id,
            PaymentCreate(
                amount=Decimal("40.00"),
                paymentMethod=PaymentMethod.CASH,
            ),
        )
        db.refresh(customer)
        assert customer.balance == Decimal("60.00")

        # Canceling must remove the invoice from the outstanding balance.
        service.cancel_invoice(invoice.id)
        db.refresh(customer)
        assert customer.balance == Decimal("0.00")

    def test_sent_invoice_edit_is_rejected_without_balance_drift(self, db):
        customer = _customer(db, email="bal2@acme.test")
        service = InvoiceService(db)

        invoice = service.create(_invoice_create(customer.id))
        service.mark_as_sent(invoice.id)
        db.flush()
        # A SENT invoice with no payments is included in the balance.
        service._update_customer_balance(customer.id)
        db.refresh(customer)
        assert customer.balance == Decimal("100.00")

        # Edit line items to raise the total; balance must follow.
        with pytest.raises(BadRequestException, match="issued invoice"):
            service.update(
                invoice.id,
                InvoiceUpdate(
                    lineItems=[
                        InvoiceLineItemCreate(
                            itemName="Widget",
                            description="A widget",
                            quantity=Decimal("2"),
                            unitPrice=Decimal("100.00"),
                            taxType=TaxType.NO_TAX,
                        )
                    ]
                ),
                expected_version=invoice.version,
            )

        db.refresh(invoice)
        db.refresh(customer)
        assert invoice.total_due == Decimal("100.00")
        assert customer.balance == Decimal("100.00")
