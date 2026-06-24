"""Analytics instrumentation.

These tests prove the acceptance criteria with automated checks:

- emission is centralised through ``emit_event`` and is fire-and-forget — a
  failing sink can never break or fail the business action ;
- ``po_send_failed.error_reason`` is a sanitised category, not a raw trace;
- no PII (raw email) is ever carried in event properties;
- the documented events fire with the documented, PII-free properties.

The analytics sink is captured by monkeypatching ``analytics._dispatch`` so no
log parsing is needed and the exact (event, properties) pairs are asserted.
Pure-logic tests run without a database; the service-level emit tests use the
standard ``db`` fixture (Postgres-guarded where they exercise send paths).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import ClassVar

import pytest

import app.common.analytics as analytics
from app.common.analytics import (
    SEND_ERROR_PROVIDER_UNAVAILABLE,
    SEND_ERROR_RECIPIENT_REJECTED,
    SEND_ERROR_THROTTLED,
    SEND_ERROR_TIMEOUT,
    SEND_ERROR_UNKNOWN,
    PurchaseOrderEvent,
    emit_event,
    sanitize_error_reason,
)
from app.constants.enums import Currency, TaxType, VendorStatus
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor

# CAPTURE HELPER


@pytest.fixture
def captured_events(monkeypatch):
    """Capture every dispatched (event_name, properties) pair."""
    events: list[tuple[str, dict]] = []

    def _capture(event_name: str, properties: dict) -> None:
        events.append((event_name, properties))

    monkeypatch.setattr(analytics, "_dispatch", _capture, raising=True)
    return events


def _only(events, name):
    """Return the single properties dict for events of ``name``."""
    matches = [props for ev, props in events if ev == name]
    assert len(matches) == 1, f"expected exactly one {name}, got {len(matches)}"
    return matches[0]


# PURE-LOGIC TESTS  (no DB)


class TestEventCatalogue:
    # The 14 PRD §17 events, plus the two v1-lifecycle additions
    # (PO_PAYMENT_RECORDED from PO-19, PO_MARKED_SENT from PO-20).
    _PRD_EVENTS: ClassVar[set[str]] = {
        "po_created",
        "po_saved",
        "po_viewed",
        "po_sent",
        "po_send_failed",
        "po_converted_to_bill",
        "po_pdf_downloaded",
        "po_duplicated",
        "po_cancelled",
        "po_deleted",
        "po_document_attached",
        "po_document_downloaded",
        "po_document_deleted",
        "po_list_exported",
    }
    _LIFECYCLE_ADDITIONS: ClassVar[set[str]] = {
        "po_payment_recorded",
        "po_marked_sent",
        "po_payments_exported",
    }

    def test_prd_catalogue_is_complete(self):
        defined = {ev.value for ev in PurchaseOrderEvent}
        assert defined >= self._PRD_EVENTS

    def test_event_enum_is_exactly_prd_plus_lifecycle(self):
        defined = {ev.value for ev in PurchaseOrderEvent}
        assert defined == self._PRD_EVENTS | self._LIFECYCLE_ADDITIONS

    def test_event_names_are_snake_case_constants(self):
        for ev in PurchaseOrderEvent:
            assert ev.value == ev.value.lower()
            assert " " not in ev.value


class TestFireAndForget:
    """emit_event must never raise, whatever the sink does."""

    def test_sink_exception_is_swallowed(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("sink down")

        monkeypatch.setattr(analytics, "_dispatch", _boom, raising=True)
        # Must not raise.
        emit_event(PurchaseOrderEvent.PO_VIEWED, {"po_id": "x"})

    def test_unserialisable_payload_does_not_raise(self, monkeypatch):
        # Even if the scrub/dispatch sees something odd, no exception escapes.
        emit_event(PurchaseOrderEvent.PO_VIEWED, {"po_id": object()})

    def test_none_properties_ok(self, captured_events):
        emit_event(PurchaseOrderEvent.PO_VIEWED, None)
        assert captured_events == [("po_viewed", {})]


class TestSanitizeErrorReason:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Connection timed out after 30s", SEND_ERROR_TIMEOUT),
            ("Throttling: rate exceeded", SEND_ERROR_THROTTLED),
            (
                "Recipient address rejected: mailbox unavailable",
                SEND_ERROR_RECIPIENT_REJECTED,
            ),
            ("Service Unavailable", SEND_ERROR_PROVIDER_UNAVAILABLE),
            ("some entirely novel boom", SEND_ERROR_UNKNOWN),
            (None, SEND_ERROR_UNKNOWN),
        ],
    )
    def test_categories(self, raw, expected):
        assert sanitize_error_reason(raw) == expected

    def test_accepts_exception_object(self):
        assert sanitize_error_reason(TimeoutError("timed out")) == SEND_ERROR_TIMEOUT

    def test_never_echoes_raw_message_or_address(self):
        """The returned category must not embed the raw text / address."""
        raw = "delivery to victim@example.com failed: mailbox rejected"
        category = sanitize_error_reason(raw)
        assert "@" not in category
        assert "victim" not in category
        assert category == SEND_ERROR_RECIPIENT_REJECTED


class TestNoPII:
    def test_email_value_is_redacted(self, captured_events):
        emit_event(
            PurchaseOrderEvent.PO_SENT,
            {"po_id": "1", "leaked": "vendor@example.com"},
        )
        props = _only(captured_events, "po_sent")
        assert props["leaked"] == "<redacted>"
        assert props["po_id"] == "1"

    def test_non_email_strings_pass_through(self, captured_events):
        emit_event(PurchaseOrderEvent.PO_VIEWED, {"po_id": "PO-000001"})
        assert _only(captured_events, "po_viewed")["po_id"] == "PO-000001"


# SERVICE-LEVEL EMIT TESTS


def _vendor(db, *, email="acme@vendor.test", status=VendorStatus.ACTIVE) -> Vendor:
    vendor = Vendor(
        vendor_name="Acme Supplies",
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


def _create_po(db, vendor):
    payload = PurchaseOrderCreate(
        vendor_id=vendor.id,
        order_date=date.today(),
        line_items=[_line_item(), _line_item(item_name="Nuts M8")],
    )
    return PurchaseOrderService(db).create(payload)


class TestCreateEmitsPoCreated:
    def test_po_created_carries_documented_properties(self, db, captured_events):
        vendor = _vendor(db)
        po = _create_po(db, vendor)

        props = _only(captured_events, "po_created")
        assert props == {
            "po_id": str(po.id),
            "vendor_id": str(vendor.id),
            "currency": "KES",
            "item_count": 2,
            "recurring": False,
        }

    def test_create_succeeds_even_when_emit_raises(self, db, monkeypatch):
        """self-test: a failing emitter cannot fail the create."""

        def _boom(*_a, **_k):
            raise RuntimeError("sink down")

        # Force the underlying sink to raise; emit_event must still swallow it.
        monkeypatch.setattr(analytics, "_dispatch", _boom, raising=True)

        vendor = _vendor(db)
        po = _create_po(db, vendor)  # must not raise
        assert po.id is not None


class TestCancelDeleteDuplicateEmit:
    def test_delete_emits_po_deleted(self, db, captured_events):
        vendor = _vendor(db)
        po = _create_po(db, vendor)
        db.commit()
        captured_events.clear()

        PurchaseOrderService(db).delete(po.id)
        assert _only(captured_events, "po_deleted") == {"po_id": str(po.id)}

    def test_duplicate_emits_po_duplicated(self, db, captured_events):
        vendor = _vendor(db)
        po = _create_po(db, vendor)
        db.commit()
        captured_events.clear()

        dup = PurchaseOrderService(db).duplicate(po.id)
        props = _only(captured_events, "po_duplicated")
        assert props == {
            "source_po_id": str(po.id),
            "new_po_id": str(dup.id),
        }
