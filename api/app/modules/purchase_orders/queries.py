"""Purchase-order read-side query objects.

Decomposes the read-heavy concerns out of PurchaseOrderService, mirroring
the expenses/quotes layout exactly:

- ``apply_purchase_order_filters`` — single source of truth for purchase-order
  filtering, including the CANCELED-visibility rule, shared by the list view
  and the export so they can never drift.
- ``PurchaseOrderStatisticsRepository`` — the status-count SQL for the
  filter-tab badges.
- ``PurchaseOrderExportQuery`` — batch loading of full ORM rows for the Excel
  export.

PurchaseOrderService keeps thin delegating methods, so the router and existing
callers are unaffected.
"""

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.common.exceptions import DatabaseException
from app.common.search import build_search_clause
from app.constants.enums import PurchaseOrderStatus
from app.modules.purchase_orders.models import PurchaseOrder
from app.modules.purchase_orders.schemas import (
    PurchaseOrderFilterParams,
    PurchaseOrderStatusCounts,
)

logger = logging.getLogger(__name__)


def apply_purchase_order_filters(query, filters: PurchaseOrderFilterParams | None):
    """Single source of truth for purchase-order filtering.

    Shared by ``list_purchase_orders`` and ``PurchaseOrderExportQuery`` so the
    Excel export can never drift from the list view. Owns the visibility rule
    too: CANCELED is hidden unless explicitly requested via the status filter,
    on both the filtered and unfiltered paths. Callers must have joined Vendor.
    """
    from app.modules.vendors.models import Vendor

    if filters and filters.status:
        query = query.filter(PurchaseOrder.status == filters.status)
    else:
        query = query.filter(PurchaseOrder.status != PurchaseOrderStatus.CANCELED)

    if not filters:
        return query

    if filters.vendor_id:
        query = query.filter(PurchaseOrder.vendor_id == filters.vendor_id)
    if filters.date_from:
        query = query.filter(PurchaseOrder.order_date >= filters.date_from)
    if filters.date_to:
        query = query.filter(PurchaseOrder.order_date <= filters.date_to)
    if filters.delivery_date_from:
        query = query.filter(PurchaseOrder.delivery_date >= filters.delivery_date_from)
    if filters.delivery_date_to:
        query = query.filter(PurchaseOrder.delivery_date <= filters.delivery_date_to)
    if filters.search:
        search_clause = build_search_clause(
            filters.search,
            PurchaseOrder.po_number,
            PurchaseOrder.po_reference,
            Vendor.vendor_name,
        )
        if search_clause is not None:
            query = query.filter(search_clause)
    return query


class PurchaseOrderStatisticsRepository:
    """Read-side aggregates for purchase orders.

    Owns the status-count SQL for the filter-tab bar. Read-only: never
    flushes or commits.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def status_counts(self) -> PurchaseOrderStatusCounts:
        """Single-query status counts for the filter-tab bar.

        One grouped COUNT over status — no N+1 and no second round-trip.
        CANCELED is surfaced via its own bucket and excluded from ``all``
        (it is a voided PO), matching the Expenses tab semantics.
        """
        from sqlalchemy import func

        try:
            rows = (
                self._db.query(
                    PurchaseOrder.status,
                    func.count(PurchaseOrder.id).label("cnt"),
                )
                .group_by(PurchaseOrder.status)
                .all()
            )

            counts: dict[str, int] = {}
            total = 0
            for status_val, cnt in rows:
                counts[status_val] = cnt
                if status_val != PurchaseOrderStatus.CANCELED:
                    total += cnt

            return PurchaseOrderStatusCounts(
                all=total,
                draft=counts.get(PurchaseOrderStatus.DRAFT, 0),
                sent=counts.get(PurchaseOrderStatus.SENT, 0),
                billed=counts.get(PurchaseOrderStatus.BILLED, 0),
                canceled=counts.get(PurchaseOrderStatus.CANCELED, 0),
            )

        except SQLAlchemyError as exc:
            logger.exception("Database error getting purchase-order status counts")
            raise DatabaseException("Failed to get status counts") from exc


class PurchaseOrderExportQuery:
    """Batch loader of full PurchaseOrder rows for the Excel export."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list(
        self,
        filters: PurchaseOrderFilterParams | None = None,
        include_line_items: bool = False,
        limit: int | None = None,
    ) -> list[PurchaseOrder]:
        """Return full PurchaseOrder ORM rows for Excel export, batch-loaded.

        Vendor eager-joined for the display name; line items via
        ``selectinload`` only when requested. ``limit`` caps the rows
        materialised in memory (the router fetches BATCH_SIZE + 1 to detect
        truncation). Filtering is delegated to the shared
        ``apply_purchase_order_filters`` so it can never drift from the list.
        """
        try:
            from app.modules.vendors.models import Vendor

            query = (
                self._db.query(PurchaseOrder)
                .join(Vendor, PurchaseOrder.vendor_id == Vendor.id)
                .options(joinedload(PurchaseOrder.vendor))
            )

            if include_line_items:
                query = query.options(selectinload(PurchaseOrder.line_items))

            query = apply_purchase_order_filters(query, filters)

            query = query.order_by(PurchaseOrder.created_at.desc())
            if limit is not None:
                query = query.limit(limit)

            return query.all()

        except SQLAlchemyError as exc:
            logger.exception("Database error loading purchase orders for export")
            raise DatabaseException(
                "Failed to load purchase orders for export"
            ) from exc
