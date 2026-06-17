"""Cancel lifecycle + state-machine enforcement.

Cancel re-reads the row FOR UPDATE and routes through the shared
_transition() guard, so the suite is Postgres-guarded (DoD H1 — the SQLite
fallback does not exercise FOR UPDATE).

The forbidden-transition matrix:
    DRAFT     -> CANCELED   allowed
    SENT      -> CANCELED   allowed
    BILLED    -> CANCELED   rejected (terminal)
    CANCELED  -> CANCELED   rejected (terminal / already canceled)
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.common.audit import AuditEvent
from app.common.exceptions import BadRequestException, NotFoundException
from app.constants.enums import Currency, PurchaseOrderStatus, TaxType, VendorStatus
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="Cancel re-reads the row FOR UPDATE — a Postgres-only path.",
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


def _draft_po(db):
    service = PurchaseOrderService(db)
    return service.create(
        PurchaseOrderCreate(
            vendor_id=_vendor(db).id,
            order_date=date.today(),
            line_items=[_line_item()],
        )
    )


# ALLOWED TRANSITIONS


def test_cancel_draft_transitions_to_canceled(db):
    service = PurchaseOrderService(db)
    po = _draft_po(db)
    before_version = po.version

    canceled = service.cancel(po.id)

    assert canceled.status == PurchaseOrderStatus.CANCELED
    assert canceled.version == before_version + 1


def test_cancel_sent_transitions_to_canceled(db):
    service = PurchaseOrderService(db)
    po = _draft_po(db)
    po.status = PurchaseOrderStatus.SENT
    db.flush()

    canceled = service.cancel(po.id)
    assert canceled.status == PurchaseOrderStatus.CANCELED


def test_cancel_writes_audit_before_after_image(db):
    service = PurchaseOrderService(db)
    po = _draft_po(db)

    service.cancel(po.id)

    audit = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "purchase_order",
            AuditEvent.entity_id == po.id,
            AuditEvent.action == "canceled",
        )
        .first()
    )
    assert audit is not None
    assert audit.before["status"] == PurchaseOrderStatus.DRAFT.value
    assert audit.after["status"] == PurchaseOrderStatus.CANCELED.value


def test_canceled_po_is_not_editable(db):
    """A canceled PO is terminal: it can no longer be edited."""
    service = PurchaseOrderService(db)
    po = _draft_po(db)
    service.cancel(po.id)

    reloaded = service.get_by_id(po.id)
    assert reloaded.is_editable is False


# FORBIDDEN TRANSITIONS (no silent no-op)


def test_cancel_billed_rejected(db):
    service = PurchaseOrderService(db)
    po = _draft_po(db)
    po.status = PurchaseOrderStatus.BILLED
    db.flush()

    with pytest.raises(BadRequestException) as exc_info:
        service.cancel(po.id)
    assert exc_info.value.status_code == 400

    db.refresh(po)
    assert po.status == PurchaseOrderStatus.BILLED  # unchanged


def test_cancel_already_canceled_rejected(db):
    service = PurchaseOrderService(db)
    po = _draft_po(db)
    service.cancel(po.id)

    with pytest.raises(BadRequestException) as exc_info:
        service.cancel(po.id)
    assert exc_info.value.status_code == 400


def test_cancel_missing_po_raises_not_found(db):
    service = PurchaseOrderService(db)
    with pytest.raises(NotFoundException):
        service.cancel(uuid.uuid4())
