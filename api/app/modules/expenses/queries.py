"""Expense read-side query objects.

Decomposes the read-heavy concerns out of ExpenseService:

- ``apply_expense_filters``       — single source of truth for expense
  filtering, including the CANCELED-visibility rule, shared by
  the list view and the export so they can never drift.
- ``ExpenseStatisticsRepository`` — the status-count and dashboard
  statistics SQL.
- ``ExpenseExportQuery``          — batch loading of full ORM rows for the
  Excel export.

ExpenseService keeps thin delegating methods, so routers and existing
callers are unaffected.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.common.exceptions import DatabaseException
from app.common.reporting_time import reporting_date
from app.common.search import build_search_clause
from app.constants.enums import ExpenseStatus
from app.modules.expenses.models import Expense
from app.modules.expenses.schemas import ExpenseFilterParams, ExpenseStatusCounts

logger = logging.getLogger(__name__)


def apply_expense_filters(query, filters: ExpenseFilterParams | None):
    """Single source of truth for expense filtering.

    Shared by list_expenses and ExpenseExportQuery so the Excel export can
    never drift from the list view. Owns the visibility rule too:
    CANCELED is hidden unless explicitly requested via the status
    filter, on the filtered AND unfiltered paths. Callers must have
    joined Vendor.
    """
    from app.modules.vendors.models import Vendor

    if filters and filters.status:
        query = query.filter(Expense.status == filters.status)
    else:
        query = query.filter(Expense.status != ExpenseStatus.CANCELED)

    if not filters:
        return query

    if filters.vendor_id:
        query = query.filter(Expense.vendor_id == filters.vendor_id)
    if filters.date_from:
        query = query.filter(Expense.expense_date >= filters.date_from)
    if filters.date_to:
        query = query.filter(Expense.expense_date <= filters.date_to)
    if filters.due_date_from:
        query = query.filter(Expense.due_date >= filters.due_date_from)
    if filters.due_date_to:
        query = query.filter(Expense.due_date <= filters.due_date_to)
    if filters.is_recurring is not None:
        query = query.filter(Expense.is_recurring == filters.is_recurring)
    if filters.search:
        search_clause = build_search_clause(
            filters.search,
            Expense.expense_number,
            Expense.expense_reference,
            Vendor.vendor_name,
        )
        if search_clause is not None:
            query = query.filter(search_clause)
    return query


class ExpenseStatisticsRepository:
    """Read-side aggregates for expenses.

    Owns the status-count and dashboard-statistics SQL previously embedded
    in ExpenseService. Read-only: never flushes or commits.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def status_counts(self) -> ExpenseStatusCounts:
        """
        Single-query status counts for the filter-tab bar.

        The overdue bucket captures both:
        - Rows already stored as OVERDUE
        - PENDING rows whose due_date has passed but the nightly job
          has not yet run (computed via SQL CASE — no second round-trip).
        """
        try:
            today = reporting_date()

            rows = (
                self._db.query(
                    Expense.status,
                    func.count(Expense.id).label("cnt"),
                    func.sum(
                        case(
                            (
                                and_(
                                    Expense.status == ExpenseStatus.PENDING,
                                    Expense.due_date < today,
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("pending_past_due_cnt"),
                )
                .group_by(Expense.status)
                .all()
            )

            counts: dict[str, int] = {}
            pending_past_due = 0
            # `all` and the active-status tabs cover live expenses only;
            # CANCELED is surfaced via its own count, not folded into `all`.
            total = 0

            for status_val, cnt, ppd_cnt in rows:
                counts[status_val] = cnt
                if status_val != ExpenseStatus.CANCELED:
                    total += cnt
                pending_past_due += ppd_cnt or 0

            stored_overdue = counts.get(ExpenseStatus.OVERDUE, 0)
            displayed_overdue = stored_overdue + pending_past_due

            return ExpenseStatusCounts(
                all=total,
                pending=counts.get(ExpenseStatus.PENDING, 0),
                paid=counts.get(ExpenseStatus.PAID, 0),
                overdue=displayed_overdue,
                canceled=counts.get(ExpenseStatus.CANCELED, 0),
            )

        except SQLAlchemyError as exc:
            logger.exception("Database error getting expense status counts")
            raise DatabaseException("Failed to get status counts") from exc

    def statistics(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """
        Aggregated expense statistics — all computation pushed to the DB.
        Defaults to the current calendar month when no range is supplied.
        """
        try:
            from datetime import timedelta

            today = reporting_date()

            if not date_from and not date_to:
                date_from = date(today.year, today.month, 1)
                date_to = (
                    date(today.year, 12, 31)
                    if today.month == 12
                    else date(today.year, today.month + 1, 1) - timedelta(days=1)
                )

            agg = (
                self._db.query(
                    func.count(Expense.id).label("total_expenses"),
                    func.coalesce(func.sum(Expense.total_due), Decimal("0")).label(
                        "total_amount"
                    ),
                    func.coalesce(func.sum(Expense.amount_paid), Decimal("0")).label(
                        "total_paid"
                    ),
                    func.coalesce(func.sum(Expense.balance_due), Decimal("0")).label(
                        "total_outstanding"
                    ),
                    func.count(
                        case(
                            (
                                and_(
                                    Expense.status.in_(
                                        [ExpenseStatus.PENDING, ExpenseStatus.OVERDUE]
                                    ),
                                    Expense.due_date < today,
                                    Expense.balance_due > 0,
                                ),
                                Expense.id,
                            )
                        )
                    ).label("overdue_count"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    and_(
                                        Expense.status.in_(
                                            [
                                                ExpenseStatus.PENDING,
                                                ExpenseStatus.OVERDUE,
                                            ]
                                        ),
                                        Expense.due_date < today,
                                        Expense.balance_due > 0,
                                    ),
                                    Expense.balance_due,
                                ),
                                else_=0,
                            )
                        ),
                        Decimal("0"),
                    ).label("overdue_amount"),
                    func.coalesce(
                        func.avg(
                            case(
                                (
                                    and_(
                                        Expense.status == ExpenseStatus.PAID,
                                        Expense.paid_at.isnot(None),
                                    ),
                                    func.extract(
                                        "epoch",
                                        Expense.paid_at
                                        - func.cast(
                                            Expense.expense_date,
                                            type_=Expense.paid_at.type,
                                        ),
                                    )
                                    / 86400,
                                )
                            )
                        ),
                        0,
                    ).label("avg_days_to_payment"),
                )
                .filter(
                    Expense.status != ExpenseStatus.CANCELED,
                    Expense.expense_date >= date_from if date_from else True,
                    Expense.expense_date <= date_to if date_to else True,
                )
                .one()
            )

            total = agg.total_expenses or 0
            avg_value = (
                Decimal(str(agg.total_amount)) / total if total > 0 else Decimal("0.00")
            )

            logger.debug(
                "Calculated expense statistics",
                extra={
                    "date_from": str(date_from),
                    "date_to": str(date_to),
                    "total": total,
                },
            )

            return {
                "total_expenses": total,
                "total_amount": Decimal(str(agg.total_amount)),
                "total_paid": Decimal(str(agg.total_paid)),
                "total_outstanding": Decimal(str(agg.total_outstanding)),
                "overdue_count": agg.overdue_count or 0,
                "overdue_amount": Decimal(str(agg.overdue_amount)),
                "average_expense_value": avg_value,
                "average_days_to_payment": round(
                    float(agg.avg_days_to_payment or 0), 1
                ),
                "date_from": date_from,
                "date_to": date_to,
            }

        except SQLAlchemyError as exc:
            logger.exception("Database error calculating expense statistics")
            raise DatabaseException("Failed to calculate statistics") from exc


class ExpenseExportQuery:
    """Batch loader of full Expense rows for the Excel export."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list(
        self,
        filters: ExpenseFilterParams | None = None,
        include_line_items: bool = False,
        limit: int | None = None,
    ) -> list[Expense]:
        """Return full Expense ORM rows for Excel export, batch-loaded.

        Vendor eager-joined for the display name; line items via
        ``selectinload`` only when requested. ``limit`` caps rows
        materialised in memory.
        """
        try:
            from app.modules.vendors.models import Vendor

            query = (
                self._db.query(Expense)
                .join(Vendor, Expense.vendor_id == Vendor.id)
                .options(joinedload(Expense.vendor))
            )

            if include_line_items:
                query = query.options(selectinload(Expense.line_items))

            query = apply_expense_filters(query, filters)

            query = query.order_by(Expense.created_at.desc())
            if limit is not None:
                query = query.limit(limit)

            return query.all()

        except SQLAlchemyError as exc:
            logger.exception("Database error loading expenses for export")
            raise DatabaseException("Failed to load expenses for export") from exc
