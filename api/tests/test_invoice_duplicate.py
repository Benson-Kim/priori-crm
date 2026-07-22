"""Tests for duplicate_invoice returning the correct response type.

duplicate_invoice is annotated -> InvoiceDuplicateResponse and the router
validates against it; returning the ORM Invoice raised at response time on
every call. The service must return a populated InvoiceDuplicateResponse.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.common.exceptions import BadRequestException
from app.constants.enums import InvoiceStatus
from app.modules.invoices.schemas import InvoiceDuplicateResponse
from app.modules.invoices.service import InvoiceService


class _FakeInvoice:
    """Minimal Invoice stand-in carrying the fields duplicate_invoice copies."""

    def __init__(self, *, invoice_number: str):
        self.id = uuid.uuid4()
        self.invoice_number = invoice_number
        self.customer_id = uuid.uuid4()
        self.currency = "KES"
        self.subtotal = Decimal("100.00")
        self.discount_type = None
        self.discount_amount = None
        self.discount_percentage = None
        self.vat_enabled = False
        self.vat_rate = None
        self.tax_total = Decimal("16.00")
        self.total_due = Decimal("116.00")
        self.rfq_number = None
        self.notes = None
        self.status = InvoiceStatus.PAID
        self.line_items = [
            SimpleNamespace(
                item_name="Widget",
                description="A widget",
                quantity=Decimal("1.00"),
                unit_price=Decimal("100.00"),
                tax_type="no_tax",
            )
        ]


class TestDuplicateInvoiceResponse:
    def test_returns_invoice_duplicate_response_with_ids(self):
        original = _FakeInvoice(invoice_number="INV-0001")
        new_number = "INV-0002"
        created_invoice = _FakeInvoice(invoice_number=new_number)

        db = MagicMock()
        svc = InvoiceService(db)
        svc.get_by_id = MagicMock(return_value=original)  # type: ignore[method-assign]
        svc.create = MagicMock(return_value=created_invoice)  # type: ignore[method-assign]

        result = svc.duplicate_invoice(original.id)

        assert isinstance(result, InvoiceDuplicateResponse)
        assert result.original_invoice_id == original.id
        assert result.new_invoice_id == created_invoice.id
        assert result.new_invoice_number == new_number

    def test_rejects_source_without_line_items(self):
        original = _FakeInvoice(invoice_number="INV-EMPTY")
        original.line_items = []

        svc = InvoiceService(MagicMock())
        svc.get_by_id = MagicMock(return_value=original)  # type: ignore[method-assign]
        svc.create = MagicMock()  # type: ignore[method-assign]

        with pytest.raises(BadRequestException, match="without line items"):
            svc.duplicate_invoice(original.id)

        svc.create.assert_not_called()
