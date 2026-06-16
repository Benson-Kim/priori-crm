"""
Purchase Order business logic — service layer.

the totals/calculation engine. ``PurchaseOrderService`` extends
``BaseDocumentService`` so the state-machine, reference-retry, email and
two-phase-send mechanics are inherited rather than re-implemented (SOLID/DRY
reuse mandate). This issue wires only the calculation surface and the static
state-machine declarations; CRUD, list, send and convert land in later issues
(PO-03/04/06/07).

All money flows through ``app.common.financial`` (``build_line_items`` +
``sum_line_totals``); there is deliberately NO arithmetic in this module — a
divergent money path is the exact bug PO-02 exists to prevent.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import ClassVar

from app.common.document_service import BaseDocumentService
from app.common.financial import build_line_items, sum_line_totals
from app.constants.enums import PurchaseOrderStatus
from app.modules.purchase_orders.models import PurchaseOrder
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCalculationResponse,
    PurchaseOrderLineItemCreate,
)

logger = logging.getLogger(__name__)


class PurchaseOrderService(BaseDocumentService):
    """Service layer for purchase order operations.

    Shared state-machine and reference-retry mechanics come from
    ``BaseDocumentService``; the transition table and reference-collision
    markers below stay PO-specific. Purchase orders are vendor-facing.

    Lifecycle:
        DRAFT → SENT → BILLED
        DRAFT | SENT → CANCELED   (terminal)
    """

    MAX_RETRIES = 3
    MAX_REFERENCE_RETRIES = MAX_RETRIES

    _document_noun = "purchase_order"
    _reference_collision_markers = ("po_number", "po_reference")

    # Locked-load wiring (DocumentSendMixin._get_locked). The send
    # draft/sent statuses are declared here so the inherited FOR UPDATE row
    # loader and the Draft→Sent transition used by PO-06 resolve correctly.
    _send_model = PurchaseOrder
    _send_draft_status = PurchaseOrderStatus.DRAFT
    _send_sent_status = PurchaseOrderStatus.SENT

    ALLOWED_TRANSITIONS: ClassVar[
        dict[PurchaseOrderStatus, list[PurchaseOrderStatus]]
    ] = {
        PurchaseOrderStatus.DRAFT: [
            PurchaseOrderStatus.SENT,
            PurchaseOrderStatus.CANCELED,
        ],
        PurchaseOrderStatus.SENT: [
            PurchaseOrderStatus.BILLED,
            PurchaseOrderStatus.CANCELED,
        ],
        PurchaseOrderStatus.BILLED: [],  # terminal
        PurchaseOrderStatus.CANCELED: [],  # terminal
    }

    @staticmethod
    def _build_line_items(
        raw_items: list[PurchaseOrderLineItemCreate],
    ) -> list[dict]:
        """Delegate to the shared build_line_items helper (no local calc)."""
        return build_line_items(raw_items)

    @staticmethod
    def _sum_line_totals(line_items_data: list[dict]) -> tuple[Decimal, Decimal]:
        """Delegate to the shared sum_line_totals helper (no local calc)."""
        return sum_line_totals(line_items_data)

    # CALCULATION PREVIEW

    @classmethod
    def calculate_totals(
        cls,
        line_items: list[PurchaseOrderLineItemCreate],
    ) -> PurchaseOrderCalculationResponse:
        """
        Calculate PO totals without persisting — live preview endpoint.

        No discount in v1: total = subtotal + tax_total. Every monetary
        value is produced by ``app.common.financial`` so the result is, by
        construction, identical to the Expenses/Quotes engine for the same
        inputs (parity is asserted in tests).
        """
        calculated_items = cls._build_line_items(line_items)
        subtotal, tax_total = cls._sum_line_totals(calculated_items)

        formatted_items = [
            {
                "item_name": item["item_name"],
                "description": item["description"],
                "quantity": float(item["quantity"]),
                "unit_price": float(item["unit_price"]),
                "line_total": float(item["line_total"]),
                "tax_type": item["tax_type"],
                "tax_amount": float(item["tax_amount"]),
            }
            for item in calculated_items
        ]

        return PurchaseOrderCalculationResponse(
            subtotal=subtotal,
            tax_total=tax_total,
            total=subtotal + tax_total,
            line_items=formatted_items,
        )
