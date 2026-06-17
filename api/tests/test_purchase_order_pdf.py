"""Purchase-order View/Preview PDF (DocumentPdfRenderer reuse).

The renderer resolves the owner snapshot via the live OwnerService and the
PO is read through Postgres UUID columns / server defaults, so the suite is
Postgres-guarded (DoD H1 — SQLite fallback is not sufficient).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.exceptions import AppException
from app.constants.enums import Currency, PurchaseOrderStatus, TaxType, VendorStatus
from app.modules.owner.models import OwnerProfileSnapshot
from app.modules.purchase_orders.models import PurchaseOrder, PurchaseOrderLineItem
from app.modules.purchase_orders.router import _safe_filename_token
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="PO PDF view reads Postgres UUID columns + owner snapshot rows.",
)


# HELPERS


def _vendor(db, *, name="Acme Supplies", email="acme@vendor.test") -> Vendor:
    vendor = Vendor(
        vendor_name=name,
        email=email,
        address="12 Industrial Rd, Nairobi",
        phone_primary="+254700000000",
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _po(
    db,
    vendor,
    *,
    compliance_ref=None,
    notes=None,
    terms=None,
    delivery_date=None,
    owner_snapshot_id=None,
) -> PurchaseOrder:
    today = date.today()
    po = PurchaseOrder(
        po_number=f"PO-{today.strftime('%Y%m%d')}-001",
        po_reference="PO-000001",
        vendor_id=vendor.id,
        order_date=today,
        delivery_date=delivery_date,
        currency=Currency.KES,
        status=PurchaseOrderStatus.DRAFT,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("16.00"),
        total=Decimal("116.00"),
        compliance_ref=compliance_ref,
        notes=notes,
        terms_and_conditions=terms,
        owner_snapshot_id=owner_snapshot_id,
    )
    db.add(po)
    db.flush()
    db.add(
        PurchaseOrderLineItem(
            po_id=po.id,
            line_number=1,
            item_name="Steel bolts",
            description="Box of 500 grade-8.8 bolts",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            tax_type=TaxType.VAT_16,
            tax_amount=Decimal("16.00"),
            line_total=Decimal("100.00"),
        )
    )
    db.flush()
    return po


# RENDERING


def test_generate_pdf_returns_valid_bytes(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    po = _po(db, vendor, notes="Deliver to warehouse", terms="Net 30")

    pdf = service.generate_pdf(po.id)
    assert isinstance(pdf, bytes)
    assert pdf[:5] == b"%PDF-"  # valid PDF signature
    assert len(pdf) > 500


def test_generate_pdf_for_download_returns_bytes_and_po(db):
    """Loads the PO once and returns it with the bytes (no second query)."""
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    po = _po(db, vendor)

    pdf, returned = service.generate_pdf_for_download(po.id)
    assert pdf[:5] == b"%PDF-"
    assert returned.id == po.id
    assert returned.po_reference == po.po_reference
    assert returned.vendor.vendor_name == vendor.vendor_name


# BRANDING SOURCE (snapshot vs live)


def test_branding_uses_owner_snapshot_when_present(db, monkeypatch):
    """When owner_snapshot_id is set, branding resolves from the snapshot, not
    the live profile — editing the live profile cannot re-brand a sent PO."""
    from app.common import pdf_renderer as renderer_mod

    snapshot = OwnerProfileSnapshot(
        content_hash="po05snapshothash",
        full_name="Snapshot Co Ltd",
        address="1 Snapshot Way",
    )
    db.add(snapshot)
    db.flush()

    vendor = _vendor(db)
    po = _po(db, vendor, owner_snapshot_id=snapshot.id)

    captured = {}

    def _fake_generate(self, purchase_order, owner=None, logo_bytes=None):
        captured["owner_name"] = owner.full_name
        return b"%PDF-fake"

    monkeypatch.setattr(
        "app.common.pdf.DocumentPDFGenerator.generate_purchase_order_pdf",
        _fake_generate,
    )

    renderer_mod.DocumentPdfRenderer(db).render_purchase_order(po)
    assert captured["owner_name"] == "Snapshot Co Ltd"


def test_branding_falls_back_to_live_profile_for_draft(db, monkeypatch):
    """With no snapshot (Draft preview), branding comes from the live owner
    profile (seeded from settings.APP_NAME on first use)."""
    vendor = _vendor(db)
    po = _po(db, vendor, owner_snapshot_id=None)

    captured = {}

    def _fake_generate(self, purchase_order, owner=None, logo_bytes=None):
        captured["owner_name"] = owner.full_name
        return b"%PDF-fake"

    monkeypatch.setattr(
        "app.common.pdf.DocumentPDFGenerator.generate_purchase_order_pdf",
        _fake_generate,
    )

    from app.common import pdf_renderer as renderer_mod

    renderer_mod.DocumentPdfRenderer(db).render_purchase_order(po)
    # Live profile is seeded to the app name when first created.
    assert captured["owner_name"]


# COMPLIANCE REF (included only when set)


def test_compliance_ref_passed_through_when_set(db, monkeypatch):
    vendor = _vendor(db)
    po = _po(db, vendor, compliance_ref="LPO-2026-0042")

    captured = {}

    def _fake_generate(self, purchase_order, owner=None, logo_bytes=None):
        captured["compliance_ref"] = purchase_order.compliance_ref
        return b"%PDF-fake"

    monkeypatch.setattr(
        "app.common.pdf.DocumentPDFGenerator.generate_purchase_order_pdf",
        _fake_generate,
    )
    service = PurchaseOrderService(db)
    service.generate_pdf(po.id)
    assert captured["compliance_ref"] == "LPO-2026-0042"


def test_compliance_ref_absent_when_blank(db, monkeypatch):
    vendor = _vendor(db)
    po = _po(db, vendor, compliance_ref=None)

    captured = {}

    def _fake_generate(self, purchase_order, owner=None, logo_bytes=None):
        captured["compliance_ref"] = purchase_order.compliance_ref
        return b"%PDF-fake"

    monkeypatch.setattr(
        "app.common.pdf.DocumentPDFGenerator.generate_purchase_order_pdf",
        _fake_generate,
    )
    service = PurchaseOrderService(db)
    service.generate_pdf(po.id)
    assert captured["compliance_ref"] is None


def test_pdf_renders_with_delivery_date_and_compliance_ref(db):
    """Full-content path: delivery date + compliance ref both present render
    a valid PDF (the metadata table includes the optional ref row)."""
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    po = _po(
        db,
        vendor,
        compliance_ref="LPO-2026-0042",
        delivery_date=date.today() + timedelta(days=14),
    )
    pdf = service.generate_pdf(po.id)
    assert pdf[:5] == b"%PDF-"


# FILENAME SANITISATION


def test_safe_filename_token_strips_unsafe_chars():
    assert _safe_filename_token("Acme / Supplies Ltd.") == "Acme_Supplies_Ltd"
    assert _safe_filename_token('Bad"name\\path') == "Bad_name_path"
    assert _safe_filename_token("   ") == "vendor"  # empty -> fallback
    assert _safe_filename_token("Clean-Name_1") == "Clean-Name_1"


# ERROR HANDLING (no silent failure)


def test_render_failure_raises_typed_500(db, monkeypatch):
    vendor = _vendor(db)
    po = _po(db, vendor)

    def _boom(self, purchase_order, owner=None, logo_bytes=None):
        raise RuntimeError("reportlab exploded")

    monkeypatch.setattr(
        "app.common.pdf.DocumentPDFGenerator.generate_purchase_order_pdf",
        _boom,
    )
    service = PurchaseOrderService(db)
    with pytest.raises(AppException) as exc_info:
        service.generate_pdf(po.id)
    assert exc_info.value.status_code == 500
    assert "PDF could not be generated" in exc_info.value.detail
