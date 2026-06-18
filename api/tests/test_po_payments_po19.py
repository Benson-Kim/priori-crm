"""PO-19 payment & balance tests (work item #20).

Exercises the record_payment / _apply_payment logic against the real
service using a lightweight locked-row stub, so the balance arithmetic,
overpayment guard, auto-settle-to-PAID transition (via the shared state
machine) and single version bump are all asserted without a live DB. The
Postgres-guarded suite (gate H#1) additionally exercises the CHECK
constraints and the FOR UPDATE concurrency path end-to-end.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.common.exceptions import BadRequestException
from app.constants.enums import PurchaseOrderStatus
from app.modules.purchase_orders.schemas import PurchaseOrderPaymentCreate
from app.modules.purchase_orders.service import PurchaseOrderService


class _FakePO:
    """Stand-in for a locked PurchaseOrder row."""

    def __init__(self, total: str, status=PurchaseOrderStatus.SENT) -> None:
        self.id = "00000000-0000-0000-0000-000000000001"
        self.po_reference = "PO-000001"
        self.total = Decimal(total)
        self.amount_paid = Decimal("0.00")
        self.balance_due = Decimal(total)
        self.status = status
        self.version = 1
        self.paid_at = None


class _CapturingSession:
    """Minimal session capturing added objects; flush/no-op."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    def flush(self) -> None:  # noqa: D401 - no-op for the unit test
        pass


def _service_with(po: _FakePO) -> PurchaseOrderService:
    service = PurchaseOrderService.__new__(PurchaseOrderService)
    service._db = _CapturingSession()
    service._current_user = None
    service._actor_id = None
    # Stub the locked load + audit + analytics so the unit test stays DB-free.
    service._get_locked = lambda _po_id: po  # type: ignore[assignment]
    return service


def _patch_side_effects(monkeypatch) -> None:
    import app.modules.purchase_orders.service as svc

    monkeypatch.setattr(svc, "record_audit_event", lambda *a, **k: None)
    monkeypatch.setattr(svc, "emit_event", lambda *a, **k: None)


def test_partial_payment_reduces_balance_and_bumps_version(monkeypatch) -> None:
    _patch_side_effects(monkeypatch)
    po = _FakePO("1000.00")
    service = _service_with(po)

    payment = service.record_payment(
        po.id,
        PurchaseOrderPaymentCreate(amount=Decimal("400.00"), paymentDate=date.today()),
    )

    assert po.amount_paid == Decimal("400.00")
    assert po.balance_due == Decimal("600.00")
    assert po.status == PurchaseOrderStatus.SENT  # not settled yet
    assert po.version == 2  # single bump
    assert payment.amount == Decimal("400.00")
    assert po.paid_at is None


def test_full_payment_settles_to_paid(monkeypatch) -> None:
    _patch_side_effects(monkeypatch)
    po = _FakePO("1000.00")
    service = _service_with(po)

    service.record_payment(
        po.id,
        PurchaseOrderPaymentCreate(amount=Decimal("1000.00"), paymentDate=date.today()),
    )

    assert po.balance_due == Decimal("0.00")
    assert po.status == PurchaseOrderStatus.PAID
    assert po.paid_at is not None
    assert po.version == 2  # state-machine bump only, exactly once


def test_overpayment_rejected(monkeypatch) -> None:
    _patch_side_effects(monkeypatch)
    po = _FakePO("1000.00")
    service = _service_with(po)

    with pytest.raises(BadRequestException):
        service.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=Decimal("1000.01"), paymentDate=date.today()
            ),
        )
    # Unchanged after rejection.
    assert po.amount_paid == Decimal("0.00")
    assert po.status == PurchaseOrderStatus.SENT


def test_payment_on_draft_rejected(monkeypatch) -> None:
    _patch_side_effects(monkeypatch)
    po = _FakePO("1000.00", status=PurchaseOrderStatus.DRAFT)
    service = _service_with(po)

    with pytest.raises(BadRequestException):
        service.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=Decimal("100.00"), paymentDate=date.today()
            ),
        )


def test_payment_on_already_paid_rejected(monkeypatch) -> None:
    _patch_side_effects(monkeypatch)
    po = _FakePO("1000.00", status=PurchaseOrderStatus.PAID)
    po.balance_due = Decimal("0.00")
    po.amount_paid = Decimal("1000.00")
    service = _service_with(po)

    with pytest.raises(BadRequestException):
        service.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=Decimal("1.00"), paymentDate=date.today()
            ),
        )
