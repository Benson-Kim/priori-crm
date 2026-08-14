"""Dashboard API endpoints.

Read-only widgets backing the Dashboard page: summary metric cards, the
cashflow chart series, top sales and recent transactions. All endpoints
require an authenticated user (via DashboardServiceDep) and accept the
same period contract (range / dateFrom / dateTo / currency) as the
statements endpoints — the shared parameter annotations are imported
from the statements router so the two contracts cannot drift.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Query

from app.common.dependencies import DashboardServiceDep
from app.common.routing import CommitOnSuccessRoute
from app.constants.enums import Currency
from app.modules.dashboard.schemas import (
    MAX_TOP_SALES_LIMIT,
    MAX_TRANSACTIONS_LIMIT,
    CashflowSeriesResponse,
    DashboardSummaryResponse,
    DashboardTransactionsResponse,
    Granularity,
    TopSalesResponse,
)
from app.modules.statements.router import (
    CurrencyParam,
    DateFromParam,
    DateToParam,
    RangeParam,
)
from app.modules.statements.schemas import RangePreset, ResolvedPeriod

logger = logging.getLogger(__name__)

router = APIRouter(route_class=CommitOnSuccessRoute)

GranularityParam = Annotated[
    Granularity | None,
    Query(
        description=(
            "Chart bucket size. Auto-selected from the window length "
            "(day/week/month) when omitted."
        )
    ),
]


@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    summary="Dashboard metric cards",
    description=(
        "Balance (outstanding receivables), income, expenses and "
        "new-customers count for the selected period, each with a delta "
        "against the immediately preceding period of equal length. "
        "Income and expenses run the same aggregates as the statements "
        "overview. Deltas are null (not a division-by-zero error) when "
        "the baseline is zero."
    ),
    responses={
        200: {"description": "Summary metrics"},
        400: {"description": "Invalid period parameters"},
    },
)
def get_summary(
    service: DashboardServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
) -> DashboardSummaryResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_summary(period, currency)


@router.get(
    "/cashflow-series",
    response_model=CashflowSeriesResponse,
    summary="Cashflow chart series",
    description=(
        "Zero-filled income/expense buckets covering the selected period "
        "exactly, plus period totals. Measured at tax-inclusive totals "
        "(the same cash view as the statements cashflow ledger)."
    ),
    responses={
        200: {"description": "Bucketed cashflow series"},
        400: {"description": "Invalid period parameters"},
    },
)
def get_cashflow_series(
    service: DashboardServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
    granularity: GranularityParam = None,
) -> CashflowSeriesResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_cashflow_series(period, currency, granularity)


@router.get(
    "/top-sales",
    response_model=TopSalesResponse,
    summary="Top-selling items",
    description=(
        "Invoice line items grouped by name, ordered by pre-tax amount "
        "descending (the same measurement as the income statement's "
        "revenue lines), with units sold and document counts."
    ),
    responses={
        200: {"description": "Top sales lines"},
        400: {"description": "Invalid period parameters"},
    },
)
def get_top_sales(
    service: DashboardServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_TOP_SALES_LIMIT, description="Max rows to return"),
    ] = 5,
) -> TopSalesResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_top_sales(period, currency, limit)


@router.get(
    "/transactions",
    response_model=DashboardTransactionsResponse,
    summary="Recent transactions",
    description=(
        "Most recent recognized documents in the signed ledger convention "
        "(income positive, expense negative), newest first, enriched with "
        "the counterparty website and the document's first line item."
    ),
    responses={
        200: {"description": "Recent transactions"},
        400: {"description": "Invalid period parameters"},
    },
)
def get_recent_transactions(
    service: DashboardServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_TRANSACTIONS_LIMIT, description="Max rows to return"),
    ] = 10,
) -> DashboardTransactionsResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_recent_transactions(period, currency, limit)
