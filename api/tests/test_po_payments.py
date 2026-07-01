"""payment & balance tests.

Exercises the record_payment / _apply_payment logic against the real
service using a lightweight locked-row stub, so the balance arithmetic,
overpayment guard, auto-settle-to-PAID transition (via the shared state
machine) and single version bump are all asserted without a live DB. The
Postgres-guarded suite (gate H#1) additionally exercises the CHECK
constraints and the FOR UPDATE concurrency path end-to-end.

Also covers:
- is_paid false-positive fix: a zero-total DRAFT PO must NOT be considered
  paid (Finding 1).
- document_id wiring: recording a payment with a valid document_id links it;
  an invalid/foreign document_id is rejected (Finding 3).
- paid bucket in status counts (Finding 2) — see test_po_status_counts.py.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.common.exceptions import BadRequestException, NotFoundException
from app.constants.enums import PurchaseOrderStatus
from app.modules.purchase_orders.models import PurchaseOrder
from app.modules.purchase_orders.schemas import PurchaseOrderPaymentCreate
from app.modules.purchase_orders.service import PurchaseOrderService


class _FakePO:
    """Stand-in for a locked PurchaseOrder row.

    ``is_paid`` delegates to the real ``PurchaseOrder.is_paid`` property
    descriptor so the tests exercise the production logic directly — a
    regression in models.py is caught rather than masked by a hand-copied
    expression.
    """

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

    @property
    def is_paid(self) -> bool:
        # Invoke the real property descriptor against this stub so the
        # production condition in models.py is what is actually asserted.
        return PurchaseOrder.is_paid.fget(self)


class _CapturingSession:
    """Minimal session capturing added objects; flush/no-op."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    def flush(self) -> None:
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
        PurchaseOrderPaymentCreate(
            amount=Decimal("400.00"),
            paymentDate=date.today(),
            reference="TXN-TEST",
            currency="KES",
            exchangeRate=Decimal("1"),
        ),
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
        PurchaseOrderPaymentCreate(
            amount=Decimal("1000.00"),
            paymentDate=date.today(),
            reference="TXN-TEST",
            currency="KES",
            exchangeRate=Decimal("1"),
        ),
    )

    assert po.balance_due == Decimal("0.00")
    assert po.status == PurchaseOrderStatus.PAID
    assert po.paid_at is not None
    assert po.version == 2  # state-machine bump only, exactly once


def test_overpayment_accepted(monkeypatch) -> None:
    _patch_side_effects(monkeypatch)
    po = _FakePO("1000.00")
    service = _service_with(po)

    service.record_payment(
        po.id,
        PurchaseOrderPaymentCreate(
            amount=Decimal("1000.01"),
            paymentDate=date.today(),
            reference="TXN-TEST",
            currency="KES",
            exchangeRate=Decimal("1"),
        ),
    )
    # Overpayment is recorded: balance_due goes negative (credit owed back)
    # and the document settles to PAID.
    assert po.amount_paid == Decimal("1000.01")
    assert po.balance_due == Decimal("-0.01")
    assert po.status == PurchaseOrderStatus.PAID


def test_payment_on_draft_rejected(monkeypatch) -> None:
    _patch_side_effects(monkeypatch)
    po = _FakePO("1000.00", status=PurchaseOrderStatus.DRAFT)
    service = _service_with(po)

    with pytest.raises(BadRequestException):
        service.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=Decimal("100.00"),
                paymentDate=date.today(),
                reference="TXN-TEST",
                currency="KES",
                exchangeRate=Decimal("1"),
            ),
        )


def test_payment_on_already_paid_accepted(monkeypatch) -> None:
    _patch_side_effects(monkeypatch)
    po = _FakePO("1000.00", status=PurchaseOrderStatus.PAID)
    po.balance_due = Decimal("0.00")
    po.amount_paid = Decimal("1000.00")
    service = _service_with(po)

    # This app records payments; a further payment on a settled document is
    # accepted and drives balance_due negative (credit owed back).
    service.record_payment(
        po.id,
        PurchaseOrderPaymentCreate(
            amount=Decimal("1.00"),
            paymentDate=date.today(),
            reference="TXN-TEST",
            currency="KES",
            exchangeRate=Decimal("1"),
        ),
    )
    assert po.amount_paid == Decimal("1001.00")
    assert po.balance_due == Decimal("-1.00")
    assert po.status == PurchaseOrderStatus.PAID


# IS_PAID FALSE-POSITIVE FIX (Finding 1)


class TestIsPaidProperty:
    """is_paid must not return True for a zero-total DRAFT PO.

    A DRAFT PO with total=0 has balance_due=0 but has never been sent or
    had a payment recorded. The old condition ``balance_due <= 0`` would
    incorrectly mark it as paid. The fix requires an explicit PAID status
    or a SENT PO with a cleared balance.
    """

    def _make_po(self, status: str, total: str = "0.00") -> _FakePO:
        po = _FakePO(total, status=status)
        po.balance_due = Decimal("0.00")
        return po

    def test_draft_zero_total_is_not_paid(self) -> None:
        """A DRAFT PO with zero total must NOT be considered paid."""
        po = self._make_po(PurchaseOrderStatus.DRAFT, "0.00")
        assert not po.is_paid, (
            "A zero-total DRAFT PO must not be considered paid — "
            "it has never been sent and no payment has been recorded."
        )

    def test_sent_zero_balance_is_paid(self) -> None:
        """A SENT PO with balance_due=0 IS considered paid (settled)."""
        po = self._make_po(PurchaseOrderStatus.SENT, "100.00")
        assert po.is_paid

    def test_explicit_paid_status_is_paid(self) -> None:
        """A PO in PAID status is always considered paid."""
        po = self._make_po(PurchaseOrderStatus.PAID, "100.00")
        assert po.is_paid

    def test_draft_nonzero_balance_is_not_paid(self) -> None:
        """A DRAFT PO with a non-zero balance is not paid."""
        po = _FakePO("500.00", status=PurchaseOrderStatus.DRAFT)
        assert not po.is_paid


# DOCUMENT_ID WIRING (Finding 3)


class _FakeDocument:
    """Minimal PurchaseOrderDocument stand-in."""

    def __init__(self, doc_id: uuid.UUID, po_id: str) -> None:
        self.id = doc_id
        self.po_id = po_id
        self.source = "payment_modal"


def _service_with_document(
    po: _FakePO, document: _FakeDocument | None
) -> PurchaseOrderService:
    """Build a service that stubs both _get_locked and get_document."""
    service = _service_with(po)

    def _get_document(po_id, doc_id):
        # Scoped lookup: the document must exist, its id must match, AND it
        # must belong to the requested PO. A foreign document (matching id
        # but different po_id) is rejected just like a missing one.
        if (
            document is None
            or str(document.id) != str(doc_id)
            or str(document.po_id) != str(po_id)
        ):
            raise NotFoundException(
                detail=f"Document '{doc_id}' not found on purchase order '{po_id}'",
                resource="purchase_order_document",
            )
        return document

    service.get_document = _get_document  # type: ignore[method-assign]
    return service


class TestRecordPaymentDocumentId:
    """document_id wiring: valid doc links; invalid/foreign doc is rejected."""

    def test_valid_document_id_is_linked(self, monkeypatch) -> None:
        """A valid document_id belonging to the same PO is accepted and linked."""
        _patch_side_effects(monkeypatch)
        po = _FakePO("1000.00")
        doc_id = uuid.uuid4()
        doc = _FakeDocument(doc_id, po.id)
        service = _service_with_document(po, doc)

        payment = service.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=Decimal("400.00"),
                paymentDate=date.today(),
                reference="TXN-TEST",
                currency="KES",
                exchangeRate=Decimal("1"),
                documentId=doc_id,
            ),
        )

        assert payment.document_id == doc_id

    def test_no_document_id_is_accepted(self, monkeypatch) -> None:
        """Omitting document_id is valid — the field is optional."""
        _patch_side_effects(monkeypatch)
        po = _FakePO("1000.00")
        service = _service_with_document(po, None)

        payment = service.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=Decimal("400.00"),
                paymentDate=date.today(),
                reference="TXN-TEST",
                currency="KES",
                exchangeRate=Decimal("1"),
            ),
        )

        assert payment.document_id is None

    def test_foreign_document_id_is_rejected(self, monkeypatch) -> None:
        """A document_id that belongs to a DIFFERENT PO is rejected with 404.

        The document exists and its id matches the requested doc_id, but its
        po_id points at another purchase order, so the scoped lookup must
        still reject it. This is distinct from the nonexistent-document case.
        """
        _patch_side_effects(monkeypatch)
        po = _FakePO("1000.00")
        foreign_doc_id = uuid.uuid4()
        # The document exists (same id) but belongs to a different PO.
        foreign_doc = _FakeDocument(foreign_doc_id, po_id="a-different-po")
        service = _service_with_document(po, foreign_doc)

        with pytest.raises(NotFoundException):
            service.record_payment(
                po.id,
                PurchaseOrderPaymentCreate(
                    amount=Decimal("400.00"),
                    paymentDate=date.today(),
                    reference="TXN-TEST",
                    currency="KES",
                    exchangeRate=Decimal("1"),
                    documentId=foreign_doc_id,
                ),
            )

    def test_nonexistent_document_id_is_rejected(self, monkeypatch) -> None:
        """A document_id that does not exist at all is rejected with 404."""
        _patch_side_effects(monkeypatch)
        po = _FakePO("1000.00")
        service = _service_with_document(po, None)

        with pytest.raises(NotFoundException):
            service.record_payment(
                po.id,
                PurchaseOrderPaymentCreate(
                    amount=Decimal("400.00"),
                    paymentDate=date.today(),
                    reference="TXN-TEST",
                    currency="KES",
                    exchangeRate=Decimal("1"),
                    documentId=uuid.uuid4(),
                ),
            )
