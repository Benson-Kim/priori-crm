"""
Vendor API endpoints.
"""

import logging
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.common.dependencies import VendorServiceDep, require_role
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.statement import default_statement_period
from app.constants.enums import UserRole
from app.modules.vendors.schemas import (
    ContactSearchResponse,
    VendorCreate,
    VendorDeleteResponse,
    VendorDuplicateCheckResponse,
    VendorFilterParams,
    VendorInvoicesCard,
    VendorPayablesSummary,
    VendorPaymentsCard,
    VendorPurchaseOrdersCard,
    VendorResponse,
    VendorStatement,
    VendorStatusCounts,
    VendorSummary,
    VendorTransactionFilterParams,
    VendorTransactionSummary,
    VendorUpdate,
)

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


# SUPPLIER STATEMENTS CARDS (PO-33)


@router.get(
    "/{vendor_id}/cards/purchase-orders",
    response_model=VendorPurchaseOrdersCard,
    summary="Vendor 'Total POs' card",
    description=(
        "Non-DRAFT purchase orders for the vendor (PO Ref, Date, Amount) plus "
        "a paid / pending / overpaid summary. Filter by order_date via "
        "dateFrom / dateTo. Independent of the other cards."
    ),
    responses={
        200: {"description": "Purchase-orders card data"},
        404: {"description": "Vendor not found"},
    },
)
def get_vendor_purchase_orders_card(
    vendor_id: UUID,
    service: VendorServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
) -> VendorPurchaseOrdersCard:
    return service.get_purchase_orders_card(vendor_id, date_from, date_to)


@router.get(
    "/{vendor_id}/cards/payments",
    response_model=VendorPaymentsCard,
    summary="Vendor 'Total Payments' card",
    description=(
        "The vendor's purchase-order payments (Date, Invoice #, Payment Ref #, "
        "Amount, Document). Filter by payment_date via dateFrom / dateTo."
    ),
    responses={
        200: {"description": "Payments card data"},
        404: {"description": "Vendor not found"},
    },
)
def get_vendor_payments_card(
    vendor_id: UUID,
    service: VendorServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
) -> VendorPaymentsCard:
    return service.get_payments_card(vendor_id, date_from, date_to)


@router.get(
    "/{vendor_id}/cards/invoices",
    response_model=VendorInvoicesCard,
    summary="Vendor 'Total Invoices' card",
    description=(
        "The vendor's invoices/bills (from the expenses ledger). Filter by "
        "invoice date via dateFrom / dateTo."
    ),
    responses={
        200: {"description": "Invoices card data"},
        404: {"description": "Vendor not found"},
    },
)
def get_vendor_invoices_card(
    vendor_id: UUID,
    service: VendorServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
) -> VendorInvoicesCard:
    return service.get_invoices_card(vendor_id, date_from, date_to)


@router.get(
    "/{vendor_id}/cards/purchase-orders/export/excel",
    summary="Export the vendor 'Total POs' card to Excel",
    responses={
        200: {
            "description": "Excel file",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
        },
        404: {"description": "Vendor not found"},
    },
)
async def export_vendor_purchase_orders_card(
    vendor_id: UUID,
    service: VendorServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
):
    import io

    from fastapi.responses import StreamingResponse

    from app.common.excel import ExcelExporter
    from app.common.export_limiter import run_export

    card = service.get_purchase_orders_card(vendor_id, date_from, date_to)
    exporter = ExcelExporter()
    xlsx = await run_export(exporter.export_vendor_purchase_orders, card.rows)
    filename = f"Vendor_{vendor_id}_PurchaseOrders_{date.today():%Y%m%d}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{vendor_id}/cards/payments/export/excel",
    summary="Export the vendor 'Total Payments' card to Excel",
    responses={
        200: {
            "description": "Excel file",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
        },
        404: {"description": "Vendor not found"},
    },
)
async def export_vendor_payments_card(
    vendor_id: UUID,
    service: VendorServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
):
    import io

    from fastapi.responses import StreamingResponse

    from app.common.excel import ExcelExporter
    from app.common.export_limiter import run_export

    card = service.get_payments_card(vendor_id, date_from, date_to)
    exporter = ExcelExporter()
    xlsx = await run_export(exporter.export_vendor_payments, card.rows)
    filename = f"Vendor_{vendor_id}_Payments_{date.today():%Y%m%d}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{vendor_id}/cards/invoices/export/excel",
    summary="Export the vendor 'Total Invoices' card to Excel",
    responses={
        200: {
            "description": "Excel file",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
        },
        404: {"description": "Vendor not found"},
    },
)
async def export_vendor_invoices_card(
    vendor_id: UUID,
    service: VendorServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
):
    import io

    from fastapi.responses import StreamingResponse

    from app.common.excel import ExcelExporter
    from app.common.export_limiter import run_export

    card = service.get_invoices_card(vendor_id, date_from, date_to)
    exporter = ExcelExporter()
    xlsx = await run_export(exporter.export_vendor_invoices, card.rows)
    filename = f"Vendor_{vendor_id}_Invoices_{date.today():%Y%m%d}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx),
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Per-card PDF exports (PO-33). Each renders a branded PDF of the card for its
# own dateFrom/dateTo, off the event loop via the shared run_export limiter.


def _pdf_response(pdf_bytes: bytes, filename: str):
    import io

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{vendor_id}/cards/purchase-orders/pdf",
    summary="Download the vendor 'Total POs' card as PDF",
    responses={
        200: {"description": "PDF file", "content": {"application/pdf": {}}},
        404: {"description": "Vendor not found"},
    },
)
async def download_vendor_purchase_orders_card_pdf(
    vendor_id: UUID,
    service: VendorServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
):
    from app.common.card_pdf import CardPdfExporter
    from app.common.export_limiter import run_export

    card = service.get_purchase_orders_card(vendor_id, date_from, date_to)
    exporter = CardPdfExporter(service._db)
    pdf = await run_export(
        exporter.export_purchase_orders, card, date_from, date_to
    )
    filename = f"Vendor_{vendor_id}_PurchaseOrders_{date.today():%Y%m%d}.pdf"
    return _pdf_response(pdf, filename)


@router.get(
    "/{vendor_id}/cards/payments/pdf",
    summary="Download the vendor 'Total Payments' card as PDF",
    responses={
        200: {"description": "PDF file", "content": {"application/pdf": {}}},
        404: {"description": "Vendor not found"},
    },
)
async def download_vendor_payments_card_pdf(
    vendor_id: UUID,
    service: VendorServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
):
    from app.common.card_pdf import CardPdfExporter
    from app.common.export_limiter import run_export

    card = service.get_payments_card(vendor_id, date_from, date_to)
    exporter = CardPdfExporter(service._db)
    pdf = await run_export(exporter.export_payments, card, date_from, date_to)
    filename = f"Vendor_{vendor_id}_Payments_{date.today():%Y%m%d}.pdf"
    return _pdf_response(pdf, filename)


@router.get(
    "/{vendor_id}/cards/invoices/pdf",
    summary="Download the vendor 'Total Invoices' card as PDF",
    responses={
        200: {"description": "PDF file", "content": {"application/pdf": {}}},
        404: {"description": "Vendor not found"},
    },
)
async def download_vendor_invoices_card_pdf(
    vendor_id: UUID,
    service: VendorServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
):
    from app.common.card_pdf import CardPdfExporter
    from app.common.export_limiter import run_export

    card = service.get_invoices_card(vendor_id, date_from, date_to)
    exporter = CardPdfExporter(service._db)
    pdf = await run_export(exporter.export_invoices, card, date_from, date_to)
    filename = f"Vendor_{vendor_id}_Invoices_{date.today():%Y%m%d}.pdf"
    return _pdf_response(pdf, filename)
