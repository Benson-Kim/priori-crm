"""Tests for duplicate_invoice returning the correct response type (INV-BE-1 / V-DI-3).

duplicate_invoice is annotated -> InvoiceDuplicateResponse and the router
validates against it; returning the ORM Invoice raised at response time on
every call. The service must return a populated InvoiceDuplicateResponse.
"""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

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
        self.tax_total = Decimal("16.00")
        self.total_due = Decimal("116.00")
        self.rfq_number = None
        self.notes = None
        self.status = InvoiceStatus.PAID
        self.line_items = []


class TestDuplicateInvoiceResponse:
    def test_returns_invoice_duplicate_response_with_ids(self):
        original = _FakeInvoice(invoice_number="INV-0001")
        new_number = "INV-0002"

        db = MagicMock()
        # The duplicate Invoice created inside the method gets its id assigned
        # on flush; emulate that by setting the id when add() is called.
        created: dict = {}

        def _capture_add(obj):
            # First add() is the duplicate Invoice; give it an id + number.
            if not created and hasattr(obj, "invoice_number"):
                obj.id = uuid.uuid4()
                obj.invoice_number = new_number
                created["invoice"] = obj

        db.add.side_effect = _capture_add

        svc = InvoiceService(db)
        svc.get_by_id = MagicMock(return_value=original)  # type: ignore[method-assign]
        svc._generate_invoice_number = MagicMock(return_value=new_number)  # type: ignore[method-assign]
        svc._generate_invoice_reference = MagicMock(return_value="IN-0002")  # type: ignore[method-assign]

        result = svc.duplicate_invoice(original.id)

        assert isinstance(result, InvoiceDuplicateResponse)
        assert result.original_invoice_id == original.id
        assert result.new_invoice_id == created["invoice"].id
        assert result.new_invoice_number == new_number
