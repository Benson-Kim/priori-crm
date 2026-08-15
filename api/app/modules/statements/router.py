"""Financial statements API endpoints.

Read-only reporting endpoints backing the Income Statements and Cashflow
pages. See ``app.modules.statements.queries`` for the accounting basis.
All endpoints require an authenticated user (via StatementsServiceDep).
"""

import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.common.dependencies import StatementsServiceDep
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.routing import CommitOnSuccessRoute
from app.constants.enums import Currency
from app.modules.statements.schemas import (
    CashflowCounts,
    CashflowEntry,
    IncomeStatementResponse,
    RangePreset,
    ResolvedPeriod,
    StatementOverviewResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(route_class=CommitOnSuccessRoute)

# Shared query-parameter annotations
RangeParam = Annotated[
    RangePreset,
    Query(alias="range", description="Reporting period preset"),
]
DateFromParam = Annotated[
    date | None,
    Query(alias="dateFrom", description="Window start (range=custom only)"),
]
DateToParam = Annotated[
    date | None,
    Query(alias="dateTo", description="Window end (range=custom only)"),
]
CurrencyParam = Annotated[
    Currency,
    Query(description="ISO-4217 currency to report in (single-currency)"),
]
SearchParam = Annotated[
    str | None,
    Query(
        max_length=200,
        description="Substring match on entity name or document reference",
    ),
]
AccountCategoryParam = Annotated[
    str | None,
    Query(
        alias="accountCategory",
        max_length=500,
        description=(
            "Drill-down: only documents containing a line item with this "
            "account category"
        ),
    ),
]


@router.get(
    "/overview",
    response_model=StatementOverviewResponse,
    summary="Financial overview metrics",
    description=(
        "Total revenue, total expenses, net profit and profit margin for "
        "the selected period, each with a delta against the immediately "
        "preceding period of equal length. Deltas and margin are null "
        "(not a division-by-zero error) when the baseline is zero."
    ),
    responses={
        200: {"description": "Overview metrics"},
        400: {"description": "Invalid period parameters"},
    },
)
def get_overview(
    service: StatementsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
) -> StatementOverviewResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_overview(period, currency)


@router.get(
    "/income-statement",
    response_model=IncomeStatementResponse,
    summary="Income statement",
    description=(
        "Operating revenue and operating expense line items grouped by "
        "account category for the selected period, with section totals "
        "that always equal the sum of their lines."
    ),
    responses={
        200: {"description": "Income statement sections"},
        400: {"description": "Invalid period parameters"},
    },
)
def get_income_statement(
    service: StatementsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
) -> IncomeStatementResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_income_statement(period, currency)


@router.get(
    "/cashflow",
    response_model=PaginatedResponse[CashflowEntry],
    summary="Cashflow ledger",
    description=(
        "Paginated, signed ledger of recognized documents (income "
        "positive, expense negative). Supports category tabs, search and "
        "the account-category drill-down from the income statement. "
        "total/total_pages are null unless withTotal=true."
    ),
    responses={
        200: {"description": "Paginated cashflow entries"},
        400: {"description": "Invalid period parameters"},
    },
)
def list_cashflow(
    service: StatementsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
    category: Annotated[
        Literal["all", "income", "expense"],
        Query(description="Filter-tab category"),
    ] = "all",
    account_category: AccountCategoryParam = None,
    search: SearchParam = None,
    page: Annotated[int, Query(ge=1, le=1000, description="Page number")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 10,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Also compute total/total_pages (extra COUNT)",
        ),
    ] = False,
) -> PaginatedResponse[CashflowEntry]:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    return service.list_cashflow(
        period,
        currency,
        params,
        category=category,
        search=search,
        account_category=account_category,
    )


@router.get(
    "/cashflow/counts",
    response_model=CashflowCounts,
    summary="Cashflow filter-tab counts",
    description=(
        "Entry counts per category (all / income / expense) under the "
        "current period, search and drill-down filters. Kept off the "
        "list endpoint so the page query never pays a mandatory count."
    ),
    responses={
        200: {"description": "Category counts"},
        400: {"description": "Invalid period parameters"},
    },
)
def get_cashflow_counts(
    service: StatementsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
    account_category: AccountCategoryParam = None,
    search: SearchParam = None,
) -> CashflowCounts:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_cashflow_counts(
        period, currency, search=search, account_category=account_category
    )
