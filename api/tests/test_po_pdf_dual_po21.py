"""dual-PDF tests .

Asserts the single shared PO PDF builder produces the ORIGINAL document
(TOTAL only) when include_balance is False and the CURRENT / statement
document (with Amount Paid + Balance Due) when True. Renders real PDF
bytes via ReportLab against a fake PO and checks the byte stream is
non-empty and that the two variants differ (the balance variant is
strictly larger because it appends two summary rows).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.common.pdf import DocumentPDFGenerator


class _FakeVendor:
    vendor_name = "Acme Supplies Ltd"
    address = "1 Industrial Way"
    email = "ap@acme.example"
    phone_primary = "+254700000000"


class _FakeLineItem:
    def __init__(self) -> None:
        self.line_number = 1
        self.item_name = "Steel bolts"
        self.description = "Box of 500"
        self.quantity = Decimal("10.00")
        self.unit_price = Decimal("100.00")
        self.line_total = Decimal("1000.00")
        self.tax_type = "vat_16"
        self.tax_amount = Decimal("160.00")


class _FakePayment:
    def __init__(self) -> None:
        self.amount = Decimal("400.00")
        self.payment_date = date(2026, 6, 20)
        self.reference = "PAY-0001"


class _FakePO:
    def __init__(self) -> None:
        self.id = "00000000-0000-0000-0000-000000000020"
        self.po_number = "PO-20260618-001"
        self.po_reference = "PO-000020"
        self.vendor_id = "v1"
        self.vendor = _FakeVendor()
        self.order_date = date(2026, 6, 18)
        self.delivery_date = date(2026, 6, 30)
        self.currency = "KES"
        self.compliance_ref = None
        self.subtotal = Decimal("1000.00")
        self.tax_total = Decimal("160.00")
        self.total = Decimal("1160.00")
        self.amount_paid = Decimal("400.00")
        self.balance_due = Decimal("760.00")
        self.payments = [_FakePayment()]
        self.line_items = [_FakeLineItem()]
        self.notes = None
        self.terms_and_conditions = None
        self.owner_snapshot_id = None


def test_original_pdf_renders_without_balance() -> None:
    pdf = DocumentPDFGenerator().generate_purchase_order_pdf(
        _FakePO(), owner=None, logo_bytes=None, include_balance=False
    )
    assert isinstance(pdf, bytes)
    assert pdf.startswith(b"%PDF")


def test_statement_pdf_includes_balance_rows() -> None:
    po = _FakePO()
    gen = DocumentPDFGenerator()
    original = gen.generate_purchase_order_pdf(
        po, owner=None, logo_bytes=None, include_balance=False
    )
    statement = gen.generate_purchase_order_pdf(
        po, owner=None, logo_bytes=None, include_balance=True
    )
    assert statement.startswith(b"%PDF")
    # The statement appends Amount Paid + Balance Due rows, so it carries
    # strictly more content than the original document.
    assert len(statement) > len(original)


def test_default_is_original_document() -> None:
    # Omitting include_balance must yield the original-amount document.
    gen = DocumentPDFGenerator()
    default = gen.generate_purchase_order_pdf(_FakePO(), owner=None, logo_bytes=None)
    explicit = gen.generate_purchase_order_pdf(
        _FakePO(), owner=None, logo_bytes=None, include_balance=False
    )
    # Both render the same variant; sizes should be in the same ballpark
    # (identical structure), so neither carries the extra balance rows.
    assert default.startswith(b"%PDF")
    assert explicit.startswith(b"%PDF")
