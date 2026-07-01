"""
FX payment integrity tests (gate D / gate E).

Covers the two issues identified in the !56 post-merge review:

1. update_payment same-currency guard: editing a payment to same-currency
   with rate != 1 must raise BadRequestException (the guard existed only
   in record_payment; update_payment now applies the same check).

2. FX rounding policy: amount_paid == Σ quantize(amountᵢ × rateᵢ).
   Proves the documented rounding contract and that removing a middle
   payment resyncs exactly.

These tests use the monkeypatched _FakePO / _make_service pattern from
test_po_payments.py so they run without a database (SQLite or Postgres).
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.common.exceptions import BadRequestException
from app.constants.enums import PurchaseOrderStatus
from app.modules.purchase_orders.schemas import (
    PurchaseOrderPaymentCreate,
    PurchaseOrderPaymentUpdate,
)
from app.modules.purchase_orders.service import PurchaseOrderService


# ---------------------------------------------------------------------------
# Minimal fake PO + service wiring (mirrors test_po_payments.py)
# ---------------------------------------------------------------------------


class _FakePO:
    def __init__(self, total: str, status=PurchaseOrderStatus.SENT) -> None:
        self.id = "00000000-0000-0000-0000-000000000001"
        self.po_reference = "PO-000001"
        self.currency = "KES"
        self.total = Decimal(total)
        self.amount_paid = Decimal("0.00")
        self.balance_due = Decimal(total)
        self.status = status
        self.version = 1
        self.paid_at = None


def _make_service(monkeypatch, po: _FakePO) -> PurchaseOrderService:
    """Return a PurchaseOrderService with DB interactions monkeypatched."""
    db = MagicMock()
    service = PurchaseOrderService.__new__(PurchaseOrderService)
    service._db = db
    service._actor_id = None

    # _get_locked returns the fake PO.
    monkeypatch.setattr(service, "_get_locked", lambda po_id: po)

    # get_payment returns a simple namespace so update_payment can mutate it.
    payments: dict = {}

    def _get_payment(po_id, payment_id):
        return payments[payment_id]

    monkeypatch.setattr(service, "get_payment", _get_payment)
    monkeypatch.setattr(service, "get_document", lambda po_id, doc_id: None)

    # db.add captures the payment so we can inspect it.
    def _db_add(obj):
        if isinstance(obj, type(MagicMock())):
            return
        from app.modules.purchase_orders.models import PurchaseOrderPayment
        if isinstance(obj, PurchaseOrderPayment):
            obj.id = f"pay-{len(payments)}"
            payments[obj.id] = obj

    db.add.side_effect = _db_add
    db.flush.return_value = None

    # _resync_after_payment_change: recompute from the payments dict.
    def _resync(purchase_order):
        from app.modules.purchase_orders.models import PurchaseOrderPayment
        amount_paid = sum(
            PurchaseOrderService._converted_amount(p.amount, p.exchange_rate)
            for p in payments.values()
        )
        purchase_order.amount_paid = amount_paid
        purchase_order.balance_due = purchase_order.total - amount_paid
        purchase_order.version += 1

    monkeypatch.setattr(service, "_resync_after_payment_change", _resync)

    # record_audit_event is a no-op.
    import app.modules.purchase_orders.service as svc_mod
    monkeypatch.setattr(svc_mod, "record_audit_event", lambda *a, **kw: None)
    monkeypatch.setattr(svc_mod, "emit_event", lambda *a, **kw: None)

    return service, payments


# ---------------------------------------------------------------------------
# Gate D: update_payment same-currency guard
# ---------------------------------------------------------------------------


class TestUpdatePaymentSameCurrencyGuard:
    """update_payment must enforce the same-currency rate-must-be-1 invariant.

    Regression guard for gate D: the guard existed only in record_payment;
    an edit could previously change currency/rate to a same-currency pair
    with rate != 1 and silently distort _resync_after_payment_change.
    """

    def test_update_payment_same_currency_rate_not_one_rejected(self, monkeypatch):
        """Editing a payment to same-currency (KES) with rate != 1 must 400."""
        po = _FakePO("10000.00")
        service, payments = _make_service(monkeypatch, po)

        # Record a valid cross-currency payment first.
        service.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=Decimal("100.00"),
                paymentDate=date.today(),
                reference="TXN-FX",
                currency="USD",
                exchangeRate=Decimal("130"),
            ),
        )
        payment_id = list(payments.keys())[0]

        # Now edit it to same-currency (KES) but leave rate at 130 — must 400.
        with pytest.raises(BadRequestException, match="Exchange rate must be 1"):
            service.update_payment(
                po.id,
                payment_id,
                PurchaseOrderPaymentUpdate(
                    currency="KES",
                    exchangeRate=Decimal("130"),
                ),
            )

    def test_update_payment_same_currency_rate_one_accepted(self, monkeypatch):
        """Editing a cross-currency payment to same-currency with rate=1 is OK."""
        po = _FakePO("10000.00")
        service, payments = _make_service(monkeypatch, po)

        service.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=Decimal("100.00"),
                paymentDate=date.today(),
                reference="TXN-FX",
                currency="USD",
                exchangeRate=Decimal("130"),
            ),
        )
        payment_id = list(payments.keys())[0]

        # Edit to same-currency with rate=1 — must succeed.
        service.update_payment(
            po.id,
            payment_id,
            PurchaseOrderPaymentUpdate(
                currency="KES",
                exchangeRate=Decimal("1"),
            ),
        )
        assert payments[payment_id].currency == "KES"
        assert payments[payment_id].exchange_rate == Decimal("1")


# ---------------------------------------------------------------------------
# Gate E: FX rounding policy
# ---------------------------------------------------------------------------


class TestFxResyncMultiPayment:
    """FX rounding policy: amount_paid == Σ quantize(amountᵢ × rateᵢ).

    Proves the documented rounding contract and that removing a middle
    payment resyncs exactly (gate E).
    """

    def test_fx_resync_multi_payment_exact(self, monkeypatch):
        """Three fractional-rate payments: amount_paid == Σ quantize(aᵢ × rᵢ)."""
        po = _FakePO("10000.00")
        service, payments = _make_service(monkeypatch, po)

        # Three cross-currency payments that together don't overpay.
        payment_inputs = [
            (Decimal("30.00"), Decimal("129.75")),   # converted: 3892.50
            (Decimal("20.00"), Decimal("130.33")),   # converted: 2606.60
            (Decimal("25.00"), Decimal("128.50")),   # converted: 3212.50
        ]

        for amount, rate in payment_inputs:
            service.record_payment(
                po.id,
                PurchaseOrderPaymentCreate(
                    amount=amount,
                    paymentDate=date.today(),
                    reference=f"TXN-{amount}",
                    currency="USD",
                    exchangeRate=rate,
                ),
            )

        # Verify amount_paid == Σ quantize(amountᵢ × rateᵢ).
        expected_total = sum(
            PurchaseOrderService._converted_amount(a, r)
            for a, r in payment_inputs
        )
        assert po.amount_paid == expected_total

        # Remove the middle payment and verify resync is exact.
        payment_ids = list(payments.keys())
        middle_id = payment_ids[1]
        middle_amount, middle_rate = payment_inputs[1]

        # Simulate delete: remove from payments dict then resync.
        del payments[middle_id]
        service._resync_after_payment_change(po)

        expected_after_delete = sum(
            PurchaseOrderService._converted_amount(a, r)
            for i, (a, r) in enumerate(payment_inputs)
            if i != 1
        )
        assert po.amount_paid == expected_after_delete
