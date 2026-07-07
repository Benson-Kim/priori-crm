"""
Vendor API endpoints.
"""

import io
import logging
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from app.common.dependencies import VendorServiceDep, require_role
from app.common.export_limiter import run_export
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.statement import default_statement_period
from app.constants.enums import UserRole
from app.lib.config import settings
from app.modules.vendors.schemas import (
    ContactSearchResponse,
    VendorCardSummary,
    VendorCreate,
    VendorDeleteResponse,
    VendorDuplicateCheckResponse,
    VendorFilterParams,
    VendorPayablesSummary,
    VendorResponse,
    VendorStatement,
    VendorStatusCounts,
    VendorSummary,
    VendorTransactionFilterParams,
    VendorTransactionSummary,
    VendorUpdate,
)

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

logger = logging.getLogger(__name__)

router = APIRouter()


# CREATE


@router.post(
    "",
    response_model=VendorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new vendor",
    description=(
        "Create a new vendor record. Covers both modal paths: "
        "new from scratch (contactId omitted) and from existing contact "
        "(contactId provided). All fields except vendorName are optional."
    ),
    responses={
        201: {"description": "Vendor created successfully"},
        400: {
            "description": "Validation failed (e.g. blank vendor name, invalid email)"
        },
        404: {"description": "Linked contact not found (when contactId is supplied)"},
        409: {"description": "A vendor with this email already exists"},
    },
)
def create_vendor(
    body: VendorCreate,
    service: VendorServiceDep,
) -> VendorResponse:
    """Create a vendor."""
    vendor = service.create(body, user_id=service._actor_id)
    return VendorResponse.model_validate(vendor)


# LIST


@router.get(
    "",
    response_model=PaginatedResponse[VendorSummary],
    summary="List vendors",
    description=(
        "Paginated, searchable list of vendors. "
        "Maps to the Vendors list view. "
        "Filter by status to drive the All / Active / Inactive tab bar."
    ),
    responses={
        200: {"description": "Paginated vendor list"},
    },
)
def list_vendors(
    service: VendorServiceDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    per_page: Annotated[
        int,
        Query(ge=1, le=100, description="Rows per page (default: 10)"),
    ] = 10,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description="Filter by status: 'active' | 'inactive'. Omit for all.",
        ),
    ] = None,
    search: Annotated[
        str | None,
        Query(
            description=(
                "Search across vendor name, email, and phone. "
                "Maps to the search bar on the list view."
            )
        ),
    ] = None,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Include total/total_pages (runs a COUNT(*); off by default)",
        ),
    ] = False,
) -> PaginatedResponse[VendorSummary]:
    """List vendors with pagination, status filtering, and search."""
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    filters = VendorFilterParams(status=status_filter, search=search)
    return service.list_vendors(params, filters)


# STATUS COUNTS


@router.get(
    "/counts",
    response_model=VendorStatusCounts,
    summary="Get vendor status counts",
    description=(
        "Returns counts for each filter tab: All, Active, Inactive. "
        "Used to render the live-updating counts in the tab bar"
    ),
    responses={
        200: {"description": "Vendor counts by status"},
    },
)
def get_vendor_counts(service: VendorServiceDep) -> VendorStatusCounts:
    """Get vendor counts grouped by status."""
    return service.get_status_counts()


# CONTACT SEARCH


@router.get(
    "/contacts/search",
    response_model=ContactSearchResponse,
    summary="Search existing contacts for vendor modal",
    description=(
        "Searches CRM contacts by name, phone, or email. "
        "Powers the 'Search Existing Vendor' dropdown in the Add Vendor modal "
        "Returns contacts not yet linked to a vendor."
    ),
    responses={
        200: {"description": "Matching contacts"},
        400: {"description": "Query string too short"},
    },
)
def search_contacts(
    service: VendorServiceDep,
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=100,
            description="Search term (name, phone, or email)",
        ),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=50, description="Maximum results to return"),
    ] = 20,
) -> ContactSearchResponse:
    """
    Search contacts for the Add Vendor modal search dropdown.
    """
    return service.search_contacts(query=q, limit=limit)


# DUPLICATE EMAIL CHECK


@router.get(
    "/check-email",
    response_model=VendorDuplicateCheckResponse,
    summary="Check for duplicate vendor email",
    description=(
        "Real-time duplicate email check — called on-blur from the email field "
        "in the Add / Edit Vendor modal "
        "Returns is_duplicate=true with the existing vendor's name and ID "
        "so the frontend can offer a 'View existing record?' prompt."
    ),
    responses={
        200: {"description": "Duplicate check result"},
    },
)
def check_email_duplicate(
    service: VendorServiceDep,
    email: Annotated[
        str,
        Query(description="Email address to check"),
    ],
    exclude_vendor_id: Annotated[
        UUID | None,
        Query(
            alias="excludeVendorId",
            description="Vendor ID to exclude (pass when editing an existing vendor)",
        ),
    ] = None,
) -> VendorDuplicateCheckResponse:
    """
    Check whether a vendor with the given email already exists.
    """
    result = service.check_email_duplicate(email, exclude_vendor_id)
    return VendorDuplicateCheckResponse(**result)


# GET BY ID


@router.get(
    "/{vendor_id}",
    response_model=VendorResponse,
    summary="Get vendor details",
    description=(
        "Retrieve the complete vendor record with computed payables aggregates. "
        "Powers the vendor detail view Overview tab"
    ),
    responses={
        200: {"description": "Vendor details"},
        404: {"description": "Vendor not found"},
    },
)
def get_vendor(
    vendor_id: UUID,
    service: VendorServiceDep,
) -> VendorResponse:
    """Get a vendor by ID."""
    vendor = service.get_by_id(vendor_id)
    return VendorResponse.model_validate(vendor)


# UPDATE


@router.put(
    "/{vendor_id}",
    response_model=VendorResponse,
    summary="Update vendor",
    description=(
        "Partial update — only supplied fields are written. "
        "Pass X-Expected-Version header for optimistic locking."
    ),
    responses={
        200: {"description": "Vendor updated successfully"},
        400: {"description": "Validation failed"},
        404: {"description": "Vendor not found"},
        409: {"description": "Version conflict or duplicate email"},
    },
)
def update_vendor(
    vendor_id: UUID,
    body: VendorUpdate,
    service: VendorServiceDep,
    expected_version: Annotated[
        int | None,
        Query(
            alias="expectedVersion",
            description=(
                "Current version number for optimistic locking. "
                "If provided and the vendor has been updated since you loaded it, "
                "the request is rejected with HTTP 409."
            ),
        ),
    ] = None,
) -> VendorResponse:
    """Update an existing vendor."""
    vendor = service.update(
        vendor_id, body, user_id=service._actor_id, expected_version=expected_version
    )
    return VendorResponse.model_validate(vendor)


# DELETE


@router.delete(
    "/{vendor_id}",
    response_model=VendorDeleteResponse,
    summary="Delete vendor",
    description=(
        "Permanently delete a vendor. "
        "Blocked if the vendor has any open (pending or overdue) transactions — "
        "The caller should show the Delete Confirmation modal "
        "before calling this endpoint."
    ),
    responses={
        200: {"description": "Vendor deleted successfully"},
        400: {"description": "Vendor has open transactions — deletion blocked"},
        403: {"description": "Insufficient role to delete vendors"},
        404: {"description": "Vendor not found"},
    },
    dependencies=[Depends(require_role(UserRole.MANAGER, UserRole.ADMIN))],
)
def delete_vendor(
    vendor_id: UUID,
    service: VendorServiceDep,
) -> VendorDeleteResponse:
    """
    Permanently delete a vendor.
    """
    result = service.delete(vendor_id, user_id=service._actor_id)
    return VendorDeleteResponse(**result)


# ACTIVATE


@router.post(
    "/{vendor_id}/activate",
    response_model=VendorResponse,
    summary="Activate vendor",
    description=(
        "Set vendor status to active. "
        "Allowed only when current status is inactive. "
        "No confirmation modal required — action is always reversible."
    ),
    responses={
        200: {"description": "Vendor activated"},
        400: {"description": "Vendor is already active"},
        404: {"description": "Vendor not found"},
    },
)
def activate_vendor(
    vendor_id: UUID,
    service: VendorServiceDep,
) -> VendorResponse:
    """Activate a vendor"""
    vendor = service.activate(vendor_id, user_id=service._actor_id)
    return VendorResponse.model_validate(vendor)


# DEACTIVATE


@router.post(
    "/{vendor_id}/deactivate",
    response_model=VendorResponse,
    summary="Deactivate vendor",
    description=(
        "Set vendor status to inactive. "
        "Allowed only when current status is active. "
        "Reversible — no confirmation modal "
        "Historical transactions and detail view remain accessible."
    ),
    responses={
        200: {"description": "Vendor deactivated"},
        400: {"description": "Vendor is already inactive"},
        404: {"description": "Vendor not found"},
    },
)
def deactivate_vendor(
    vendor_id: UUID,
    service: VendorServiceDep,
) -> VendorResponse:
    """Deactivate a vendor."""
    vendor = service.deactivate(vendor_id, user_id=service._actor_id)
    return VendorResponse.model_validate(vendor)


# TRANSACTION LIST


@router.get(
    "/{vendor_id}/transactions",
    response_model=PaginatedResponse[VendorTransactionSummary],
    summary="Get vendor transaction list",
    description=(
        "Paginated list of the vendor's transactions — expenses and purchase "
        "orders (and bills once that module lands) — in one source-tagged "
        "list. Each row carries transaction_type ('expense' | "
        "'purchase_order') plus computed days_overdue and status_display "
        "fields. Purchase orders are non-payable commitments and report a "
        "0.00 balance."
    ),
    responses={
        200: {"description": "Paginated transaction list"},
        404: {"description": "Vendor not found"},
    },
)
def get_vendor_transactions(
    vendor_id: UUID,
    service: VendorServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 10,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description=(
                "Filter by transaction status. Payable: 'paid' | 'pending' | "
                "'overdue'. Purchase order: 'draft' | 'sent' | 'billed' | "
                "'canceled'. Omit for all."
            ),
        ),
    ] = None,
    type_filter: Annotated[
        str | None,
        Query(
            alias="type",
            description=(
                "Filter by source type: 'expense' | 'purchase_order'. Omit for all."
            ),
        ),
    ] = None,
) -> PaginatedResponse[VendorTransactionSummary]:
    """
    Get the paginated transaction list for a vendor.
    """
    params = PaginationParams(page=page, per_page=per_page)
    filters = VendorTransactionFilterParams(
        status=status_filter,
        transaction_type=type_filter,
    )
    return service.get_vendor_transactions(vendor_id, params, filters)


# PAYABLES SUMMARY


@router.get(
    "/{vendor_id}/payables",
    response_model=VendorPayablesSummary,
    summary="Get vendor payables summary",
    description=(
        "Returns the Total Unpaid and Overdue amounts for the two summary cards "
        "Values are always computed fresh — never cached on the vendor row."
    ),
    responses={
        200: {"description": "Payables summary"},
        404: {"description": "Vendor not found"},
    },
)
def get_vendor_payables(
    vendor_id: UUID,
    service: VendorServiceDep,
) -> VendorPayablesSummary:
    """
    Get the payables summary cards
    """
    return service.get_payables_summary(vendor_id)


# DETAIL CARDS (Total POs / Total Payments / Total Bills)
#
# Three cards on the vendor detail Overview tab. Each returns an aggregate
# block (total / paid / pending / count) plus a paginated, paid/pending-tagged
# row list, filtered by an independent date range (defaults to last 12 months,
# mirroring the statement endpoint). Excel + PDF exports honour the same range.


def _card_period(
    period_start: date | None, period_end: date | None
) -> tuple[date, date]:
    """Resolve the card date filter, defaulting to the last 12 months."""
    return default_statement_period(period_start, period_end)


@router.get(
    "/{vendor_id}/cards/purchase-orders",
    response_model=VendorCardSummary,
    summary="Total POs card",
    description=(
        "Aggregate + paginated list of the vendor's purchase orders in the "
        "period (DRAFT excluded), each row tagged paid/pending."
    ),
    responses={
        200: {"description": "PO card"},
        404: {"description": "Vendor not found"},
    },
)
def get_vendor_po_card(
    vendor_id: UUID,
    service: VendorServiceDep,
    period_start: Annotated[date | None, Query(alias="period_start")] = None,
    period_end: Annotated[date | None, Query(alias="period_end")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 10,
) -> VendorCardSummary:
    start, end = _card_period(period_start, period_end)
    return service.get_purchase_orders_card(vendor_id, start, end, page, per_page)


@router.get(
    "/{vendor_id}/cards/payments",
    response_model=VendorCardSummary,
    summary="Total Payments card",
    description=(
        "Aggregate + paginated list of all payments made to the vendor in the "
        "period — PO payments and expense payments combined. Every row is paid."
    ),
    responses={
        200: {"description": "Payments card"},
        404: {"description": "Vendor not found"},
    },
)
def get_vendor_payments_card(
    vendor_id: UUID,
    service: VendorServiceDep,
    period_start: Annotated[date | None, Query(alias="period_start")] = None,
    period_end: Annotated[date | None, Query(alias="period_end")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 10,
) -> VendorCardSummary:
    start, end = _card_period(period_start, period_end)
    return service.get_payments_card(vendor_id, start, end, page, per_page)


@router.get(
    "/{vendor_id}/cards/bills",
    response_model=VendorCardSummary,
    summary="Total Bills/Invoices card",
    description=(
        "Aggregate + paginated list of the vendor's bills (expenses) in the "
        "period (CANCELED excluded), each row tagged paid/pending."
    ),
    responses={
        200: {"description": "Bills card"},
        404: {"description": "Vendor not found"},
    },
)
def get_vendor_bills_card(
    vendor_id: UUID,
    service: VendorServiceDep,
    period_start: Annotated[date | None, Query(alias="period_start")] = None,
    period_end: Annotated[date | None, Query(alias="period_end")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 10,
) -> VendorCardSummary:
    start, end = _card_period(period_start, period_end)
    return service.get_bills_card(vendor_id, start, end, page, per_page)


# CARD EXPORTS — Excel + PDF, one pair per card, sharing the same date filter.

_CARD_KEYS = {
    "purchase-orders": "purchase_orders",
    "payments": "payments",
    "bills": "bills",
}


async def _card_excel(
    service, vendor_id: UUID, card: str, start: date, end: date
) -> StreamingResponse:
    xlsx, stem = await run_export(
        service.build_card_excel, vendor_id, card, start, end, settings.BATCH_SIZE
    )
    filename = f"{stem}_{date.today().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx),
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _card_pdf(
    service, vendor_id: UUID, card: str, start: date, end: date
) -> StreamingResponse:
    pdf, stem = await run_export(
        service.build_card_pdf, vendor_id, card, start, end, settings.BATCH_SIZE
    )
    filename = f"{stem}_{date.today().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{vendor_id}/cards/{card}/export/excel",
    summary="Export a vendor card to Excel",
    description="Export a detail card (purchase-orders | payments | bills) as .xlsx.",
    responses={
        200: {"description": "Excel file", "content": {_XLSX_MEDIA_TYPE: {}}},
        404: {"description": "Vendor not found"},
    },
)
async def export_vendor_card_excel(
    vendor_id: UUID,
    card: str,
    service: VendorServiceDep,
    period_start: Annotated[date | None, Query(alias="period_start")] = None,
    period_end: Annotated[date | None, Query(alias="period_end")] = None,
) -> StreamingResponse:
    card_key = _CARD_KEYS.get(card, card)
    start, end = _card_period(period_start, period_end)
    return await _card_excel(service, vendor_id, card_key, start, end)


@router.get(
    "/{vendor_id}/cards/{card}/export/pdf",
    summary="Download a vendor card as PDF",
    description="Download a detail card (purchase-orders | payments | bills) as PDF.",
    responses={
        200: {"description": "PDF file", "content": {"application/pdf": {}}},
        404: {"description": "Vendor not found"},
    },
)
async def export_vendor_card_pdf(
    vendor_id: UUID,
    card: str,
    service: VendorServiceDep,
    period_start: Annotated[date | None, Query(alias="period_start")] = None,
    period_end: Annotated[date | None, Query(alias="period_end")] = None,
) -> StreamingResponse:
    card_key = _CARD_KEYS.get(card, card)
    start, end = _card_period(period_start, period_end)
    return await _card_pdf(service, vendor_id, card_key, start, end)


# STATEMENT


@router.get(
    "/{vendor_id}/statement",
    response_model=VendorStatement,
    summary="Generate vendor statement",
    description=(
        "Generates a statement of account for a period. "
        "If period_start / period_end are omitted, defaults to the last "
        "12 months, mirroring the customer statement endpoint."
    ),
    responses={
        200: {"description": "Vendor statement"},
        404: {"description": "Vendor not found"},
    },
)
def get_vendor_statement(
    vendor_id: UUID,
    service: VendorServiceDep,
    period_start: Annotated[
        date | None,
        Query(
            alias="period_start",
            description="Start date for the statement period",
        ),
    ] = None,
    period_end: Annotated[
        date | None,
        Query(
            alias="period_end",
            description="End date for the statement period",
        ),
    ] = None,
) -> VendorStatement:
    """Generate a vendor statement, defaulting to the last 12 months."""
    period_start, period_end = default_statement_period(period_start, period_end)
    return service.generate_statement(
        vendor_id=vendor_id,
        period_start=period_start,
        period_end=period_end,
    )
