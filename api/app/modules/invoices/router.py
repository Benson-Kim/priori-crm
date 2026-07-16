"""Invoice API endpoints with comprehensive documentation."""

import logging
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from app.common.dependencies import (
    InvoiceServiceDep,
    require_privileged,
    verify_internal_secret,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.modules.invoices.schemas import (
    InvoiceCalculationResponse,
    InvoiceCreate,
    InvoiceDuplicateResponse,
    InvoiceFilterParams,
    InvoiceLineItemCreate,
    InvoiceMarkSentRequest,
    InvoiceResponse,
    InvoiceSendRequest,
    InvoiceSendResponse,
    InvoiceStatisticsResponse,
    InvoiceStatusCounts,
    InvoiceSummary,
    InvoiceUpdate,
    PaymentCreate,
    PaymentResponse,
)
from app.modules.invoices.service import InvoiceService

logger = logging.getLogger(__name__)

router = APIRouter()


# CREATE


@router.post(
    "",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new invoice",
    description="Create a new invoice with line items and automatic calculations.",
    responses={
        201: {"description": "Invoice created successfully"},
        400: {"description": "Invalid request data or customer inactive"},
        404: {"description": "Customer not found"},
        409: {"description": "Invoice number conflict (retried automatically)"},
    },
)
def create_invoice(
    body: InvoiceCreate,
    service: InvoiceServiceDep,
) -> InvoiceResponse:
    """
    Create a new invoice.
    """
    invoice = service.create(body, user_id=service.actor_id)
    return InvoiceResponse.model_validate(invoice)


# READ


@router.get(
    "",
    response_model=PaginatedResponse[InvoiceSummary],
    summary="List invoices",
    description="Get paginated list of invoices with filtering and search.",
    responses={
        200: {"description": "List of invoices"},
        400: {"description": "Invalid query parameters"},
    },
)
def list_invoices(
    service: InvoiceServiceDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 10,
    status: Annotated[
        str | None,
        Query(
            description="Filter by status: draft, sent, partial, paid, overdue, canceled"
        ),
    ] = None,
    customer_id: Annotated[
        UUID | None,
        Query(description="Filter by customer ID", alias="customerId"),
    ] = None,
    date_from: Annotated[
        date | None,
        Query(
            description="Filter invoices from this date (transaction_date)",
            alias="dateFrom",
        ),
    ] = None,
    date_to: Annotated[
        date | None,
        Query(
            description="Filter invoices up to this date (transaction_date)",
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
        Query(description="Search invoice number, reference, or customer name"),
    ] = None,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Include total/total_pages (runs a COUNT(*); off by default)",
        ),
    ] = False,
) -> PaginatedResponse[InvoiceSummary]:
    """
    List invoices with pagination and filtering.
    """
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)

    filters = InvoiceFilterParams(
        status=status,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
        search=search,
    )

    return service.list_invoices(params, filters)


@router.get(
    "/counts",
    response_model=InvoiceStatusCounts,
    summary="Get invoice status counts",
    description="Get count of invoices grouped by status for dashboard displays.",
    responses={
        200: {"description": "Invoice counts by status"},
    },
)
def get_invoice_counts(
    service: InvoiceServiceDep,
    customer_id: Annotated[
        UUID | None,
        Query(description="Filter by customer ID", alias="customerId"),
    ] = None,
) -> InvoiceStatusCounts:
    """
    Get invoice counts grouped by status.
    """
    return service.get_status_counts(customer_id)


# CALCULATIONS


@router.post(
    "/calculate",
    response_model=InvoiceCalculationResponse,
    summary="Calculate invoice totals",
    description="Calculate totals without saving (preview for frontend).",
    responses={
        200: {"description": "Calculated totals"},
        400: {"description": "Invalid line items or discount"},
    },
)
def calculate_invoice_totals(
    line_items: list[InvoiceLineItemCreate],
    discount_type: Annotated[str | None, Query()] = None,
    discount_amount: Annotated[float | None, Query()] = None,
    discount_percentage: Annotated[float | None, Query()] = None,
    vat_enabled: Annotated[
        bool,
        Query(
            alias="vatEnabled",
            description="Enable document-level VAT on the discounted subtotal",
        ),
    ] = False,
    vat_rate: Annotated[
        float | None,
        Query(
            alias="vatRate",
            ge=0,
            le=1,
            description=(
                "VAT rate as a fraction (e.g. 0.16). Required when vatEnabled."
            ),
        ),
    ] = None,
) -> InvoiceCalculationResponse:
    """
    Calculate invoice totals without saving.
    """
    from decimal import Decimal

    from app.common.exceptions import BadRequestException
    from app.constants.enums import DiscountType

    # Preview/persist parity: a rate is required when VAT is enabled.
    if vat_enabled and vat_rate is None:
        raise BadRequestException(
            detail="vat_rate is required when vat_enabled is true",
            field="vatRate",
        )

    # Convert discount parameters
    dt = DiscountType(discount_type) if discount_type else None
    da = Decimal(str(discount_amount)) if discount_amount else None
    dp = Decimal(str(discount_percentage)) if discount_percentage else None
    vr = Decimal(str(vat_rate)) if vat_rate is not None else None

    return InvoiceService.calculate_totals(
        line_items, dt, da, dp, vat_enabled=vat_enabled, vat_rate=vr
    )


@router.get(
    "/export/excel",
    summary="Export invoices to Excel",
    description="Export filtered invoices to Excel spreadsheet.",
    responses={
        200: {
            "description": "Excel file",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
        },
    },
)
async def export_invoices_to_excel(
    service: InvoiceServiceDep,
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
    """
    Export invoices to Excel.
    """
    import io

    from app.common.excel import ExcelExporter
    from app.common.export_limiter import run_export
    from app.lib.config import settings

    filters = InvoiceFilterParams(
        status=status,
        customer_id=customer_id,
        date_from=date_from,
        date_to=date_to,
    )

    # Batch-load full ORM rows (customer eager-joined; line items via
    # selectinload when requested) instead of one get_by_id per row.
    # Fetch one row beyond the cap so truncation is detectable.
    rows = service.list_for_export(
        filters,
        include_line_items=include_line_items,
        limit=settings.BATCH_SIZE + 1,
    )
    truncated = len(rows) > settings.BATCH_SIZE
    invoices = rows[: settings.BATCH_SIZE]

    # Cap concurrency and run the CPU-bound workbook build off the event
    # loop. Rows are already loaded, so the generator is pure CPU.
    exporter = ExcelExporter()
    xlsx_bytes = await run_export(
        exporter.export_invoices, invoices, include_line_items
    )

    filename = f"Invoices_{date.today().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Truncated": "true" if truncated else "false",
            "X-Export-Limit": str(settings.BATCH_SIZE),
        },
    )


# STATISTICS & ANALYTICS (Bonus)


@router.get(
    "/stats/summary",
    summary="Get invoice statistics",
    description="Get aggregated invoice statistics for dashboard.",
    responses={
        200: {"description": "Invoice statistics"},
    },
)
def get_invoice_statistics(
    service: InvoiceServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
) -> InvoiceStatisticsResponse:
    """
    Get invoice statistics for dashboard.
    """
    stats = service.get_invoice_statistics(date_from, date_to)
    return InvoiceStatisticsResponse(**stats)


@router.get(
    "/number/{invoice_number}",
    response_model=InvoiceResponse,
    summary="Get invoice by number",
    description="Retrieve invoice by invoice number (e.g., INV-20240707-001).",
    responses={
        200: {"description": "Invoice details"},
        404: {"description": "Invoice not found"},
    },
)
def get_invoice_by_number(
    invoice_number: str,
    service: InvoiceServiceDep,
) -> InvoiceResponse:
    """
    Get invoice by invoice number.
    """
    invoice = service.get_by_number(invoice_number)
    return InvoiceResponse.model_validate(invoice)


# DUPLICATE


@router.post(
    "/{invoice_id}/duplicate",
    response_model=InvoiceDuplicateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate invoice",
    description="Create a copy of an invoice as a new DRAFT.",
    responses={
        201: {"description": "Invoice duplicated successfully"},
        404: {"description": "Invoice not found"},
    },
)
def duplicate_invoice(
    invoice_id: UUID,
    service: InvoiceServiceDep,
) -> InvoiceDuplicateResponse:
    """
    Duplicate an existing invoice.
    """
    return service.duplicate_invoice(invoice_id, service.actor_id)


# GET BY ID


@router.get(
    "/{invoice_id}",
    response_model=InvoiceResponse,
    summary="Get invoice details",
    description="Retrieve complete invoice information with line items and payments.",
    responses={
        200: {"description": "Invoice details"},
        404: {"description": "Invoice not found"},
    },
)
def get_invoice(
    invoice_id: UUID,
    service: InvoiceServiceDep,
) -> InvoiceResponse:
    """
    Get detailed invoice information by ID.
    """
    invoice = service.get_by_id(invoice_id)
    return InvoiceResponse.model_validate(invoice)


# UPDATE


@router.put(
    "/{invoice_id}",
    response_model=InvoiceResponse,
    summary="Update invoice",
    description="Update invoice details (restrictions apply based on status).",
    responses={
        200: {"description": "Invoice updated successfully"},
        400: {"description": "Invoice not editable or validation failed"},
        404: {"description": "Invoice not found"},
        409: {"description": "Version conflict (concurrent edit detected)"},
    },
)
def update_invoice(
    invoice_id: UUID,
    body: InvoiceUpdate,
    service: InvoiceServiceDep,
    expected_version: Annotated[
        int | None,
        Query(description="Expected version number for optimistic locking"),
    ] = None,
) -> InvoiceResponse:
    """
    Update an existing invoice.
    """
    invoice = service.update(invoice_id, body, expected_version)
    return InvoiceResponse.model_validate(invoice)


# ACTIONS


@router.post(
    "/{invoice_id}/mark-sent",
    response_model=InvoiceResponse,
    summary="Mark invoice as sent",
    description="Change invoice status from DRAFT to SENT without sending email.",
    responses={
        200: {"description": "Invoice marked as sent"},
        400: {"description": "Invoice not in DRAFT status"},
        404: {"description": "Invoice not found"},
    },
)
def mark_invoice_as_sent(
    invoice_id: UUID,
    service: InvoiceServiceDep,
    body: InvoiceMarkSentRequest | None = None,
) -> InvoiceResponse:
    """
    Mark invoice as sent (without actually sending email).
    """
    sent_at = body.sent_at if body else None
    invoice = service.mark_as_sent(invoice_id, sent_at)
    return InvoiceResponse.model_validate(invoice)


@router.post(
    "/{invoice_id}/send",
    response_model=InvoiceSendResponse,
    summary="Send invoice via email",
    description="Send invoice to customer via email with PDF attachment.",
    responses={
        200: {"description": "Invoice sent successfully"},
        400: {"description": "Invoice canceled or customer has no email"},
        404: {"description": "Invoice not found"},
        502: {"description": "Email delivery failed"},
    },
)
def send_invoice(
    invoice_id: UUID,
    service: InvoiceServiceDep,
    body: InvoiceSendRequest | None = None,
) -> InvoiceSendResponse:
    """
    Send invoice via email.
    """
    request_data = body or InvoiceSendRequest()

    result = service.send_invoice(
        invoice_id,
        to_email=request_data.to_email,
        subject=request_data.subject,
        body=request_data.body,
        attach_pdf=request_data.attach_pdf,
    )

    return InvoiceSendResponse(**result)


@router.post(
    "/{invoice_id}/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record payment",
    description="Record a payment against an invoice and update balance.",
    responses={
        201: {"description": "Payment recorded successfully"},
        400: {
            "description": "Payment amount exceeds balance or invoice is DRAFT/CANCELED"
        },
        403: {"description": "Insufficient role to record payments"},
        404: {"description": "Invoice not found"},
    },
    dependencies=[Depends(require_privileged())],
)
def record_payment(
    invoice_id: UUID,
    body: PaymentCreate,
    service: InvoiceServiceDep,
) -> PaymentResponse:
    """
    Record a payment against an invoice.
    """
    payment = service.record_payment(invoice_id, body, service.actor_id)
    return PaymentResponse.model_validate(payment)


@router.post(
    "/{invoice_id}/cancel",
    response_model=InvoiceResponse,
    summary="Cancel invoice",
    description="Cancel an invoice (terminal state - irreversible).",
    responses={
        200: {"description": "Invoice canceled successfully"},
        400: {"description": "Invoice already canceled"},
        403: {"description": "Insufficient role to cancel invoices"},
        404: {"description": "Invoice not found"},
    },
    dependencies=[Depends(require_privileged())],
)
def cancel_invoice(
    invoice_id: UUID,
    service: InvoiceServiceDep,
) -> InvoiceResponse:
    """
    Cancel an invoice.
    """
    invoice = service.cancel_invoice(invoice_id)
    return InvoiceResponse.model_validate(invoice)


@router.delete(
    "/{invoice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete invoice",
    description="Delete an invoice (only DRAFT invoices can be deleted).",
    responses={
        204: {"description": "Invoice deleted successfully"},
        400: {"description": "Invoice is not in DRAFT status"},
        404: {"description": "Invoice not found"},
    },
    dependencies=[Depends(require_privileged())],
)
def delete_invoice(invoice_id: UUID, service: InvoiceServiceDep) -> None:
    """Delete an invoice (only DRAFT invoices can be deleted)."""
    service.delete_invoice(invoice_id)
    return None  # 204 No Content


# PDF & EXPORT


@router.get(
    "/{invoice_id}/pdf",
    summary="Download invoice as PDF",
    description="Generate and download invoice PDF.",
    responses={
        200: {
            "description": "PDF file",
            "content": {"application/pdf": {}},
        },
        404: {"description": "Invoice not found"},
    },
)
async def download_invoice_pdf(
    invoice_id: UUID,
    service: InvoiceServiceDep,
) -> StreamingResponse:
    """
    Generate and download invoice as PDF.
    """
    import io

    from app.common.export_limiter import run_export

    # Cap concurrent PDF builds and run the blocking render in a worker
    # thread. The request-scoped Session is only used by this one thread
    # while the handler awaits, so off-thread use is safe — the same
    # execution model as the previous sync `def` endpoint.
    pdf_data, invoice = await run_export(service.generate_pdf_for_download, invoice_id)

    return StreamingResponse(
        io.BytesIO(pdf_data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="Invoice_{invoice.invoice_reference}.pdf"'
        },
    )


# SCHEDULER  (internal — hidden from public OpenAPI docs)


@router.post(
    "/internal/transition-overdue",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
    summary="Nightly overdue transition (internal)",
    description=(
        "Bulk-transitions past-due, unpaid SENT/PARTIAL invoices to OVERDUE. "
        "Called by the nightly scheduler. Requires the X-Internal-Secret "
        "header; not a public client endpoint."
    ),
    dependencies=[Depends(verify_internal_secret)],
)
def trigger_invoice_overdue_transition(service: InvoiceServiceDep) -> dict:
    updated = service.bulk_transition_overdue()
    return {
        "transitioned": updated,
        "message": f"{updated} invoice(s) transitioned to OVERDUE",
    }
