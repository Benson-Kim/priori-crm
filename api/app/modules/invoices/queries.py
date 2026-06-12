"""Invoice read-side query objects (ISSUE-009).

Decomposes the read-heavy concerns out of the ~1,000-line InvoiceService:

- ``apply_invoice_filters``       — single source of truth for invoice
  filtering (ISSUE-011), shared by the list view and the export so they
  can never drift.
- ``InvoiceStatisticsRepository`` — the status-count and dashboard
  statistics SQL.
- ``InvoiceExportQuery``          — batch loading of full ORM rows for the
  Excel export.

InvoiceService keeps thin delegating methods, so routers and existing
callers are unaffected.
"""

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.common.exceptions import DatabaseException
from app.common.search import build_search_clause
from app.constants.enums import InvoiceStatus
from app.modules.invoices.models import Invoice
from app.modules.invoices.schemas import InvoiceFilterParams, InvoiceStatusCounts

logger = logging.getLogger(__name__)


def apply_invoice_filters(query, filters: InvoiceFilterParams | None):
    """Single source of truth for invoice filtering (ISSUE-011).

    Shared by list_invoices and InvoiceExportQuery so the Excel export can
    never drift from the list view. Callers must have joined Customer.
    """
    from app.modules.customers.models import Customer

    if not filters:
        return query
    if filters.status:
        query = query.filter(Invoice.status == filters.status)
    if filters.customer_id:
        query = query.filter(Invoice.customer_id == filters.customer_id)
    if filters.date_from:
        query = query.filter(Invoice.transaction_date >= filters.date_from)
    if filters.date_to:
        query = query.filter(Invoice.transaction_date <= filters.date_to)
    if filters.due_date_from:
        query = query.filter(Invoice.due_date >= filters.due_date_from)
    if filters.due_date_to:
        query = query.filter(Invoice.due_date <= filters.due_date_to)
    if filters.search:
        search_clause = build_search_clause(
            filters.search,
            Invoice.invoice_number,
            Invoice.invoice_reference,
            Customer.company_name,
            Customer.first_name,
            Customer.last_name,
        )
        if search_clause is not None:
            query = query.filter(search_clause)
    return query


class InvoiceStatisticsRepository:
    """Read-side aggregates for invoices (ISSUE-009).

    Owns the status-count and dashboard-statistics SQL previously embedded
    in InvoiceService. Read-only: never flushes or commits.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def status_counts(
        self, customer_id: uuid.UUID | None = None
    ) -> InvoiceStatusCounts:
        """Get counts of invoices by status."""
        try:
            today = date.today()
            query = self._db.query(
                Invoice.status,
                func.count(Invoice.id).label("cnt"),
                func.sum(
                    case(
                        (
                            and_(
                                Invoice.status.in_(
                                    [
                                        InvoiceStatus.SENT,
                                        InvoiceStatus.PARTIAL,
                                        InvoiceStatus.OVERDUE,
                                    ]
                                ),
                                Invoice.due_date < today,
                                Invoice.balance_due > 0,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("overdue_cnt"),
            )

            if customer_id:
                query = query.filter(Invoice.customer_id == customer_id)

            rows = query.group_by(Invoice.status).all()

            counts_dict: dict[str, int] = {}
            overdue_count = 0
            total = 0
            for status_val, cnt, od_cnt in rows:
                counts_dict[status_val] = cnt
                total += cnt
                overdue_count += od_cnt or 0

            return InvoiceStatusCounts(
                all=total,
                draft=counts_dict.get(InvoiceStatus.DRAFT, 0),
                sent=counts_dict.get(InvoiceStatus.SENT, 0),
                partial=counts_dict.get(InvoiceStatus.PARTIAL, 0),
                paid=counts_dict.get(InvoiceStatus.PAID, 0),
                overdue=overdue_count,
                canceled=counts_dict.get(InvoiceStatus.CANCELED, 0),
            )

        except SQLAlchemyError as e:
            logger.exception("Database error getting status counts")
            raise DatabaseException("Failed to get status counts") from e

    def statistics(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """
        Get invoice statistics for dashboard using SQL aggregates.

        All computation is pushed to the database — no full table scan.
        Excludes canceled invoices.  Defaults to current calendar month.
        """
        try:
            from datetime import timedelta

            if not date_from and not date_to:
                today = date.today()
                date_from = date(today.year, today.month, 1)
                date_to = (
                    date(today.year, 12, 31)
                    if today.month == 12
                    else date(today.year, today.month + 1, 1) - timedelta(days=1)
                )

            today = date.today()

            agg = (
                self._db.query(
                    func.count(Invoice.id).label("total_invoices"),
                    func.coalesce(func.sum(Invoice.total_due), Decimal("0")).label(
                        "total_invoiced"
                    ),
                    func.coalesce(func.sum(Invoice.amount_paid), Decimal("0")).label(
                        "total_paid"
                    ),
                    func.coalesce(func.sum(Invoice.balance_due), Decimal("0")).label(
                        "total_outstanding"
                    ),
                    func.coalesce(
                        func.avg(
                            case(
                                (
                                    and_(
                                        Invoice.status == InvoiceStatus.PAID,
                                        Invoice.paid_at.isnot(None),
                                    ),
                                    func.extract(
                                        "epoch",
                                        Invoice.paid_at
                                        - func.cast(
                                            Invoice.transaction_date,
                                            type_=Invoice.paid_at.type,
                                        ),
                                    )
                                    / 86400,
                                )
                            )
                        ),
                        0,
                    ).label("avg_days_to_payment"),
                    func.count(
                        case(
                            (
                                and_(
                                    Invoice.status.in_(
                                        [
                                            InvoiceStatus.SENT,
                                            InvoiceStatus.PARTIAL,
                                            InvoiceStatus.OVERDUE,
                                        ]
                                    ),
                                    Invoice.due_date < today,
                                    Invoice.balance_due > 0,
                                ),
                                Invoice.id,
                            )
                        )
                    ).label("overdue_count"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    and_(
                                        Invoice.status.in_(
                                            [
                                                InvoiceStatus.SENT,
                                                InvoiceStatus.PARTIAL,
                                                InvoiceStatus.OVERDUE,
                                            ]
                                        ),
                                        Invoice.due_date < today,
                                        Invoice.balance_due > 0,
                                    ),
                                    Invoice.balance_due,
                                ),
                                else_=0,
                            )
                        ),
                        Decimal("0"),
                    ).label("overdue_amount"),
                )
                .filter(
                    Invoice.status != InvoiceStatus.CANCELED,
                    Invoice.transaction_date >= date_from if date_from else True,
                    Invoice.transaction_date <= date_to if date_to else True,
                )
                .one()
            )

            total = agg.total_invoices or 0
            avg_value = (
                (Decimal(str(agg.total_invoiced)) / total)
                if total > 0
                else Decimal("0.00")
            )

            logger.debug(
                "Calculated invoice statistics",
                extra={
                    "date_from": str(date_from),
                    "date_to": str(date_to),
                    "total": total,
                },
            )

            return {
                "total_invoices": total,
                "total_invoiced": Decimal(str(agg.total_invoiced)),
                "total_paid": Decimal(str(agg.total_paid)),
                "total_outstanding": Decimal(str(agg.total_outstanding)),
                "average_invoice_value": avg_value,
                "average_days_to_payment": round(
                    float(agg.avg_days_to_payment or 0), 1
                ),
                "overdue_count": agg.overdue_count or 0,
                "overdue_amount": Decimal(str(agg.overdue_amount)),
                "date_from": date_from,
                "date_to": date_to,
            }

        except SQLAlchemyError as e:
            logger.exception("Database error calculating invoice statistics")
            raise DatabaseException("Failed to calculate statistics") from e


class InvoiceExportQuery:
    """Batch loader of full Invoice rows for the Excel export (ISSUE-009)."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list(
        self,
        filters: InvoiceFilterParams | None = None,
        include_line_items: bool = False,
        limit: int | None = None,
    ) -> list[Invoice]:
        """Return full Invoice ORM rows for Excel export, batch-loaded.

        Customer is eager-joined for the display name and, when requested,
        line items are batch-loaded with ``selectinload`` (one extra query
        for the whole page, not one per row). ``limit`` caps the number of
        rows materialised in memory.
        """
        try:
            from app.modules.customers.models import Customer

            query = (
                self._db.query(Invoice)
                .join(Customer, Invoice.customer_id == Customer.id)
                .options(joinedload(Invoice.customer))
            )

            if include_line_items:
                query = query.options(selectinload(Invoice.line_items))

            query = apply_invoice_filters(query, filters)

            query = query.order_by(Invoice.created_at.desc())
            if limit is not None:
                query = query.limit(limit)

            return query.all()

        except SQLAlchemyError as e:
            logger.exception("Database error loading invoices for export")
            raise DatabaseException("Failed to load invoices for export") from e
