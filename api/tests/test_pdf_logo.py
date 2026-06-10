"""PDF owner-logo embedding (ISSUE-035).

Pure, DB-free checks on DocumentPDFGenerator: a valid logo produces a larger
PDF (the image is embedded) and renders without error, while a corrupt or
absent logo falls back to a text-only header instead of raising.
"""

import io
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.common.pdf import DocumentPDFGenerator
from app.modules.owner.schemas import OwnerInfo

pytestmark = pytest.mark.no_db


def _png_bytes() -> bytes:
    """Build a tiny in-memory PNG without external fixtures."""
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (120, 40), (90, 30, 160)).save(buf, format="PNG")
    return buf.getvalue()


def _line_item():
    return SimpleNamespace(
        line_number=1,
        item_name="Consulting",
        description="Advisory services",
        quantity=Decimal("2"),
        unit_price=Decimal("100.00"),
        tax_type="VAT16",
        line_total=Decimal("200.00"),
    )


def _invoice():
    return SimpleNamespace(
        invoice_reference="IN-0001",
        invoice_number="INV-20260609-001",
        customer=SimpleNamespace(display_name="Acme Ltd"),
        transaction_date=date(2026, 6, 9),
        due_date=date(2026, 7, 9),
        currency="KES",
        line_items=[_line_item()],
        subtotal=Decimal("200.00"),
        discount_type=None,
        discount_amount=None,
        discount_percentage=None,
        tax_total=Decimal("32.00"),
        total_due=Decimal("232.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("232.00"),
        notes=None,
    )


def _owner():
    return OwnerInfo(
        full_name="Priori Technologies", logo_storage_key="owner/logo/x.png"
    )


class TestPdfLogoEmbed:
    def test_valid_logo_is_embedded(self):
        gen = DocumentPDFGenerator()
        with_logo = gen.generate_invoice_pdf(
            _invoice(), owner=_owner(), logo_bytes=_png_bytes()
        )
        without_logo = gen.generate_invoice_pdf(_invoice(), owner=_owner())
        assert with_logo.startswith(b"%PDF")
        # The embedded raster makes the logo PDF materially larger.
        assert len(with_logo) > len(without_logo)

    def test_corrupt_logo_falls_back_to_text_header(self):
        gen = DocumentPDFGenerator()
        pdf = gen.generate_invoice_pdf(
            _invoice(), owner=_owner(), logo_bytes=b"not-an-image"
        )
        assert pdf.startswith(b"%PDF")

    def test_no_logo_bytes_renders_text_header(self):
        gen = DocumentPDFGenerator()
        pdf = gen.generate_invoice_pdf(_invoice(), owner=_owner(), logo_bytes=None)
        assert pdf.startswith(b"%PDF")
