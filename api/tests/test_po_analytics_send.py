"""send-path analytics (po_sent / po_send_failed).

Postgres-guarded: the send path re-reads the row FOR UPDATE, captures an
owner snapshot and drains the outbox with SKIP LOCKED — Postgres-only paths
(mirrors test_purchase_order_send.py).

SES is never really called: app.lib.email.email_service.send_document_email is
monkeypatched to succeed or fail deterministically.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import app.common.analytics as analytics
import app.lib.email as email_module
from app.common.analytics import SEND_ERROR_PROVIDER_UNAVAILABLE
from app.constants.enums import Currency, TaxType, VendorStatus
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


@pytest.fixture
def captured_events(monkeypatch):
    events: list[tuple[str, dict]] = []

    def _capture(event_name: str, properties: dict) -> None:
        events.append((event_name, properties))

    monkeypatch.setattr(analytics, "_dispatch", _capture, raising=True)
    return events


def _patch_ses(monkeypatch, *, fail: bool):
    def _send(*_args, **_kwargs):
        if fail:
            raise RuntimeError("Service Unavailable")
        return {"MessageId": "test-message-id"}

    monkeypatch.setattr(
        email_module.email_service, "send_document_email", _send, raising=True
    )


def _vendor(db, *, email="acme@vendor.test") -> Vendor:
    vendor = Vendor(
        vendor_name="Acme Supplies",
        email=email,
        currency=Currency.KES,
        status=VendorStatus.ACTIVE,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _draft_po(db, vendor):
    payload = PurchaseOrderCreate(
        vendor_id=vendor.id,
        order_date=date.today(),
        line_items=[
            PurchaseOrderLineItemCreate(
                item_name="Steel bolts M8",
                description="Box of 500",
                quantity=Decimal("2"),
                unit_price=Decimal("100.00"),
                tax_type=TaxType.VAT_16,
            )
        ],
    )
    return PurchaseOrderService(db).create(payload)


def _only(events, name):
    matches = [props for ev, props in events if ev == name]
    assert len(matches) == 1, f"expected exactly one {name}, got {len(matches)}"
    return matches[0]


def test_successful_send_emits_po_sent_without_raw_email(
    db, monkeypatch, captured_events
):
    _patch_ses(monkeypatch, fail=False)
    vendor = _vendor(db)
    po = _draft_po(db, vendor)
    db.commit()
    captured_events.clear()

    PurchaseOrderService(db).send_purchase_order(po.id)

    props = _only(captured_events, "po_sent")
    assert props == {
        "po_id": str(po.id),
        "vendor_id": str(vendor.id),
        "recipient_email_present": True,
    }
    # Defence in depth: no property value is a raw email address.
    assert all("@" not in str(v) for v in props.values())
    # The failure event must NOT fire on the happy path.
    assert not any(ev == "po_send_failed" for ev, _ in captured_events)


def test_failed_first_attempt_emits_sanitized_po_send_failed(
    db, monkeypatch, captured_events
):
    _patch_ses(monkeypatch, fail=True)
    vendor = _vendor(db)
    po = _draft_po(db, vendor)
    db.commit()
    captured_events.clear()

    # The PO is still SENT and the email durably queued; the first attempt
    # failed, so po_send_failed fires with a sanitized category.
    PurchaseOrderService(db).send_purchase_order(po.id)

    props = _only(captured_events, "po_send_failed")
    assert props["po_id"] == str(po.id)
    assert props["error_reason"] == SEND_ERROR_PROVIDER_UNAVAILABLE
    # error_reason is a category, never a raw trace/message.
    assert "@" not in props["error_reason"]
    assert not any(ev == "po_sent" for ev, _ in captured_events)
