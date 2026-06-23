"""PO-18 lifecycle tests: DRAFT -> SENT -> PAID.

Proves work item #19 acceptance criteria:
- enum is exactly DRAFT/SENT/PAID (no billed/canceled);
- ALLOWED_TRANSITIONS exposes only DRAFT->SENT and SENT->PAID, with the
  shared state machine raising on every illegal edge (no hand-rolled status
  writes);
- delete is DRAFT-only.

The state-machine edges are asserted directly against the transition table
and via self._transition so the test does not depend on a live DB; the
Postgres-guarded suite (gate H#1) additionally exercises the CHECK
constraint accepting 'paid' once the migration is applied.
"""

from __future__ import annotations

import pytest

from app.common.exceptions import BadRequestException
from app.constants.enums import PurchaseOrderStatus
from app.modules.purchase_orders.service import (
    _DELETABLE_STATUSES,
    PurchaseOrderService,
)


def test_enum_is_exactly_draft_sent_paid() -> None:
    # The PO lifecycle has exactly three states; billed/canceled are gone.
    members = set(PurchaseOrderStatus)
    assert members == {
        PurchaseOrderStatus.DRAFT,
        PurchaseOrderStatus.SENT,
        PurchaseOrderStatus.PAID,
    }
    assert PurchaseOrderStatus.PAID.value == "paid"
    assert not hasattr(PurchaseOrderStatus, "BILLED")
    assert not hasattr(PurchaseOrderStatus, "CANCELED")


def test_allowed_transitions_table_is_draft_sent_paid_only() -> None:
    table = PurchaseOrderService.ALLOWED_TRANSITIONS
    assert table[PurchaseOrderStatus.DRAFT] == [PurchaseOrderStatus.SENT]
    assert table[PurchaseOrderStatus.SENT] == [PurchaseOrderStatus.PAID]
    # PAID is terminal.
    assert table[PurchaseOrderStatus.PAID] == []


@pytest.mark.parametrize(
    "frm, to",
    [
        (PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.PAID),
        (PurchaseOrderStatus.SENT, PurchaseOrderStatus.DRAFT),
        (PurchaseOrderStatus.PAID, PurchaseOrderStatus.SENT),
        (PurchaseOrderStatus.PAID, PurchaseOrderStatus.DRAFT),
    ],
)
def test_illegal_edges_absent_from_table(
    frm: PurchaseOrderStatus, to: PurchaseOrderStatus
) -> None:
    # Every disallowed edge must be absent from the transition table; the
    # shared StateMachineMixin._transition rejects anything not listed.
    assert to not in PurchaseOrderService.ALLOWED_TRANSITIONS[frm]


@pytest.mark.parametrize(
    "frm, to",
    [
        (PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.SENT),
        (PurchaseOrderStatus.SENT, PurchaseOrderStatus.PAID),
    ],
)
def test_legal_edges_present_in_table(
    frm: PurchaseOrderStatus, to: PurchaseOrderStatus
) -> None:
    assert to in PurchaseOrderService.ALLOWED_TRANSITIONS[frm]


def test_only_draft_is_deletable() -> None:
    assert frozenset({PurchaseOrderStatus.DRAFT}) == _DELETABLE_STATUSES
    assert PurchaseOrderStatus.SENT not in _DELETABLE_STATUSES
    assert PurchaseOrderStatus.PAID not in _DELETABLE_STATUSES


class _FakeTransitionTarget:
    """Minimal stand-in to exercise the shared _transition edge guard."""

    def __init__(self, status: PurchaseOrderStatus) -> None:
        self.status = status
        self.version = 1


def test_transition_rejects_illegal_edge_via_state_machine() -> None:
    # Use the real service transition logic against an in-memory object: a
    # DRAFT->PAID jump must raise rather than silently writing the status.
    service = PurchaseOrderService.__new__(PurchaseOrderService)
    entity = _FakeTransitionTarget(PurchaseOrderStatus.DRAFT)
    with pytest.raises(BadRequestException):
        service._transition(entity, PurchaseOrderStatus.PAID)
    # Status unchanged after the rejected transition.
    assert entity.status == PurchaseOrderStatus.DRAFT
