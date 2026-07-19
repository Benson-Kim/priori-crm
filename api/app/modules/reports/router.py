"""Reports API endpoints.

Read-only reporting endpoints backing the Sales, Purchases, Tax, and
Aged AR/AP pages. All endpoints require an authenticated user (via
ReportsServiceDep). No role guard — any authenticated user may read reports.

Period semantics: all period endpoints use the same RangePreset and
ResolvedPeriod infrastructure as the statements module (single source of
truth). The frontend sends range=custom with explicit dateFrom/dateTo for
month/quarter/year modes via ReportPeriodPicker.

Aged AR/AP endpoints: point-in-time, no period filter.
"""

import logging
from datetime import date
from io import BytesIO
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.common.dependencies import ReportsServiceDep
from app.common.excel import ExcelExporter
from app.common.export_limiter import run_export
from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import Currency
from app.lib.config import settings
from app.modules.reports.schemas import (
    AgedPayablesDetailResponse,
    AgedPayablesSummaryResponse,
    AgedReceivablesDetailResponse,
    AgedReceivablesSummaryResponse,
    PurchasesLedgerEntry,
    PurchasesReportSummaryResponse,
    PurchasesSourceCounts,
    SalesLedgerEntry,
    SalesReportSummaryResponse,
    SalesStatusCounts,
    TaxReportResponse,
)
from app.modules.statements.schemas import RangePreset, ResolvedPeriod

logger = logging.getLogger(__name__)

router = APIRouter()

_exporter = ExcelExporter()


# Shared query-parameter annotations (mirrors statements router)

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
PageParam = Annotated[int, Query(ge=1, le=1000, description="Page number")]
PerPageParam = Annotated[int, Query(ge=1, le=100, description="Items per page")]
WithTotalParam = Annotated[
    bool,
    Query(
        alias="withTotal", description="Also compute total/total_pages (extra COUNT)"
    ),
]


# Sales: Summary


@router.get(
    "/sales",
    response_model=SalesReportSummaryResponse,
    summary="Sales report summary",
    description=(
        "Net revenue (after discounts), tax collected, total invoiced, "
        "outstanding and overdue balances for the selected period. "
        "Includes top-customer and category breakdowns."
    ),
    responses={
        200: {"description": "Sales summary"},
        400: {"description": "Invalid period parameters"},
    },
)
def get_sales_summary(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
) -> SalesReportSummaryResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_sales_summary(period, currency)


# Sales: Ledger


@router.get(
    "/sales/ledger",
    response_model=PaginatedResponse[SalesLedgerEntry],
    summary="Sales ledger",
    description=(
        "Paginated invoice ledger with subtotal, discount, net revenue, "
        "tax and balance columns. Supports search and status filter tabs. "
        "total/total_pages null unless withTotal=true."
    ),
)
def list_sales_ledger(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
    status: Annotated[str | None, Query(description="Invoice status filter")] = None,
    search: SearchParam = None,
    page: PageParam = 1,
    per_page: PerPageParam = 10,
    with_total: WithTotalParam = False,
) -> PaginatedResponse[SalesLedgerEntry]:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    return service.list_sales_ledger(
        period, currency, params, search=search, status=status
    )


@router.get(
    "/sales/counts",
    response_model=SalesStatusCounts,
    summary="Sales filter-tab counts",
    description=(
        "Per-status counts (all/sent/partial/paid/overdue) under the "
        "current period and currency. Kept off the ledger endpoint so the "
        "page query never pays a mandatory COUNT."
    ),
)
def get_sales_counts(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
) -> SalesStatusCounts:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_sales_status_counts(period, currency)


@router.get(
    "/sales/export",
    summary="Export sales ledger to Excel",
    description="Download the full sales ledger (no pagination) as .xlsx.",
    response_class=StreamingResponse,
)
async def export_sales(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
) -> StreamingResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)

    def build_export() -> bytes:
        rows = service.export_sales_ledger(
            period, currency, batch_size=settings.BATCH_SIZE
        )
        return _exporter.export_sales_report(rows, str(currency))

    xlsx_bytes = await run_export(build_export)
    filename = f"sales-report-{period.date_from}-{period.date_to}.xlsx"

    return StreamingResponse(
        BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Purchases: Summary


@router.get(
    "/purchases",
    response_model=PurchasesReportSummaryResponse,
    summary="Purchases report summary",
    description=(
        "Expense and PO spend, tax, counts and outstanding balances. "
        "Includes top-vendor breakdown."
    ),
)
def get_purchases_summary(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
) -> PurchasesReportSummaryResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_purchases_summary(period, currency)


# Purchases: Ledger


@router.get(
    "/purchases/ledger",
    response_model=PaginatedResponse[PurchasesLedgerEntry],
    summary="Purchases ledger",
    description=(
        "Paginated combined expense + purchase-order ledger. "
        "Supports source filter (all/expense/purchase_order) and search. "
        "total/total_pages null unless withTotal=true."
    ),
)
def list_purchases_ledger(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
    source: Annotated[
        Literal["all", "expense", "purchase_order"],
        Query(description="Source filter"),
    ] = "all",
    search: SearchParam = None,
    page: PageParam = 1,
    per_page: PerPageParam = 10,
    with_total: WithTotalParam = False,
) -> PaginatedResponse[PurchasesLedgerEntry]:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    return service.list_purchases_ledger(
        period, currency, params, source=source, search=search
    )


@router.get(
    "/purchases/counts",
    response_model=PurchasesSourceCounts,
    summary="Purchases filter-tab counts",
    description="Per-source counts (all/expense/purchase_order).",
)
def get_purchases_counts(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
    search: SearchParam = None,
) -> PurchasesSourceCounts:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_purchases_source_counts(period, currency, search=search)


@router.get(
    "/purchases/export",
    summary="Export purchases ledger to Excel",
    response_class=StreamingResponse,
)
async def export_purchases(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
) -> StreamingResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)

    def build_export() -> bytes:
        rows = service.export_purchases_ledger(
            period, currency, batch_size=settings.BATCH_SIZE
        )
        return _exporter.export_purchases_report(rows, str(currency))

    xlsx_bytes = await run_export(build_export)
    filename = f"purchases-report-{period.date_from}-{period.date_to}.xlsx"
    return StreamingResponse(
        BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Tax Report


@router.get(
    "/taxes",
    response_model=TaxReportResponse,
    summary="Tax (VAT) report",
    description=(
        "VAT collected on sales vs VAT paid on purchases. Net VAT position "
        "(positive = payable to KRA, negative = credit). Always in KES "
        "(VAT is a KES obligation in Kenya). Per-type breakdowns for sales "
        "and purchases."
    ),
)
def get_tax_report(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
) -> TaxReportResponse:
    # VAT is a KES obligation in Kenya — always report in KES regardless of
    # the query parameter, matching the export endpoint and frontend usage.
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_tax_report(period, Currency.KES)


@router.get(
    "/taxes/export",
    summary="Export tax report to Excel",
    response_class=StreamingResponse,
)
async def export_taxes(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
) -> StreamingResponse:
    # Tax report always KES
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    report = service.get_tax_report(period, Currency.KES)
    xlsx_bytes = await run_export(_exporter.export_tax_report, report, "KES")
    filename = f"tax-report-{period.date_from}-{period.date_to}.xlsx"
    return StreamingResponse(
        BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Aged Receivables


@router.get(
    "/aged-receivables",
    response_model=AgedReceivablesSummaryResponse,
    summary="Aged receivables summary",
    description=(
        "Outstanding invoice balances grouped by customer and aging bucket "
        "(Current, 1-30, 31-60, 61-90, 90+ days overdue). Point-in-time: "
        "no date range filter. Includes only SENT, PARTIAL, OVERDUE invoices "
        "with balance_due > 0."
    ),
)
def get_aged_receivables(
    service: ReportsServiceDep,
    currency: CurrencyParam = Currency.KES,
) -> AgedReceivablesSummaryResponse:
    return service.get_aged_receivables(str(currency))


@router.get(
    "/aged-receivables/detail",
    response_model=AgedReceivablesDetailResponse,
    summary="Aged receivables invoice-level detail",
    description=(
        "Individual outstanding invoice rows with aging bucket and days overdue. "
        "Ordered oldest-due first. Paginated with sentinel pattern (no COUNT)."
    ),
)
def list_aged_receivables_detail(
    service: ReportsServiceDep,
    currency: CurrencyParam = Currency.KES,
    page: PageParam = 1,
    per_page: PerPageParam = 25,
) -> AgedReceivablesDetailResponse:
    params = PaginationParams(page=page, per_page=per_page, with_total=False)
    return service.list_aged_receivables_detail(str(currency), params)


# Aged Payables


@router.get(
    "/aged-payables",
    response_model=AgedPayablesSummaryResponse,
    summary="Aged payables summary",
    description=(
        "Outstanding payable balances grouped by vendor and aging bucket. "
        "Expenses: bucketed by due_date. "
        "POs (SENT + unpaid): always in 'Current' bucket (no due_date on PO model). "
        "Point-in-time: no date range filter."
    ),
)
def get_aged_payables(
    service: ReportsServiceDep,
    currency: CurrencyParam = Currency.KES,
) -> AgedPayablesSummaryResponse:
    return service.get_aged_payables(str(currency))


@router.get(
    "/aged-payables/detail",
    response_model=AgedPayablesDetailResponse,
    summary="Aged payables detail",
    description="Individual outstanding expense + PO rows with bucket and days overdue.",
)
def list_aged_payables_detail(
    service: ReportsServiceDep,
    currency: CurrencyParam = Currency.KES,
    page: PageParam = 1,
    per_page: PerPageParam = 25,
) -> AgedPayablesDetailResponse:
    params = PaginationParams(page=page, per_page=per_page, with_total=False)
    return service.list_aged_payables_detail(str(currency), params)
