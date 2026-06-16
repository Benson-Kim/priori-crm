"""Send PO by email (Draft -> Sent) via the shared transactional outbox.

The send path re-reads the row FOR UPDATE, transitions Draft->Sent, captures
the owner snapshot and enqueues an outbox row in the same transaction before
dispatching outside the lock. These reads/locks and the owner snapshot rows
are Postgres-specific, so the suite is Postgres-guarded (DoD H1 — the SQLite
fallback does not exercise FOR UPDATE / SKIP LOCKED).

SES is never really called: app.lib.email.email_service.send_document_email is
monkeypatched to succeed or fail deterministically.
"""

from datetime import date
from decimal import Decimal

import pytest

import app.lib.email as email_module
from app.common.email_outbox import EmailOutbox, EmailOutboxService, EmailOutboxStatus
from app.common.exceptions import BadRequestException
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
    reason=(
        "PO send re-reads FOR UPDATE, captures an owner snapshot and drains "
        "the outbox with SKIP LOCKED — Postgres-only paths."
    ),
)


# HELPERS


def _vendor(
    db,
    *,
    name="Acme Supplies",
    email="acme@vendor.test",
    status=VendorStatus.ACTIVE,
) -> Vendor:
    vendor = Vendor(
        vendor_name=name,
        email=email,
        currency=Currency.KES,
        status=status,
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


def _draft_po(db, vendor, *, delivery_date=None):
    """Create a real DRAFT PO via the service so references/totals are valid."""
    service = PurchaseOrderService(db)
    payload = PurchaseOrderCreate(
        vendor_id=vendor.id,
        order_date=date.today(),
        delivery_date=delivery_date,
        line_items=[_line_item()],
    )
    return service.create(payload)


def _patch_ses(monkeypatch, *, fail: bool):
    """Force every SES send to succeed or raise, without touching the network."""
    calls = {"n": 0}

    def _send(*_args, **_kwargs):
        calls["n"] += 1
        if fail:
            raise RuntimeError("SES unavailable")
        return {"MessageId": "test-message-id"}

    monkeypatch.setattr(
        email_module.email_service, "send_document_email", _send, raising=True
    )
    return calls


def _outbox_rows(db):
    return db.query(EmailOutbox).all()


# SUCCESS PATH


def test_send_transitions_draft_to_sent_and_delivers(db, monkeypatch):
    """Happy path: Draft->Sent, sent_at stamped, owner snapshot captured,
    outbox row delivered (status 'sent')."""
    _patch_ses(monkeypatch, fail=False)
    vendor = _vendor(db)
    po = _draft_po(db, vendor)
    db.commit()

    service = PurchaseOrderService(db)
    result = service.send_purchase_order(po.id)

    db.refresh(po)
    assert po.status == PurchaseOrderStatus.SENT
    assert po.sent_at is not None
    assert po.owner_snapshot_id is not None
    assert result["sent_to"] == "acme@vendor.test"
    assert "sent successfully" in result["message"]

    rows = _outbox_rows(db)
    assert len(rows) == 1
    assert rows[0].status == EmailOutboxStatus.SENT
    assert rows[0].document_type == "purchase_order"
    assert rows[0].attach_pdf is True


def test_send_recipient_override(db, monkeypatch):
    """An explicit toEmail overrides the vendor email."""
    _patch_ses(monkeypatch, fail=False)
    vendor = _vendor(db)
    po = _draft_po(db, vendor)
    db.commit()

    result = PurchaseOrderService(db).send_purchase_order(
        po.id, to_email="override@vendor.test"
    )
    assert result["sent_to"] == "override@vendor.test"
    assert _outbox_rows(db)[0].recipient == "override@vendor.test"


# VENDOR WITHOUT EMAIL  (Send blocked server-side)


def test_send_rejected_when_vendor_has_no_email(db, monkeypatch):
    """Vendor without an email -> typed 400, PO stays Draft, nothing queued."""
    _patch_ses(monkeypatch, fail=False)
    vendor = _vendor(db, email=None)
    po = _draft_po(db, vendor)
    db.commit()

    with pytest.raises(BadRequestException) as exc_info:
        PurchaseOrderService(db).send_purchase_order(po.id)
    assert exc_info.value.status_code == 400

    db.refresh(po)
    assert po.status == PurchaseOrderStatus.DRAFT
    assert _outbox_rows(db) == []


# SEND ONLY ON DRAFT


def test_send_rejected_when_not_draft(db, monkeypatch):
    """A second send (already SENT) is rejected: resend is unavailable."""
    _patch_ses(monkeypatch, fail=False)
    vendor = _vendor(db)
    po = _draft_po(db, vendor)
    db.commit()

    PurchaseOrderService(db).send_purchase_order(po.id)
    db.refresh(po)
    assert po.status == PurchaseOrderStatus.SENT

    with pytest.raises(BadRequestException) as exc_info:
        PurchaseOrderService(db).send_purchase_order(po.id)
    assert exc_info.value.status_code == 400


# NO SILENT FAILURE  (Gate B): SES failure -> durable FAILED row, drain retries


def test_send_failure_keeps_po_sent_with_durable_failed_row(db, monkeypatch):
    """A failed first SES attempt does not roll back the SENT transition; the
    outbox row is durably FAILED so the action never silently loses the email."""
    _patch_ses(monkeypatch, fail=True)
    vendor = _vendor(db)
    po = _draft_po(db, vendor)
    db.commit()

    result = PurchaseOrderService(db).send_purchase_order(po.id)
    assert "queued for delivery" in result["message"]

    db.refresh(po)
    # The SENT transition committed before dispatch, so it survives the
    # failed send.
    assert po.status == PurchaseOrderStatus.SENT

    rows = _outbox_rows(db)
    assert len(rows) == 1
    assert rows[0].status == EmailOutboxStatus.FAILED
    assert rows[0].attempts == 1
    assert rows[0].last_error


def test_drain_redelivers_failed_po_email(db, monkeypatch):
    """After a failed first attempt the drainer redelivers the PO email once
    SES recovers, flipping the row to SENT (no second PO transition)."""
    fail_calls = _patch_ses(monkeypatch, fail=True)
    vendor = _vendor(db)
    po = _draft_po(db, vendor)
    db.commit()

    PurchaseOrderService(db).send_purchase_order(po.id)
    assert fail_calls["n"] == 1
    row = _outbox_rows(db)[0]
    assert row.status == EmailOutboxStatus.FAILED

    # SES recovers; the drainer retries pending/failed rows.
    _patch_ses(monkeypatch, fail=False)
    summary = EmailOutboxService(db).drain()
    assert summary["delivered"] == 1

    db.refresh(row)
    assert row.status == EmailOutboxStatus.SENT
    assert row.attempts == 2
    assert row.sent_at is not None
