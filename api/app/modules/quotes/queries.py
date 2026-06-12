"""Quote read-side query objects.

Decomposes the read-heavy concerns out of QuoteService:

- ``apply_quote_filters``       — single source of truth for quote
  filtering, shared by the list view and the export so they
  can never drift.
- ``QuoteStatisticsRepository`` — the status-count and dashboard
  statistics SQL.
- ``QuoteExportQuery``          — batch loading of full ORM rows for the
  Excel export.

QuoteService keeps thin delegating methods, so routers and existing
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
from app.common.search import build_search_clause
from app.constants.enums import QuoteStatus
from app.modules.quotes.models import Quote
from app.modules.quotes.schemas import QuoteFilterParams, QuoteStatusCounts

logger = logging.getLogger(__name__)


def apply_quote_filters(query, filters: QuoteFilterParams | None):
    """Single source of truth for quote filtering.

    Shared by list_quotes and QuoteExportQuery so the Excel export can
    never drift from the list view. Callers must have joined Customer.
    """
    from app.modules.customers.models import Customer

    if not filters:
        return query
    if filters.status:
        query = query.filter(Quote.status == filters.status)
    if filters.customer_id:
        query = query.filter(Quote.customer_id == filters.customer_id)
    if filters.date_from:
        query = query.filter(Quote.transaction_date >= filters.date_from)
    if filters.date_to:
        query = query.filter(Quote.transaction_date <= filters.date_to)
    if filters.due_date_from:
        query = query.filter(Quote.due_date >= filters.due_date_from)
    if filters.due_date_to:
        query = query.filter(Quote.due_date <= filters.due_date_to)
    if filters.search:
        search_clause = build_search_clause(
            filters.search,
            Quote.quote_number,
            Quote.quote_reference,
            Customer.first_name,
            Customer.last_name,
            Customer.company_name,
        )
        if search_clause is not None:
            query = query.filter(search_clause)
    return query


class QuoteStatisticsRepository:
    """Read-side aggregates for quotes.

    Owns the status-count and dashboard-statistics SQL previously embedded
    in QuoteService. Read-only: never flushes or commits.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def status_counts(self) -> QuoteStatusCounts:
        """
        Get counts of quotes by status in a single SQL query.

        The 'expired' bucket counts DRAFT/SENT quotes whose due_date has
        passed; computed via CASE to avoid a second round-trip.
        """
        try:
            today = date.today()
            rows = (
                self._db.query(
                    Quote.status,
                    func.count(Quote.id).label("cnt"),
                    func.sum(
                        case(
                            (
                                and_(
                                    Quote.status.in_(
                                        [QuoteStatus.DRAFT, QuoteStatus.SENT]
                                    ),
                                    Quote.due_date < today,
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("expired_cnt"),
                )
                .group_by(Quote.status)
                .all()
            )

            counts_dict: dict[str, int] = {}
            expired_count = 0
            total = 0
            for status_val, cnt, exp_cnt in rows:
                counts_dict[status_val] = cnt
                total += cnt
                expired_count += exp_cnt or 0

            return QuoteStatusCounts(
                all=total,
                draft=counts_dict.get(QuoteStatus.DRAFT, 0),
                sent=counts_dict.get(QuoteStatus.SENT, 0),
                approved=counts_dict.get(QuoteStatus.APPROVED, 0),
                invoiced=counts_dict.get(QuoteStatus.INVOICED, 0),
                expired=expired_count,
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
        Get quote statistics for dashboard using SQL aggregates.

        All aggregations are pushed to the database — no full table
        scan into Python memory.  Defaults to the current calendar month.
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

            # Single aggregate query — no Python-side loops over rows
            agg = (
                self._db.query(
                    func.count(Quote.id).label("total_quotes"),
                    func.coalesce(func.sum(Quote.total_due), Decimal("0")).label(
                        "total_quoted"
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (Quote.status == QuoteStatus.APPROVED, Quote.total_due),
                                else_=0,
                            )
                        ),
                        Decimal("0"),
                    ).label("total_approved"),
                    func.coalesce(
                        func.sum(
                            case(
                                (Quote.status == QuoteStatus.INVOICED, Quote.total_due),
                                else_=0,
                            )
                        ),
                        Decimal("0"),
                    ).label("total_invoiced"),
                    func.count(
                        case(
                            (
                                Quote.status.in_(
                                    [QuoteStatus.APPROVED, QuoteStatus.INVOICED]
                                ),
                                Quote.id,
                            )
                        )
                    ).label("converted_count"),
                    func.coalesce(
                        func.avg(
                            case(
                                (
                                    and_(
                                        Quote.approved_at.isnot(None),
                                        Quote.status.in_(
                                            [QuoteStatus.APPROVED, QuoteStatus.INVOICED]
                                        ),
                                    ),
                                    func.extract(
                                        "epoch",
                                        Quote.approved_at
                                        - func.cast(
                                            Quote.transaction_date,
                                            type_=Quote.approved_at.type,
                                        ),
                                    )
                                    / 86400,
                                )
                            )
                        ),
                        0,
                    ).label("avg_days_to_approval"),
                    func.count(
                        case(
                            (
                                and_(
                                    Quote.status.in_(
                                        [QuoteStatus.DRAFT, QuoteStatus.SENT]
                                    ),
                                    Quote.due_date < today,
                                ),
                                Quote.id,
                            )
                        )
                    ).label("expired_count"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    and_(
                                        Quote.status.in_(
                                            [QuoteStatus.DRAFT, QuoteStatus.SENT]
                                        ),
                                        Quote.due_date < today,
                                    ),
                                    Quote.total_due,
                                ),
                                else_=0,
                            )
                        ),
                        Decimal("0"),
                    ).label("expired_amount"),
                )
                .filter(
                    Quote.transaction_date >= date_from if date_from else True,
                    Quote.transaction_date <= date_to if date_to else True,
                )
                .one()
            )

            total = agg.total_quotes or 0
            conversion_rate = (
                round(agg.converted_count / total * 100, 1) if total > 0 else 0.0
            )
            avg_value = (
                (Decimal(str(agg.total_quoted)) / total)
                if total > 0
                else Decimal("0.00")
            )

            logger.debug(
                "Calculated quote statistics",
                extra={
                    "date_from": str(date_from),
                    "date_to": str(date_to),
                    "total": total,
                },
            )

            return {
                "total_quotes": total,
                "total_quoted": Decimal(str(agg.total_quoted)),
                "total_approved": Decimal(str(agg.total_approved)),
                "total_invoiced": Decimal(str(agg.total_invoiced)),
                "conversion_rate": conversion_rate,
                "average_quote_value": avg_value,
                "average_days_to_approval": round(
                    float(agg.avg_days_to_approval or 0), 1
                ),
                "expired_count": agg.expired_count or 0,
                "expired_amount": Decimal(str(agg.expired_amount)),
                "date_from": date_from,
                "date_to": date_to,
            }

        except SQLAlchemyError as e:
            logger.exception("Database error calculating quote statistics")
            raise DatabaseException("Failed to calculate statistics") from e


class QuoteExportQuery:
    """Batch loader of full Quote rows for the Excel export."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list(
        self,
        filters: QuoteFilterParams | None = None,
        include_line_items: bool = False,
        limit: int | None = None,
    ) -> list[Quote]:
        """Return full Quote ORM rows for Excel export, batch-loaded.

        Customer eager-joined; line items via ``selectinload`` only when
        requested. ``limit`` caps rows materialised in memory.
        """
        try:
            from app.modules.customers.models import Customer

            query = (
                self._db.query(Quote)
                .join(Customer, Quote.customer_id == Customer.id)
                .options(joinedload(Quote.customer))
            )

            if include_line_items:
                query = query.options(selectinload(Quote.line_items))

            query = apply_quote_filters(query, filters)

            query = query.order_by(Quote.created_at.desc())
            if limit is not None:
                query = query.limit(limit)

            return query.all()

        except SQLAlchemyError as e:
            logger.exception("Database error loading quotes for export")
            raise DatabaseException("Failed to load quotes for export") from e
