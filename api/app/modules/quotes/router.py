"""Quote API endpoints with comprehensive documentation."""

import logging
from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from app.common.dependencies import (
    QuoteServiceDep,
    require_privileged,
    verify_internal_secret,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import DiscountType
from app.modules.quotes.schemas import (
    QuoteApproveRequest,
    QuoteCalculationResponse,
    QuoteConvertToInvoiceResponse,
    QuoteCreate,
    QuoteDuplicateResponse,
    QuoteFilterParams,
    QuoteLineItemCreate,
    QuoteMarkSentRequest,
    QuoteResponse,
    QuoteSendRequest,
    QuoteSendResponse,
    QuoteStatisticsResponse,
    QuoteStatusCounts,
    QuoteSummary,
    QuoteUpdate,
)
from app.modules.quotes.service import QuoteService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "",
    response_model=QuoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new quote",
    description="Create a new quote with line items and automatic calculations.",
    responses={
        201: {"description": "Quote created successfully"},
        400: {"description": "Invalid request data or customer inactive"},
        404: {"description": "Customer not found"},
        409: {"description": "Quote number conflict (retried automatically)"},
    },
)
def create_quote(body: QuoteCreate, service: QuoteServiceDep) -> QuoteResponse:
    """Create a new quote."""
    quote = service.create(body, user_id=service.actor_id)
    return QuoteResponse.model_validate(quote)


@router.get(
    "",
    response_model=PaginatedResponse[QuoteSummary],
    summary="List quotes",
    description="Get paginated list of quotes with filtering and search.",
    responses={
        200: {"description": "List of quotes"},
        400: {"description": "Invalid query parameters"},
    },
)
def list_quotes(
    service: QuoteServiceDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 10,
    status: Annotated[
        str | None,
        Query(description="Filter by status: draft, sent, approved, invoiced, expired"),
    ] = None,
    customer_id: Annotated[
        UUID | None,
        Query(description="Filter by customer ID", alias="customerId"),
    ] = None,
    date_from: Annotated[
        date | None,
        Query(
            description="Filter quotes from this date (transaction_date)",
            alias="dateFrom",
        ),
    ] = None,
    date_to: Annotated[
        date | None,
        Query(
            description="Filter quotes up to this date (transaction_date)",
            alias="dateTo",
        ),
    ] = None,
    due_date_from: Annotated[
        date | None,
        Query(description="Filter by due date range start", alias="dueDateFrom"),
    ] = None,
    due_date_to: Annotated[
        date | None,
        Query(description="Filter by due date range end", alias="dueDateTo"),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Search quote number, reference, or customer name"),
    ] = None,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Include total/total_pages (runs a COUNT(*); off by default)",
        ),
    ] = False,
) -> PaginatedResponse[QuoteSummary]:
    """List quotes with pagination and filtering."""
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    filters = QuoteFilterParams(
        status=status,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
        search=search,
    )
    return service.list_quotes(params, filters)


@router.get(
    "/counts",
    response_model=QuoteStatusCounts,
    summary="Get quote status counts",
    description="Get count of quotes grouped by status for dashboard displays.",
    responses={
        200: {"description": "Quote counts by status"},
    },
)
def get_quote_counts(service: QuoteServiceDep) -> QuoteStatusCounts:
    """Get quote counts grouped by status."""
    return service.get_status_counts()


@router.post(
    "/calculate",
    response_model=QuoteCalculationResponse,
    summary="Calculate quote totals",
    description="Calculate totals without saving (preview for frontend).",
    responses={
        200: {"description": "Calculated totals"},
        400: {"description": "Invalid line items or discount"},
    },
)
def calculate_quote_totals(
    line_items: list[QuoteLineItemCreate],
    discount_type: Annotated[str | None, Query()] = None,
    discount_amount: Annotated[float | None, Query()] = None,
    discount_percentage: Annotated[float | None, Query()] = None,
) -> QuoteCalculationResponse:
    """Calculate quote totals without saving (preview)."""
    dt = DiscountType(discount_type) if discount_type else None
    da = Decimal(str(discount_amount)) if discount_amount is not None else None
    dp = Decimal(str(discount_percentage)) if discount_percentage is not None else None

    return QuoteService.calculate_totals(line_items, dt, da, dp)


@router.get(
    "/export/excel",
    summary="Export quotes to Excel",
    description="Export filtered quotes to Excel spreadsheet.",
    responses={
        200: {
            "description": "Excel file",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
        },
    },
)
async def export_quotes_to_excel(
    service: QuoteServiceDep,
    status: Annotated[str | None, Query()] = None,
    customer_id: Annotated[UUID | None, Query(alias="customerId")] = None,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
    include_line_items: Annotated[
        bool,
        Query(
            alias="includeLineItems", description="Include line items in separate sheet"
        ),
    ] = False,
) -> StreamingResponse:
    """Export quotes to Excel."""
    import io

    from fastapi.responses import StreamingResponse

    from app.common.excel import ExcelExporter
    from app.common.export_limiter import run_export
    from app.lib.config import settings

    filters = QuoteFilterParams(
        status=status,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
    )

    # Batch-load full ORM rows instead of one get_by_id per row.
    # Fetch one row beyond the cap so truncation is detectable (ISSUE-014).
    rows = service.list_for_export(
        filters,
        include_line_items=include_line_items,
        limit=settings.BATCH_SIZE + 1,
    )
    truncated = len(rows) > settings.BATCH_SIZE
    quotes = rows[: settings.BATCH_SIZE]

    # Cap concurrency and build the workbook off the event loop.
    exporter = ExcelExporter()
    xlsx_bytes = await run_export(exporter.export_quotes, quotes, include_line_items)

    filename = f"Quotes_{date.today().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Truncated": "true" if truncated else "false",
            "X-Export-Limit": str(settings.BATCH_SIZE),
        },
    )


@router.get(
    "/stats/summary",
    summary="Get quote statistics",
    description="Get aggregated quote statistics for dashboard.",
    responses={
        200: {"description": "Quote statistics"},
    },
)
def get_quote_statistics(
    service: QuoteServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
) -> QuoteStatisticsResponse:
    """Get quote statistics for dashboard. Defaults to current month."""
    stats = service.get_quote_statistics(date_from, date_to)
    return QuoteStatisticsResponse(**stats)


@router.get(
    "/number/{quote_number}",
    response_model=QuoteResponse,
    summary="Get quote by number",
    description="Retrieve quote by quote number (e.g., QTE-20260315-001).",
    responses={
        200: {"description": "Quote details"},
        404: {"description": "Quote not found"},
    },
)
def get_quote_by_number(
    quote_number: str,
    service: QuoteServiceDep,
) -> QuoteResponse:
    """Get quote by quote number."""
    quote = service.get_by_number(quote_number)
    return QuoteResponse.model_validate(quote)


# DUPLICATE


@router.post(
    "/{quote_id}/duplicate",
    response_model=QuoteDuplicateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate quote",
    description="Create a copy of a quote as a new DRAFT.",
    responses={
        201: {"description": "Quote duplicated successfully"},
        404: {"description": "Quote not found"},
    },
)
def duplicate_quote(
    quote_id: UUID,
    service: QuoteServiceDep,
) -> QuoteDuplicateResponse:
    """Duplicate an existing quote as a new DRAFT."""
    duplicate = service.duplicate_quote(quote_id, service.actor_id)
    return QuoteDuplicateResponse(
        original_quote_id=quote_id,
        new_quote_id=duplicate.id,
        new_quote_number=duplicate.quote_number,
        message="Quote duplicated successfully",
    )


# GET BY ID


@router.get(
    "/{quote_id}",
    response_model=QuoteResponse,
    summary="Get quote details",
    description="Retrieve complete quote information with line items.",
    responses={
        200: {"description": "Quote details"},
        404: {"description": "Quote not found"},
    },
)
def get_quote(quote_id: UUID, service: QuoteServiceDep) -> QuoteResponse:
    """Get detailed quote information by ID."""
    quote = service.get_by_id(quote_id)
    return QuoteResponse.model_validate(quote)


# UPDATE


@router.put(
    "/{quote_id}",
    response_model=QuoteResponse,
    summary="Update quote",
    description="Update quote details (restrictions apply based on status).",
    responses={
        200: {"description": "Quote updated successfully"},
        400: {"description": "Quote not editable or validation failed"},
        404: {"description": "Quote not found"},
        409: {"description": "Version conflict (concurrent edit detected)"},
    },
)
def update_quote(
    quote_id: UUID,
    body: QuoteUpdate,
    service: QuoteServiceDep,
    expected_version: Annotated[
        int | None,
        Query(description="Expected version number for optimistic locking"),
    ] = None,
) -> QuoteResponse:
    """Update an existing quote."""
    quote = service.update(quote_id, body, expected_version)
    return QuoteResponse.model_validate(quote)


# STATUS CHANGES


@router.post(
    "/{quote_id}/mark-sent",
    response_model=QuoteResponse,
    summary="Mark quote as sent",
    description="Change quote status from DRAFT to SENT without sending email.",
    responses={
        200: {"description": "Quote marked as sent"},
        400: {"description": "Quote not in DRAFT status"},
        404: {"description": "Quote not found"},
    },
)
def mark_quote_as_sent(
    quote_id: UUID,
    service: QuoteServiceDep,
    body: QuoteMarkSentRequest | None = None,
) -> QuoteResponse:
    """Mark quote as sent (without sending email)."""
    sent_at = body.sent_at if body else None
    quote = service.mark_as_sent(quote_id, sent_at)
    return QuoteResponse.model_validate(quote)


@router.post(
    "/{quote_id}/send",
    response_model=QuoteSendResponse,
    summary="Send quote via email",
    description="Send quote to customer via email with PDF attachment.",
    responses={
        200: {"description": "Quote sent successfully"},
        400: {"description": "Quote already invoiced or customer has no email"},
        404: {"description": "Quote not found"},
        502: {"description": "Email delivery failed"},
    },
)
def send_quote(
    quote_id: UUID,
    service: QuoteServiceDep,
    body: QuoteSendRequest | None = None,
) -> QuoteSendResponse:
    """
    Send quote via email.
    """
    request_data = body or QuoteSendRequest()

    result = service.send_quote(
        quote_id,
        to_email=request_data.to_email,
        subject=request_data.subject,
        body=request_data.body,
        attach_pdf=request_data.attach_pdf,
    )

    return QuoteSendResponse(**result)


@router.post(
    "/{quote_id}/approve",
    response_model=QuoteResponse,
    summary="Approve quote",
    description="Approve a quote (transitions to APPROVED status).",
    responses={
        200: {"description": "Quote approved successfully"},
        400: {"description": "Quote not in SENT/DRAFT status or is expired"},
        403: {"description": "Insufficient role to approve quotes"},
        404: {"description": "Quote not found"},
    },
    dependencies=[Depends(require_privileged())],
)
def approve_quote(
    quote_id: UUID,
    service: QuoteServiceDep,
    body: QuoteApproveRequest | None = None,
) -> QuoteResponse:
    """Approve a quote."""
    approved_at = body.approved_at if body else None
    quote = service.approve_quote(quote_id, approved_at, service.actor_id)
    return QuoteResponse.model_validate(quote)


@router.post(
    "/{quote_id}/convert-to-invoice",
    response_model=QuoteConvertToInvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Convert quote to invoice",
    description="Convert an approved quote to a new invoice.",
    responses={
        201: {"description": "Quote converted to invoice successfully"},
        400: {
            "description": "Quote cannot be converted (not approved, expired, or already converted)"
        },
        403: {"description": "Insufficient role to convert quotes"},
        404: {"description": "Quote not found"},
    },
    dependencies=[Depends(require_privileged())],
)
def convert_quote_to_invoice(
    quote_id: UUID,
    service: QuoteServiceDep,
) -> QuoteConvertToInvoiceResponse:
    """Convert an approved quote to an invoice."""
    result = service.convert_to_invoice(quote_id, service.actor_id)
    return QuoteConvertToInvoiceResponse(**result)


@router.delete(
    "/{quote_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete quote",
    description="Delete a quote (only DRAFT quotes can be deleted).",
    responses={
        204: {"description": "Quote deleted successfully"},
        400: {"description": "Quote is not in DRAFT status"},
        404: {"description": "Quote not found"},
    },
)
def delete_quote(quote_id: UUID, service: QuoteServiceDep) -> None:
    """Delete a quote (only DRAFT quotes can be deleted)."""
    service.delete_quote(quote_id)


@router.get(
    "/{quote_id}/pdf",
    summary="Download quote as PDF",
    description="Generate and download quote PDF.",
    responses={
        200: {
            "description": "PDF file",
            "content": {"application/pdf": {}},
        },
        404: {"description": "Quote not found"},
    },
)
async def download_quote_pdf(quote_id: UUID, service: QuoteServiceDep):
    """Generate and download quote as PDF."""
    import io

    from fastapi.responses import StreamingResponse

    from app.common.export_limiter import run_export

    # Cap concurrent PDF builds and run the blocking render in a worker
    # thread. Loads the quote once (no second get_by_id for the filename).
    pdf_data, quote = await run_export(service.generate_pdf_for_download, quote_id)

    return StreamingResponse(
        io.BytesIO(pdf_data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="Quote_{quote.quote_reference}.pdf"'
        },
    )


# SCHEDULER  (internal — hidden from public OpenAPI docs)


@router.post(
    "/internal/transition-expired",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
    summary="Nightly expired transition (internal)",
    description=(
        "Bulk-transitions past-due DRAFT/SENT quotes to EXPIRED and stamps "
        "expired_at. Called by the nightly scheduler. Requires the "
        "X-Internal-Secret header; not a public client endpoint."
    ),
    dependencies=[Depends(verify_internal_secret)],
)
def trigger_quote_expired_transition(service: QuoteServiceDep) -> dict:
    updated = service.bulk_transition_expired()
    return {
        "transitioned": updated,
        "message": f"{updated} quote(s) transitioned to EXPIRED",
    }
