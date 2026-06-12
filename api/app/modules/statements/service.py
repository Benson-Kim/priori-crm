"""Financial statements service — thin orchestration over the read SQL.

All heavy lifting (predicates, aggregation) lives in
``app.modules.statements.queries``; this layer only resolves derived
metrics (net profit, margins, deltas) with Decimal arithmetic and shapes
responses. See the queries module docstring for the accounting basis.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy.orm import Session

from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import Currency
from app.modules.statements.queries import (
    CashflowFilters,
    StatementsRepository,
)
from app.modules.statements.schemas import (
    CashflowCounts,
    CashflowEntry,
    IncomeStatementLine,
    IncomeStatementResponse,
    IncomeStatementSection,
    OverviewMetric,
    ProfitMarginMetric,
    ResolvedPeriod,
    StatementOverviewResponse,
)

logger = logging.getLogger(__name__)

_TWO_DP = Decimal("0.01")
_ZERO = Decimal("0.00")


def _pct(value: Decimal) -> float:
    """Quantize a Decimal percentage to 2dp before exposing it as float."""
    return float(value.quantize(_TWO_DP, rounding=ROUND_HALF_UP))


class StatementsService:
    """Read-only reporting service; never mutates state."""

    def __init__(self, db: Session, current_user=None) -> None:
        self.db = db
        self.current_user = current_user
        self.repo = StatementsRepository(db)

    # Derived-metric helpers (zero-safe by construction)

    @staticmethod
    def _change_percent(current: Decimal, previous: Decimal) -> float | None:
        """Percent change vs ``previous``; None when the baseline is zero."""
        if previous == 0:
            return None
        return _pct((current - previous) / abs(previous) * Decimal(100))

    @staticmethod
    def _margin_percent(net: Decimal, revenue: Decimal) -> float | None:
        """Profit margin in percent; None when revenue is zero."""
        if revenue == 0:
            return None
        return _pct(net / revenue * Decimal(100))

    # Endpoints

    def get_overview(
        self, period: ResolvedPeriod, currency: Currency
    ) -> StatementOverviewResponse:
        """Overview cards with deltas vs the preceding equal-length window."""
        current = self.repo.period_totals(period.date_from, period.date_to, currency)
        previous = self.repo.period_totals(
            period.previous_date_from, period.previous_date_to, currency
        )

        current_net = current.revenue - current.expenses
        previous_net = previous.revenue - previous.expenses

        margin = self._margin_percent(current_net, current.revenue)
        previous_margin = self._margin_percent(previous_net, previous.revenue)
        change_points = (
            round(margin - previous_margin, 2)
            if margin is not None and previous_margin is not None
            else None
        )

        return StatementOverviewResponse(
            period=period,
            currency=currency,
            total_revenue=OverviewMetric(
                amount=current.revenue,
                previous_amount=previous.revenue,
                change_percent=self._change_percent(current.revenue, previous.revenue),
            ),
            total_expenses=OverviewMetric(
                amount=current.expenses,
                previous_amount=previous.expenses,
                change_percent=self._change_percent(
                    current.expenses, previous.expenses
                ),
            ),
            net_profit=OverviewMetric(
                amount=current_net,
                previous_amount=previous_net,
                change_percent=self._change_percent(current_net, previous_net),
            ),
            profit_margin=ProfitMarginMetric(
                percent=margin,
                previous_percent=previous_margin,
                change_points=change_points,
            ),
        )

    def get_income_statement(
        self, period: ResolvedPeriod, currency: Currency
    ) -> IncomeStatementResponse:
        """Operating revenue/expense sections grouped by account category.

        Section totals are summed (in Decimal) from the returned lines, so
        ``total == sum(line.amount)`` holds by construction.
        """
        revenue_rows = self.repo.revenue_by_category(
            period.date_from, period.date_to, currency
        )
        expense_rows = self.repo.expenses_by_category(
            period.date_from, period.date_to, currency
        )

        revenue_lines = [self._to_line(row) for row in revenue_rows]
        expense_lines = [self._to_line(row) for row in expense_rows]

        revenue_total = sum((line.amount for line in revenue_lines), _ZERO)
        expense_total = sum((line.amount for line in expense_lines), _ZERO)

        return IncomeStatementResponse(
            period=period,
            currency=currency,
            operating_revenue=IncomeStatementSection(
                line_items=revenue_lines, total=revenue_total
            ),
            operating_expenses=IncomeStatementSection(
                line_items=expense_lines, total=expense_total
            ),
            net_profit=revenue_total - expense_total,
        )

    @staticmethod
    def _to_line(row) -> IncomeStatementLine:
        return IncomeStatementLine(
            account_category=row.account_category,
            description=row.description,
            document_count=row.document_count,
            amount=Decimal(str(row.amount)),
        )

    def list_cashflow(
        self,
        period: ResolvedPeriod,
        currency: Currency,
        params: PaginationParams,
        *,
        category: Literal["all", "income", "expense"] = "all",
        search: str | None = None,
        account_category: str | None = None,
    ) -> PaginatedResponse[CashflowEntry]:
        """Paginated signed ledger of recognized documents."""
        filters = CashflowFilters(
            date_from=period.date_from,
            date_to=period.date_to,
            currency=currency,
            category=category,
            search=search,
            account_category=account_category,
        )

        total = self.repo.cashflow_total(filters) if params.with_total else None
        rows = self.repo.cashflow_window(
            filters, offset=params.offset, limit=params.fetch_limit
        )

        entries = [
            CashflowEntry(
                id=f"{row.source_type}:{row.source_id}",
                source_id=row.source_id,
                source_type=row.source_type,
                entity_name=row.entity_name,
                ref_no=row.ref_no,
                category=row.category,
                date=row.entry_date,
                amount=Decimal(str(row.amount)),
            )
            for row in rows
        ]
        return PaginatedResponse.create_from_window(entries, params, total=total)

    def get_cashflow_counts(
        self,
        period: ResolvedPeriod,
        currency: Currency,
        *,
        search: str | None = None,
        account_category: str | None = None,
    ) -> CashflowCounts:
        """Tab counts under the current period/search/drill-down filters."""
        filters = CashflowFilters(
            date_from=period.date_from,
            date_to=period.date_to,
            currency=currency,
            category="all",
            search=search,
            account_category=account_category,
        )
        return self.repo.cashflow_counts(filters)
