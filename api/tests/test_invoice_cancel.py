"""Tests for cancel_invoice routing through the state machine

cancel_invoice must call _transition() so ALLOWED_TRANSITIONS is enforced and
the version bump is owned in a single place, rather than mutating status inline.
"""

import uuid
from unittest.mock import MagicMock

import pytest

from app.common.exceptions import BadRequestException
from app.constants.enums import InvoiceStatus
from app.modules.invoices.service import InvoiceService


class _FakeInvoice:
    """Minimal Invoice stand-in for unit tests."""

    def __init__(self, *, status: str = InvoiceStatus.SENT, version: int = 1):
        self.id = uuid.uuid4()
        self.customer_id = uuid.uuid4()
        self.status = status
        self.version = version
        self.invoice_number = "INV-0001"


def _service_with(invoice: _FakeInvoice) -> InvoiceService:
    db = MagicMock()
    svc = InvoiceService(db)
    # cancel_invoice loads via get_by_id; stub it directly.
    svc.get_by_id = MagicMock(return_value=invoice)  # type: ignore[method-assign]
    # cancel_invoice also resyncs the customer balance via a real
    # query; against the MagicMock db that yields a non-numeric value. Stub it
    # so these unit tests isolate the state-machine transition + version bump.
    # Balance resync is covered against Postgres in test_currency_and_balance.
    svc._update_customer_balance = MagicMock()  # type: ignore[method-assign]
    return svc


class TestCancelInvoiceUsesStateMachine:
    def test_cancel_transitions_and_bumps_version_once(self):
        invoice = _FakeInvoice(status=InvoiceStatus.SENT, version=3)
        svc = _service_with(invoice)

        result = svc.cancel_invoice(invoice.id)

        assert result.status == InvoiceStatus.CANCELED
        # _transition bumps the version exactly once.
        assert result.version == 4

    def test_cancel_from_paid_is_allowed(self):
        invoice = _FakeInvoice(status=InvoiceStatus.PAID, version=1)
        svc = _service_with(invoice)

        result = svc.cancel_invoice(invoice.id)

        assert result.status == InvoiceStatus.CANCELED
        assert result.version == 2

    def test_cancel_already_canceled_raises(self):
        invoice = _FakeInvoice(status=InvoiceStatus.CANCELED, version=5)
        svc = _service_with(invoice)

        with pytest.raises(BadRequestException):
            svc.cancel_invoice(invoice.id)
