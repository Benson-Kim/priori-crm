"""Sales Desk analytics API endpoints (issue #45).

Read-only aggregates backing the Sales Desk dashboard (Dashboard.svg), the
notification bell / sidebar badges, the Pipeline workspace header (App.svg)
and the two CSV exports. Every endpoint resolves its service through
``SalesDeskServiceDep``, which (on PostgreSQL) opens ONE read-only
REPEATABLE READ transaction — mirroring the reports module — so each
response is internally consistent under a single snapshot.

Export safety (mirrors the reports export endpoints):
- exact preflight row-count limit, re-checked while streaming;
- generation runs off-thread under the export concurrency cap
  (``common/export_limiter``);
- temp-file-backed FileResponse with background cleanup, and explicit
  cleanup on EVERY failure path;
- append-only, fail-closed, same-session access auditing
  (``modules/sales_desk/audit.py``).
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.common.dependencies import SalesDeskServiceDep
from app.common.export_limiter import run_export
from app.common.reporting_time import reporting_date
from app.common.routing import CommitOnSuccessRoute
from app.constants.enums import DealHygieneBucket, DealTab
from app.lib.config import settings
from app.modules.deals.schemas import DealFilterParams
from app.modules.sales_desk.audit import audit_sales_desk_export
from app.modules.sales_desk.schemas import (
    DeskCompaniesResponse,
    DeskCompanyRow,
    PipelineOverviewResponse,
    SalesDeskDashboardResponse,
    SalesDeskNotificationsResponse,
)
from app.modules.sales_desk.service import (
    PIPELINE_EXPORT_COLUMNS,
    write_csv_rows,
)

logger = logging.getLogger(__name__)

router = APIRouter(route_class=CommitOnSuccessRoute)

_CSV_MEDIA_TYPE = "text/csv"

OwnerParam = Annotated[
    UUID | None,
    Query(alias="owner", description="Filter to one sales rep (deal/company owner)"),
]


def _temporary_csv_path() -> str:
    fd, path = tempfile.mkstemp(prefix="sales-desk-export-", suffix=".csv")
    os.close(fd)
    return path


def _remove_export(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning(
            "Failed to remove sales desk export",
            exc_info=True,
            extra={"path": path},
        )


def _file_response(path: str, filename: str) -> FileResponse:
    return FileResponse(
        path,
        media_type=_CSV_MEDIA_TYPE,
        filename=filename,
        background=BackgroundTask(_remove_export, path),
    )


@router.get(
    "/dashboard",
    response_model=SalesDeskDashboardResponse,
    summary="Sales Desk dashboard aggregates",
    description=(
        "Everything the Sales Desk dashboard renders, computed server-side "
        "under one read snapshot: the four KPI cards (weighted pipeline, "
        "total ARR pipeline, won/lost this period — current org-local "
        "quarter), the rolling 12-month bookings chart (real close dates, "
        "org-local month boundaries), the active pipeline by stage (open "
        "stages only; parked deals excluded), each rep's won value against "
        "their quarterly target (owner settings), and the most recently "
        "added companies. All values are USD equivalents."
    ),
    responses={200: {"description": "Dashboard payload"}},
)
def get_dashboard(
    service: SalesDeskServiceDep,
    owner: OwnerParam = None,
) -> SalesDeskDashboardResponse:
    return service.get_dashboard(owner)


@router.get(
    "/notifications",
    response_model=SalesDeskNotificationsResponse,
    summary="Notification bell + sidebar badge counts",
    description=(
        "The bell badge total and the grouped counts feeding the sidebar "
        "badges: companies with unsynced billing profiles, due future-"
        "pipeline items (due nurture prospects + due parked deals), items "
        "due within the next 14 days, and open deals idle for more than 30 "
        "days. The total always equals the sum of the four groups — the "
        "single source of truth for the shared-chrome badges."
    ),
    responses={200: {"description": "Notification counts"}},
)
def get_notifications(
    service: SalesDeskServiceDep,
    owner: OwnerParam = None,
) -> SalesDeskNotificationsResponse:
    return service.get_notifications(owner)


@router.get(
    "/companies",
    response_model=DeskCompaniesResponse,
    summary="Sales Desk companies directory",
    description=(
        "The Companies table (Companies.svg) in one round trip: each company "
        "with its industry, primary contact, both billing profiles and their "
        "accounting sync state, the owning rep and its deal counts. The "
        "customers list endpoint returns a leaner summary built for the "
        "accounting screens, which is why the desk has its own row shape. "
        "`unsynced=true` narrows to companies with at least one profile not "
        "yet pushed to accounting, the same semantics as the Needs sync tab "
        "and the sidebar badge."
    ),
    responses={200: {"description": "Companies directory"}},
)
def get_companies(
    service: SalesDeskServiceDep,
    owner: OwnerParam = None,
    search: Annotated[
        str | None, Query(description="Match company, contact, industry or tenant")
    ] = None,
    unsynced: Annotated[
        bool, Query(description="Only companies with an unsynced billing profile")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=500, description="Maximum rows")] = 200,
) -> DeskCompaniesResponse:
    return service.get_companies(owner, search, unsynced, limit)


@router.get(
    "/companies/{company_id}",
    response_model=DeskCompanyRow,
    summary="One Sales Desk company row",
    description=(
        "A single company exactly as the directory lists it — industry, "
        "primary contact, billing profiles with their accounting sync "
        "state, owning rep and deal counts — assembled by the same path "
        "as `GET /sales-desk/companies`, so the drawer and the table can "
        "never disagree about a company. 404 when the company does not "
        "exist."
    ),
    responses={
        200: {"description": "Company row"},
        404: {"description": "Company not found"},
    },
)
def get_company(
    service: SalesDeskServiceDep,
    company_id: UUID,
) -> DeskCompanyRow:
    return service.get_company(company_id)


@router.get(
    "/pipeline/overview",
    response_model=PipelineOverviewResponse,
    summary="Pipeline workspace aggregates",
    description=(
        "The Pipeline page header in one round trip: per-rep scoreboard "
        "cards and the whole-team card (never owner-scoped — they are the "
        "selector), the per-stage summary strip with the CLOSED column, "
        "the activity-hygiene chip counts (same semantics as the list "
        "filters) and the open-pipeline total. Parked deals are excluded "
        "from every open aggregate."
    ),
    responses={200: {"description": "Pipeline workspace aggregates"}},
)
def get_pipeline_overview(
    service: SalesDeskServiceDep,
    owner: OwnerParam = None,
) -> PipelineOverviewResponse:
    return service.get_pipeline_overview(owner)


@router.get(
    "/exports/pipeline",
    summary="Export the pipeline deal list to CSV",
    description=(
        "Download the pipeline list as CSV, honouring the active list "
        "filters (tab, owner, activity-hygiene bucket, show-closed, "
        "search). Reads the exact filtered + ordered query the list "
        "serves, under one read snapshot."
    ),
    response_class=FileResponse,
    dependencies=[Depends(audit_sales_desk_export, scope="function")],
    responses={
        200: {"description": "CSV download"},
        400: {"description": "Export exceeds the row limit"},
        503: {"description": "Export capacity exhausted; retry shortly"},
    },
)
async def export_pipeline(
    service: SalesDeskServiceDep,
    request: Request,
    tab: Annotated[
        DealTab, Query(description="Pipeline tab: all | open | won | lost")
    ] = DealTab.ALL,
    owner_id: Annotated[
        UUID | None,
        Query(description="Filter by deal owner (sales rep)", alias="ownerId"),
    ] = None,
    hygiene: Annotated[
        DealHygieneBucket | None,
        Query(
            description=(
                "Activity-hygiene bucket (open deals only): active_week | "
                "quiet_8_30 | no_activity_30 | open_45"
            )
        ),
    ] = None,
    show_closed: Annotated[
        bool,
        Query(
            alias="showClosed",
            description="On the 'all' tab, include closed (won/lost) deals",
        ),
    ] = True,
    search: Annotated[
        str | None,
        Query(description="Search company name, contact or product"),
    ] = None,
) -> FileResponse:
    filters = DealFilterParams(
        tab=tab,
        owner_id=owner_id,
        hygiene=hygiene,
        show_closed=show_closed,
        search=search,
    )

    path = _temporary_csv_path()

    def build_export() -> int:
        preflight_count = service.count_pipeline_export_rows(filters)
        service.validate_export_size(preflight_count, "Pipeline")
        actual_count = 0

        def counted_rows():
            nonlocal actual_count
            yield PIPELINE_EXPORT_COLUMNS
            for row in service.iter_pipeline_export_rows(
                filters, batch_size=settings.BATCH_SIZE
            ):
                actual_count += 1
                service.validate_export_size(actual_count, "Pipeline")
                yield row

        write_csv_rows(counted_rows(), path)
        return actual_count

    try:
        actual_count = await run_export(build_export)
    except BaseException:
        _remove_export(path)
        raise

    request.state.sales_desk_export_metrics = {"exported_data_row_count": actual_count}
    request.state.sales_desk_export_path = path
    return _file_response(path, f"pipeline-export-{reporting_date()}.csv")


@router.get(
    "/exports/dashboard",
    summary="Export the dashboard aggregates to CSV",
    description=(
        "Download the dashboard payload (KPIs, 12-month bookings, pipeline "
        "by stage, rep vs. quota, recently added companies) as a sectioned "
        "CSV, honouring the owner filter. Computed under one read snapshot."
    ),
    response_class=FileResponse,
    dependencies=[Depends(audit_sales_desk_export, scope="function")],
    responses={
        200: {"description": "CSV download"},
        400: {"description": "Export exceeds the row limit"},
        503: {"description": "Export capacity exhausted; retry shortly"},
    },
)
async def export_dashboard(
    service: SalesDeskServiceDep,
    request: Request,
    owner: OwnerParam = None,
) -> FileResponse:
    path = _temporary_csv_path()

    def build_export() -> int:
        rows = service.dashboard_export_rows(owner)
        service.validate_export_size(len(rows), "Dashboard")
        return write_csv_rows(rows, path)

    try:
        actual_count = await run_export(build_export)
    except BaseException:
        _remove_export(path)
        raise

    request.state.sales_desk_export_metrics = {"exported_data_row_count": actual_count}
    request.state.sales_desk_export_path = path
    return _file_response(path, f"dashboard-export-{reporting_date()}.csv")
