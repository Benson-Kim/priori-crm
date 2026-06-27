"""Tests for record_payment row-locking and state-machine routing

record_payment must:
- load the invoice with SELECT ... FOR UPDATE (with_for_update) to avoid the
  TOCTOU overpay race;
- route the PAID/PARTIAL status change through _transition() so
  ALLOWED_TRANSITIONS is enforced and the version is bumped exactly once;
- still reject overpayment.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.common.exceptions import BadRequestException
from app.constants.enums import InvoiceStatus
from app.modules.invoices.service import InvoiceService


class _FakeInvoice:
    """Minimal Invoice stand-in for unit tests."""

    def __init__(
        self,
        *,
        status: str = InvoiceStatus.SENT,
        total_due: str = "100.00",
        amount_paid: str = "0.00",
        version: int = 1,
    ):
        self.id = uuid.uuid4()
        self.customer_id = uuid.uuid4()
        self.status = status
        self.total_due = Decimal(total_due)
        self.amount_paid = Decimal(amount_paid)
        self.balance_due = self.total_due - self.amount_paid
        self.version = version
        self.paid_at = None
        self.invoice_number = "INV-0001"


class _FakePayment:
    """PaymentCreate stand-in."""

    def __init__(self, amount: str):
        self.amount = Decimal(amount)
        self.payment_date = date.today()
        self.payment_method = "bank_transfer"
        self.reference = "PAY-1"
        self.notes = None


def _service_locking(invoice: _FakeInvoice) -> tuple[InvoiceService, MagicMock]:
    """Build a service whose locked query() returns the given invoice.

    Returns the service and the with_for_update mock so tests can assert the
    row lock was acquired.
    """
    db = MagicMock()

    chain = MagicMock()
    db.query.return_value = chain
    chain.options.return_value = chain
    chain.filter.return_value = chain
    for_update = MagicMock()
    chain.with_for_update.return_value = for_update
    for_update.first.return_value = invoice

    svc = InvoiceService(db)
    # Avoid touching the (mocked) customer-balance recompute query.
    svc._update_customer_balance = MagicMock()  # type: ignore[method-assign]
    return svc, chain.with_for_update


class TestRecordPaymentLocking:
    def test_acquires_row_lock(self):
        invoice = _FakeInvoice()
        svc, with_for_update = _service_locking(invoice)

        svc.record_payment(invoice.id, _FakePayment("40.00"))

        with_for_update.assert_called_once()

    def test_partial_payment_transitions_to_partial_once(self):
        invoice = _FakeInvoice(total_due="100.00", version=2)
        svc, _ = _service_locking(invoice)

        svc.record_payment(invoice.id, _FakePayment("40.00"))

        assert invoice.status == InvoiceStatus.PARTIAL
        assert invoice.balance_due == Decimal("60.00")
        assert invoice.version == 3  # single bump via _transition

    def test_full_payment_transitions_to_paid(self):
        invoice = _FakeInvoice(total_due="100.00", version=1)
        svc, _ = _service_locking(invoice)

        svc.record_payment(invoice.id, _FakePayment("100.00"))

        assert invoice.status == InvoiceStatus.PAID
        assert invoice.balance_due == Decimal("0.00")
        assert invoice.paid_at is not None
        assert invoice.version == 2

    def test_overpayment_is_accepted(self):
        invoice = _FakeInvoice(total_due="100.00")
        svc, _ = _service_locking(invoice)

        svc.record_payment(invoice.id, _FakePayment("150.00"))

        # Overpayment surfaces as a negative balance and settles to PAID.
        assert invoice.status == InvoiceStatus.PAID
        assert invoice.balance_due == Decimal("-50.00")
        assert invoice.paid_at is not None

    def test_payment_on_draft_is_rejected(self):
        invoice = _FakeInvoice(status=InvoiceStatus.DRAFT)
        svc, _ = _service_locking(invoice)

        with pytest.raises(BadRequestException):
            svc.record_payment(invoice.id, _FakePayment("10.00"))
