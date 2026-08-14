"""Reports API endpoints.

Read-only reporting endpoints backing the Sales, Purchases, Tax, and
Aged AR/AP pages. Restricted to users with MANAGER or ADMIN roles.

Period semantics: all period endpoints use the same RangePreset and
ResolvedPeriod infrastructure as the statements module (single source of
truth). The frontend sends range=custom with explicit dateFrom/dateTo for
month/quarter/year modes via ReportPeriodPicker.

Aged AR/AP endpoints: point-in-time, no period filter.
"""

import logging
import os
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from app.common.dependencies import ReportsServiceDep
from app.common.excel import ExcelExporter
from app.common.export_limiter import run_export
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.reporting_time import reporting_date
from app.common.routing import CommitOnSuccessRoute
from app.constants.enums import Currency
from app.lib.config import settings
from app.modules.reports.audit import authorize_and_audit_report_access
from app.modules.reports.schemas import (
    AgedPayablesDetailResponse,
    AgedPayablesSummaryResponse,
    AgedReceivablesDetailResponse,
    AgedReceivablesSummaryResponse,
    ExcludedTaxTransaction,
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

router = APIRouter(
    route_class=CommitOnSuccessRoute,
    dependencies=[
        Depends(authorize_and_audit_report_access, scope="function"),
    ]
)

_exporter = ExcelExporter()
_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _temporary_xlsx_path() -> str:
    fd, path = tempfile.mkstemp(prefix="report-export-", suffix=".xlsx")
    os.close(fd)
    return path


def _remove_export(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning(
            "Failed to remove report export",
            exc_info=True,
            extra={"path": path},
        )


def _file_response(path: str, filename: str) -> FileResponse:
    return FileResponse(
        path,
        media_type=_XLSX_MEDIA_TYPE,
        filename=filename,
        background=BackgroundTask(_remove_export, path),
    )


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
StrictTaxParam = Annotated[
    bool,
    Query(
        description=(
            "Reject partial VAT reconciliations when unsupported foreign-currency "
            "documents are present"
        )
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
    response_class=FileResponse,
)
async def export_sales(
    service: ReportsServiceDep,
    request: Request,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
) -> StreamingResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)

    path = _temporary_xlsx_path()

    def build_export() -> int:
        preflight_count = service.get_sales_ledger_total(period, currency)
        service.validate_export_size(preflight_count, "Sales")
        actual_count = 0

        def counted_rows() -> Iterator[SalesLedgerEntry]:
            nonlocal actual_count
            for row in service.export_sales_ledger(
                period, currency, batch_size=settings.BATCH_SIZE
            ):
                actual_count += 1
                yield row

        _exporter.export_sales_report(counted_rows(), str(currency), destination=path)
        return actual_count

    try:
        actual_count = await run_export(build_export)
    except BaseException:
        _remove_export(path)
        raise

    request.state.report_export_metrics = {"exported_data_row_count": actual_count}
    request.state.report_export_path = path
    filename = f"sales-report-{period.date_from}-{period.date_to}.xlsx"
    return _file_response(path, filename)


# Purchases: Summary


@router.get(
    "/purchases",
    response_model=PurchasesReportSummaryResponse,
    summary="Purchases report summary",
    description=(
        "Actual expense spend, expense-only outstanding payables, and potential "
        "input VAT. Purchase orders are shown separately as commitments."
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
    response_class=FileResponse,
)
async def export_purchases(
    service: ReportsServiceDep,
    request: Request,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
) -> StreamingResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)

    path = _temporary_xlsx_path()

    def build_export() -> int:
        preflight_count = service.get_purchases_ledger_total(period, currency)
        service.validate_export_size(preflight_count, "Purchases")
        actual_count = 0

        def counted_rows() -> Iterator[PurchasesLedgerEntry]:
            nonlocal actual_count
            for row in service.export_purchases_ledger(
                period, currency, batch_size=settings.BATCH_SIZE
            ):
                actual_count += 1
                yield row

        _exporter.export_purchases_report(
            counted_rows(), str(currency), destination=path
        )
        return actual_count

    try:
        actual_count = await run_export(build_export)
    except BaseException:
        _remove_export(path)
        raise

    request.state.report_export_metrics = {"exported_data_row_count": actual_count}
    request.state.report_export_path = path
    filename = f"purchases-report-{period.date_from}-{period.date_to}.xlsx"
    return _file_response(path, filename)


# Tax Report


@router.get(
    "/taxes",
    response_model=TaxReportResponse,
    summary="Tax (VAT) reconciliation estimate",
    description=(
        "A KES VAT reconciliation estimate, not a VAT return or filing-ready "
        "output. Confirm against eTIMS, customs records, credit/debit notes, "
        "and the KRA auto-populated return. By default, unsupported foreign-"
        "currency VAT documents are excluded and disclosed as a partial result; "
        "strict=true rejects an incomplete reconciliation with HTTP 422."
    ),
    responses={422: {"description": "Strict reconciliation is incomplete"}},
)
def get_tax_report(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    currency: CurrencyParam = Currency.KES,
    strict: StrictTaxParam = False,
) -> TaxReportResponse:
    # VAT is a KES obligation in Kenya — always report in KES regardless of
    # the query parameter, matching the export endpoint and frontend usage.
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    return service.get_tax_report(period, Currency.KES, strict=strict)


@router.get(
    "/taxes/excluded",
    response_model=PaginatedResponse[ExcludedTaxTransaction],
    summary="List transactions excluded from the VAT reconciliation estimate",
    description=(
        "Paginated foreign-currency VAT documents excluded because immutable KES "
        "tax-point conversion evidence is unavailable."
    ),
)
def list_excluded_tax_transactions(
    service: ReportsServiceDep,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    page: PageParam = 1,
    per_page: PerPageParam = 25,
) -> PaginatedResponse[ExcludedTaxTransaction]:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    params = PaginationParams(page=page, per_page=per_page, with_total=True)
    return service.list_excluded_tax_transactions(period, params)


@router.get(
    "/taxes/export",
    summary="Export VAT reconciliation estimate to Excel",
    response_class=FileResponse,
)
async def export_taxes(
    service: ReportsServiceDep,
    request: Request,
    range_preset: RangeParam = RangePreset.THIS_MONTH,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    strict: StrictTaxParam = False,
) -> StreamingResponse:
    period = ResolvedPeriod.resolve(range_preset, date_from, date_to)
    path = _temporary_xlsx_path()

    def build_export() -> tuple[str, dict[str, int]]:
        report = service.get_tax_report(period, Currency.KES, strict=strict)
        fixed_row_count = (
            3 + len(report.sales_by_tax_type) + len(report.purchases_by_tax_type)
        )
        service.validate_export_size(
            fixed_row_count + report.completeness.excluded_document_count,
            "VAT reconciliation",
            max_rows=settings.TAX_REPORT_EXPORT_MAX_ROWS,
        )

        actual_excluded_count = 0

        def counted_excluded_rows() -> Iterator[ExcludedTaxTransaction]:
            nonlocal actual_excluded_count
            if report.completeness.status != "partial":
                return
            for row in service.iter_excluded_tax_transactions(
                period, batch_size=settings.BATCH_SIZE
            ):
                actual_excluded_count += 1
                service.validate_export_size(
                    fixed_row_count + actual_excluded_count,
                    "VAT reconciliation",
                    max_rows=settings.TAX_REPORT_EXPORT_MAX_ROWS,
                )
                yield row

        _exporter.export_tax_report(
            report,
            "KES",
            counted_excluded_rows(),
            destination=path,
        )
        return report.completeness.status, {
            "summary_metric_row_count": 3,
            "sales_breakdown_row_count": len(report.sales_by_tax_type),
            "purchase_breakdown_row_count": len(report.purchases_by_tax_type),
            "excluded_document_count": actual_excluded_count,
            "workbook_data_row_count": fixed_row_count + actual_excluded_count,
        }

    try:
        completeness_status, export_metrics = await run_export(build_export)
    except BaseException:
        _remove_export(path)
        raise

    request.state.report_export_metrics = export_metrics
    request.state.report_export_path = path
    prefix = "partial-" if completeness_status == "partial" else ""
    filename = (
        f"{prefix}vat-reconciliation-estimate-{period.date_from}-{period.date_to}.xlsx"
    )
    return _file_response(path, filename)


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
    as_of_date = reporting_date()
    return service.get_aged_receivables(str(currency), as_of_date)


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
    as_of_date = reporting_date()
    params = PaginationParams(page=page, per_page=per_page, with_total=False)
    return service.list_aged_receivables_detail(str(currency), as_of_date, params)


# Aged Payables


@router.get(
    "/aged-payables",
    response_model=AgedPayablesSummaryResponse,
    summary="Aged payables summary",
    description=(
        "Outstanding payable balances grouped by vendor and aging bucket. "
        "Expenses: bucketed by due_date. "
        "Point-in-time: no date range filter."
    ),
)
def get_aged_payables(
    service: ReportsServiceDep,
    currency: CurrencyParam = Currency.KES,
) -> AgedPayablesSummaryResponse:
    as_of_date = reporting_date()
    return service.get_aged_payables(str(currency), as_of_date)


@router.get(
    "/aged-payables/detail",
    response_model=AgedPayablesDetailResponse,
    summary="Aged payables detail",
    description="Individual outstanding expense rows with bucket and days overdue.",
)
def list_aged_payables_detail(
    service: ReportsServiceDep,
    currency: CurrencyParam = Currency.KES,
    page: PageParam = 1,
    per_page: PerPageParam = 25,
) -> AgedPayablesDetailResponse:
    as_of_date = reporting_date()
    params = PaginationParams(page=page, per_page=per_page, with_total=False)
    return service.list_aged_payables_detail(str(currency), as_of_date, params)
