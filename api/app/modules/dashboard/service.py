"""Dashboard service — thin orchestration over the dashboard read SQL.

Extends ``StatementsService`` so the summary cards reuse exactly the
overview math (totals, zero-safe delta semantics) of the statements
pages; the repository is swapped for ``DashboardRepository``, which is
itself a ``StatementsRepository``, so the inherited statements endpoints
remain fully functional on this service.

All heavy lifting (predicates, aggregation) lives in
``app.modules.dashboard.queries``; this layer only folds day rows into
chart buckets and shapes responses, with Decimal arithmetic throughout.
"""

import logging
from bisect import bisect_right
from decimal import Decimal

from sqlalchemy.orm import Session

from app.constants.enums import Currency
from app.modules.dashboard.queries import DashboardRepository
from app.modules.dashboard.schemas import (
    MAX_TOP_SALES_LIMIT,
    MAX_TRANSACTIONS_LIMIT,
    CashflowBucket,
    CashflowSeriesResponse,
    CountMetric,
    DashboardSummaryResponse,
    DashboardTransaction,
    DashboardTransactionsResponse,
    Granularity,
    TopSaleLine,
    TopSalesResponse,
    build_buckets,
    resolve_granularity,
)
from app.modules.statements.schemas import OverviewMetric, ResolvedPeriod
from app.modules.statements.service import StatementsService

logger = logging.getLogger(__name__)

_ZERO = Decimal("0.00")


def _clamp(limit: int, maximum: int) -> int:
    """Defense-in-depth bound for widget list sizes (router validates too)."""
    return max(1, min(limit, maximum))


class DashboardService(StatementsService):
    """Read-only dashboard service; never mutates state."""

    def __init__(self, db: Session, current_user=None) -> None:
        super().__init__(db, current_user=current_user)
        # A DashboardRepository is-a StatementsRepository, so inherited
        # statements endpoints keep working against the same instance.
        self.repo = DashboardRepository(db)

    # Summary cards

    def get_summary(
        self, period: ResolvedPeriod, currency: Currency
    ) -> DashboardSummaryResponse:
        """Metric cards with deltas vs the preceding equal-length window.

        Income and expenses are taken verbatim from the inherited
        statements overview, so the two pages can never disagree.
        """
        overview = self.get_overview(period, currency)

        balance = self.repo.receivables(period.date_from, period.date_to, currency)
        previous_balance = self.repo.receivables(
            period.previous_date_from, period.previous_date_to, currency
        )

        new_count = self.repo.new_customers_count(period.date_from, period.date_to)
        previous_count = self.repo.new_customers_count(
            period.previous_date_from, period.previous_date_to
        )

        return DashboardSummaryResponse(
            period=period,
            currency=currency,
            balance=OverviewMetric(
                amount=balance,
                previous_amount=previous_balance,
                change_percent=self._change_percent(balance, previous_balance),
            ),
            income=overview.total_revenue,
            expenses=overview.total_expenses,
            new_customers=CountMetric(
                count=new_count,
                previous_count=previous_count,
                change_percent=self._change_percent(
                    Decimal(new_count), Decimal(previous_count)
                ),
            ),
        )

    # Cashflow chart series

    def get_cashflow_series(
        self,
        period: ResolvedPeriod,
        currency: Currency,
        granularity: Granularity | None = None,
    ) -> CashflowSeriesResponse:
        """Zero-filled income/expense buckets for the cashflow chart."""
        granularity = granularity or resolve_granularity(period)
        windows = build_buckets(period.date_from, period.date_to, granularity)
        starts = [start for start, _ in windows]

        income = [_ZERO] * len(windows)
        expense = [_ZERO] * len(windows)

        def fold(day_rows: list, target: list[Decimal]) -> None:
            # Every day is inside the window by the SQL predicates, so the
            # bisect index is always valid.
            for day, total in day_rows:
                target[bisect_right(starts, day) - 1] += total

        fold(
            self.repo.daily_income(period.date_from, period.date_to, currency),
            income,
        )
        fold(
            self.repo.daily_expense(period.date_from, period.date_to, currency),
            expense,
        )

        buckets = [
            CashflowBucket(
                bucket_start=start,
                bucket_end=end,
                income=bucket_income,
                expense=bucket_expense,
                net=bucket_income - bucket_expense,
            )
            for (start, end), bucket_income, bucket_expense in zip(
                windows, income, expense, strict=True
            )
        ]
        total_income = sum(income, _ZERO)
        total_expense = sum(expense, _ZERO)

        return CashflowSeriesResponse(
            period=period,
            currency=currency,
            granularity=granularity,
            total_income=total_income,
            total_expense=total_expense,
            net_total=total_income - total_expense,
            buckets=buckets,
        )

    # Top sales

    def get_top_sales(
        self, period: ResolvedPeriod, currency: Currency, limit: int = 5
    ) -> TopSalesResponse:
        """Top invoice line items by pre-tax amount, descending."""
        rows = self.repo.top_sales(
            period.date_from,
            period.date_to,
            currency,
            _clamp(limit, MAX_TOP_SALES_LIMIT),
        )
        items = [
            TopSaleLine(
                item_name=row.item_name,
                units_sold=Decimal(str(row.units_sold)),
                document_count=row.document_count,
                amount=Decimal(str(row.amount)),
            )
            for row in rows
        ]
        return TopSalesResponse(period=period, currency=currency, items=items)

    # Recent transactions

    def get_recent_transactions(
        self, period: ResolvedPeriod, currency: Currency, limit: int = 10
    ) -> DashboardTransactionsResponse:
        """Most recent recognized documents in the signed ledger convention."""
        rows = self.repo.recent_transactions(
            period.date_from,
            period.date_to,
            currency,
            _clamp(limit, MAX_TRANSACTIONS_LIMIT),
        )
        items = [
            DashboardTransaction(
                id=f"{row.source_type}:{row.source_id}",
                source_id=row.source_id,
                source_type=row.source_type,
                ref_no=row.ref_no,
                entity_name=row.entity_name,
                website=row.website,
                item_name=row.item_name,
                category=row.category,
                date=row.entry_date,
                recorded_at=row.recorded_at,
                amount=Decimal(str(row.amount)),
            )
            for row in rows
        ]
        return DashboardTransactionsResponse(
            period=period, currency=currency, items=items
        )
