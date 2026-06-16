"""Duplicate a purchase order as a new DRAFT (PRD §6.10 / §14).

The duplicate runs through the shared reference generator (advisory lock)
and the _with_reference_retry SAVEPOINT loop, both Postgres-only, so the
suite is Postgres-guarded (DoD H1).
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.exceptions import NotFoundException
from app.constants.enums import (
    Currency,
    DocumentSource,
    PurchaseOrderStatus,
    TaxType,
    VendorStatus,
)
from app.modules.purchase_orders.models import (
    PurchaseOrder,
    PurchaseOrderDocument,
)
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES,
    reason=(
        "Duplicate uses the advisory-locked reference generator and a "
        "SAVEPOINT retry loop — Postgres-only paths."
    ),
)


# HELPERS


def _vendor(db, *, name="Acme Supplies", email="acme@vendor.test") -> Vendor:
    vendor = Vendor(
        vendor_name=name,
        email=email,
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _line_item(**kw) -> PurchaseOrderLineItemCreate:
    base = {
        "item_name": "Steel bolts M8",
        "description": "Box of 500",
        "quantity": Decimal("2"),
        "unit_price": Decimal("100.00"),
        "tax_type": TaxType.VAT_16,
    }
    base.update(kw)
    return PurchaseOrderLineItemCreate(**base)


def _create_po(service, vendor, **kw):
    base = {
        "vendor_id": vendor.id,
        "order_date": date.today(),
        "line_items": [_line_item(), _line_item(item_name="Washers")],
        "compliance_ref": "LPO-2026-0042",
        "notes": "Deliver to the Upper Hill warehouse.",
        "terms_and_conditions": "Net 30.",
        "delivery_date": date.today() + timedelta(days=14),
    }
    base.update(kw)
    return service.create(PurchaseOrderCreate(**base))


# DUPLICATE


def test_duplicate_creates_new_draft_with_fresh_references(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    original = _create_po(service, vendor)

    dup = service.duplicate(original.id)

    assert dup.id != original.id
    assert dup.status == PurchaseOrderStatus.DRAFT
    assert dup.version == 1
    # Fresh, distinct references — never reused.
    assert dup.po_reference != original.po_reference
    assert dup.po_number != original.po_number


def test_duplicate_copies_content_and_resets_order_date(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    original = _create_po(service, vendor, order_date=date(2025, 1, 1))

    dup = service.duplicate(original.id)

    assert dup.vendor_id == original.vendor_id
    assert dup.currency == original.currency
    assert dup.notes == original.notes
    assert dup.terms_and_conditions == original.terms_and_conditions
    assert dup.delivery_date == original.delivery_date
    assert dup.subtotal == original.subtotal
    assert dup.tax_total == original.tax_total
    assert dup.total == original.total
    # order_date reset to today (not the original's date).
    assert dup.order_date == date.today()

    # Line items copied faithfully.
    assert len(dup.line_items) == len(original.line_items)
    orig_names = sorted(li.item_name for li in original.line_items)
    dup_names = sorted(li.item_name for li in dup.line_items)
    assert dup_names == orig_names


def test_duplicate_clears_compliance_ref(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    original = _create_po(service, vendor, compliance_ref="LPO-2026-0042")

    dup = service.duplicate(original.id)
    assert original.compliance_ref == "LPO-2026-0042"
    assert dup.compliance_ref is None


def test_duplicate_does_not_copy_documents(db):
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    original = _create_po(service, vendor)

    # Attach a document to the original.
    db.add(
        PurchaseOrderDocument(
            po_id=original.id,
            filename="quote.pdf",
            file_size_bytes=1024,
            mime_type="application/pdf",
            storage_key="po/docs/quote.pdf",
            source=DocumentSource.FORM,
        )
    )
    db.flush()

    dup = service.duplicate(original.id)

    dup_docs = (
        db.query(PurchaseOrderDocument)
        .filter(PurchaseOrderDocument.po_id == dup.id)
        .all()
    )
    assert dup_docs == []


def test_duplicate_available_at_any_status(db):
    """Duplicate is allowed regardless of the original's status."""
    service = PurchaseOrderService(db)
    vendor = _vendor(db)
    original = _create_po(service, vendor)
    # Force a non-Draft status directly (Send/Convert tested elsewhere).
    original.status = PurchaseOrderStatus.BILLED
    db.flush()

    dup = service.duplicate(original.id)
    assert dup.status == PurchaseOrderStatus.DRAFT


def test_duplicate_missing_po_raises_not_found(db):
    service = PurchaseOrderService(db)
    with pytest.raises(NotFoundException):
        service.duplicate(uuid.uuid4())
