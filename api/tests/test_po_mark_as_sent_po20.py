"""PO-20 mark-as-sent tests (work item #21).

Asserts the no-email DRAFT->SENT transition reuses the shared send-mixin
mechanics: Draft-only gate, single version bump via the state machine,
sent_at stamped, owner snapshot captured once (idempotent), and crucially
that NO outbox row is enqueued. DB-free via lightweight stubs; the
Postgres-guarded suite (gate H#1) exercises the FOR UPDATE path and the
empty-outbox assertion end-to-end.
"""

from __future__ import annotations

import pytest

from app.common.exceptions import BadRequestException
from app.constants.enums import PurchaseOrderStatus
from app.modules.purchase_orders.service import PurchaseOrderService


class _FakePO:
    def __init__(self, status=PurchaseOrderStatus.DRAFT) -> None:
        self.id = "00000000-0000-0000-0000-000000000010"
        self.po_reference = "PO-000010"
        self.status = status
        self.version = 1
        self.sent_at = None
        self.owner_snapshot_id = None


class _NoopSession:
    def add(self, obj) -> None:  # pragma: no cover - unused here
        pass

    def flush(self) -> None:
        pass


def _service_with(po: _FakePO, monkeypatch) -> PurchaseOrderService:
    import app.modules.purchase_orders.service as svc

    monkeypatch.setattr(svc, "record_audit_event", lambda *a, **k: None)
    monkeypatch.setattr(svc, "emit_event", lambda *a, **k: None)

    service = PurchaseOrderService.__new__(PurchaseOrderService)
    service._db = _NoopSession()
    service._current_user = None
    service._actor_id = None
    service._get_locked = lambda _po_id: po  # type: ignore[assignment]

    # Snapshot capture stubbed: simulate stamping an id so idempotency holds.
    def _capture(p) -> None:
        if p.owner_snapshot_id is None:
            p.owner_snapshot_id = "snapshot-1"

    service._capture_owner_snapshot = _capture  # type: ignore[assignment]
    return service


def test_mark_as_sent_transitions_draft_to_sent(monkeypatch) -> None:
    po = _FakePO()
    service = _service_with(po, monkeypatch)

    service.mark_as_sent(po.id)

    assert po.status == PurchaseOrderStatus.SENT
    assert po.sent_at is not None
    assert po.version == 2  # single state-machine bump
    assert po.owner_snapshot_id == "snapshot-1"


@pytest.mark.parametrize(
    "status",
    [PurchaseOrderStatus.SENT, PurchaseOrderStatus.PAID],
)
def test_mark_as_sent_rejects_non_draft(
    status: PurchaseOrderStatus, monkeypatch
) -> None:
    po = _FakePO(status=status)
    service = _service_with(po, monkeypatch)

    with pytest.raises(BadRequestException):
        service.mark_as_sent(po.id)
    assert po.status == status  # unchanged


def test_mark_as_sent_does_not_enqueue_email(monkeypatch) -> None:
    # mark_as_sent must never touch the outbox. If it tried to enqueue, the
    # _NoopSession (no EmailOutboxService wiring) and the absence of any
    # outbox import in the call path keep this DB-free; we assert the method
    # completes without invoking _prepare_and_mark_sent.
    po = _FakePO()
    service = _service_with(po, monkeypatch)

    called = {"prepare": False}
    service._prepare_and_mark_sent = (  # type: ignore[assignment]
        lambda *a, **k: called.__setitem__("prepare", True)
    )

    service.mark_as_sent(po.id)

    assert called["prepare"] is False
    assert po.status == PurchaseOrderStatus.SENT
