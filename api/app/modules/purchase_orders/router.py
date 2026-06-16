"""
Purchase Order API endpoints — Purchases module.

CRUD (create / get / get-by-number / update / delete), the totals-preview
endpoint, and the list view (list / counts / Excel export).
Send, convert, cancel and documents land in later issues.

"""

import logging
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

from app.common.dependencies import PurchaseOrderServiceDep, require_privileged
from app.common.pagination import PaginatedResponse, PaginationParams
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCalculationResponse,
    PurchaseOrderCreate,
    PurchaseOrderDuplicateResponse,
    PurchaseOrderFilterParams,
    PurchaseOrderLineItemCreate,
    PurchaseOrderResponse,
    PurchaseOrderSendRequest,
    PurchaseOrderSendResponse,
    PurchaseOrderStatusCounts,
    PurchaseOrderSummary,
    PurchaseOrderUpdate,
)
from app.modules.purchase_orders.service import PurchaseOrderService

logger = logging.getLogger(__name__)

router = APIRouter()


# CREATE


@router.post(
    "",
    response_model=PurchaseOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new purchase order",
    description=(
        "Create a new purchase order raised against a vendor. Calculates "
        "subtotal, tax_total and total from the line items. Initial status "
        "is always DRAFT. No discount in v1."
    ),
    responses={
        201: {"description": "Purchase order created successfully"},
        400: {"description": "Validation error, inactive vendor, or no line items"},
        404: {"description": "Vendor not found"},
        409: {"description": "Reference collision (auto-retried)"},
    },
)
def create_purchase_order(
    body: PurchaseOrderCreate,
    service: PurchaseOrderServiceDep,
) -> PurchaseOrderResponse:
    purchase_order = service.create(body, user_id=service.actor_id)
    return PurchaseOrderResponse.model_validate(purchase_order)


# LIST & AGGREGATES  (fixed paths must precede /{po_id})


@router.get(
    "",
    response_model=PaginatedResponse[PurchaseOrderSummary],
    summary="List purchase orders",
    description="Paginated, filterable list of purchase orders.",
    responses={
        200: {"description": "Paginated purchase-order list"},
    },
)
def list_purchase_orders(
    service: PurchaseOrderServiceDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 10,
    filter_status: Annotated[
        str | None,
        Query(alias="status", description="draft | sent | billed | canceled"),
    ] = None,
    vendor_id: Annotated[
        UUID | None,
        Query(alias="vendorId", description="Filter by vendor ID"),
    ] = None,
    date_from: Annotated[
        date | None,
        Query(alias="dateFrom", description="order_date >= this value"),
    ] = None,
    date_to: Annotated[
        date | None,
        Query(alias="dateTo", description="order_date <= this value"),
    ] = None,
    delivery_date_from: Annotated[
        date | None,
        Query(alias="deliveryDateFrom", description="delivery_date >= this value"),
    ] = None,
    delivery_date_to: Annotated[
        date | None,
        Query(alias="deliveryDateTo", description="delivery_date <= this value"),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Search PO number, reference, or vendor name"),
    ] = None,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Include total/total_pages (runs a COUNT(*); off by default)",
        ),
    ] = False,
) -> PaginatedResponse[PurchaseOrderSummary]:
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    filters = PurchaseOrderFilterParams(
        status=filter_status,
        vendor_id=vendor_id,
        date_from=date_from,
        date_to=date_to,
        delivery_date_from=delivery_date_from,
        delivery_date_to=delivery_date_to,
        search=search,
    )
    return service.list_purchase_orders(params, filters)


@router.get(
    "/counts",
    response_model=PurchaseOrderStatusCounts,
    summary="Get purchase-order status counts",
    description="Per-status counts for the filter-tab bar badges.",
    responses={
        200: {"description": "Counts by status"},
    },
)
def get_purchase_order_counts(
    service: PurchaseOrderServiceDep,
) -> PurchaseOrderStatusCounts:
    return service.get_status_counts()


@router.get(
    "/export/excel",
    summary="Export purchase orders to Excel",
    description="Export the currently-filtered purchase orders as an .xlsx workbook.",
    responses={
        200: {
            "description": "Excel file",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
        },
    },
)
async def export_purchase_orders_to_excel(
    service: PurchaseOrderServiceDep,
    filter_status: Annotated[str | None, Query(alias="status")] = None,
    vendor_id: Annotated[UUID | None, Query(alias="vendorId")] = None,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
    delivery_date_from: Annotated[date | None, Query(alias="deliveryDateFrom")] = None,
    delivery_date_to: Annotated[date | None, Query(alias="deliveryDateTo")] = None,
    search: Annotated[str | None, Query()] = None,
    include_line_items: Annotated[
        bool,
        Query(
            alias="includeLineItems",
            description="Include line items in a separate sheet",
        ),
    ] = False,
) -> StreamingResponse:
    import io

    from app.common.excel import ExcelExporter
    from app.common.export_limiter import run_export
    from app.lib.config import settings

    filters = PurchaseOrderFilterParams(
        status=filter_status,
        vendor_id=vendor_id,
        date_from=date_from,
        date_to=date_to,
        delivery_date_from=delivery_date_from,
        delivery_date_to=delivery_date_to,
        search=search,
    )

    # Batch-load full ORM rows; fetch one beyond the cap so truncation is
    # detectable, then trim to the cap for the workbook.
    rows = service.list_for_export(
        filters,
        include_line_items=include_line_items,
        limit=settings.BATCH_SIZE + 1,
    )
    truncated = len(rows) > settings.BATCH_SIZE
    purchase_orders = rows[: settings.BATCH_SIZE]

    # Cap concurrency and build the workbook off the event loop.
    exporter = ExcelExporter()
    xlsx_bytes = await run_export(
        exporter.export_purchase_orders, purchase_orders, include_line_items
    )

    filename = f"PurchaseOrders_{date.today().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Truncated": "true" if truncated else "false",
            "X-Export-Limit": str(settings.BATCH_SIZE),
        },
    )


# FIXED PATHS  (must precede /{po_id})


@router.post(
    "/calculate",
    response_model=PurchaseOrderCalculationResponse,
    summary="Preview purchase-order totals",
    description=(
        "Calculate subtotal, tax_total and total from line items without "
        "persisting. Used for real-time frontend recalculation. No discount "
        "in v1."
    ),
    responses={
        200: {"description": "Calculated totals"},
        400: {"description": "Invalid line items"},
    },
)
def calculate_purchase_order_totals(
    line_items: list[PurchaseOrderLineItemCreate],
) -> PurchaseOrderCalculationResponse:
    return PurchaseOrderService.calculate_totals(line_items)


@router.post(
    "/{po_id}/send",
    response_model=PurchaseOrderSendResponse,
    summary="Send purchase order to the vendor by email",
    description=(
        "Send a DRAFT purchase order to its vendor by email and transition "
        "it to SENT. The status change and the queued email are committed "
        "atomically (transactional outbox); SES dispatch then runs outside "
        "the row lock, and a failed first attempt is retried automatically "
        "by the outbox drainer. The recipient defaults to the vendor email "
        "and can be overridden in the request."
    ),
    responses={
        200: {"description": "Purchase order sent (or durably queued)"},
        400: {
            "description": ("Not DRAFT, or the vendor has no email address on record")
        },
        404: {"description": "Purchase order not found"},
    },
)
def send_purchase_order(
    po_id: UUID,
    service: PurchaseOrderServiceDep,
    body: PurchaseOrderSendRequest | None = None,
) -> PurchaseOrderSendResponse:
    request_data = body or PurchaseOrderSendRequest()
    result = service.send_purchase_order(
        po_id,
        to_email=request_data.to_email,
        subject=request_data.subject,
        body=request_data.body,
        attach_pdf=request_data.attach_pdf,
    )
    return PurchaseOrderSendResponse(**result)


@router.post(
    "/{po_id}/duplicate",
    response_model=PurchaseOrderDuplicateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate purchase order",
    description=(
        "Create a copy of a purchase order as a new DRAFT. Copies line "
        "items, currency, notes, Terms & Conditions and the delivery date; "
        "order_date is reset to today. The Compliance Ref is cleared and "
        "attached documents are not copied. Available at any status."
    ),
    responses={
        201: {"description": "Purchase order duplicated successfully"},
        404: {"description": "Purchase order not found"},
    },
)
def duplicate_purchase_order(
    po_id: UUID,
    service: PurchaseOrderServiceDep,
) -> PurchaseOrderDuplicateResponse:
    duplicate = service.duplicate(po_id, service.actor_id)
    return PurchaseOrderDuplicateResponse(
        original_po_id=po_id,
        new_po_id=duplicate.id,
        new_po_reference=duplicate.po_reference,
        message="Purchase order duplicated successfully",
    )


@router.get(
    "/number/{po_number}",
    response_model=PurchaseOrderResponse,
    summary="Get purchase order by number",
    description="Retrieve a purchase order by system number (e.g. PO-20260616-001).",
    responses={
        200: {"description": "Purchase order details"},
        404: {"description": "Purchase order not found"},
    },
)
def get_purchase_order_by_number(
    po_number: str,
    service: PurchaseOrderServiceDep,
) -> PurchaseOrderResponse:
    purchase_order = service.get_by_number(po_number)
    return PurchaseOrderResponse.model_validate(purchase_order)


# SINGLE RESOURCE  (/{po_id} — must follow all fixed paths)


@router.get(
    "/{po_id}",
    response_model=PurchaseOrderResponse,
    summary="Get purchase order details",
    description=(
        "Retrieve a complete purchase order including its vendor and line items."
    ),
    responses={
        200: {"description": "Purchase order details"},
        404: {"description": "Purchase order not found"},
    },
)
def get_purchase_order(
    po_id: UUID,
    service: PurchaseOrderServiceDep,
) -> PurchaseOrderResponse:
    purchase_order = service.get_by_id(po_id)
    return PurchaseOrderResponse.model_validate(purchase_order)


@router.put(
    "/{po_id}",
    response_model=PurchaseOrderResponse,
    summary="Update purchase order",
    description=(
        "Update a purchase order. Only DRAFT purchase orders are editable; "
        "SENT / BILLED / CANCELED are read-only. currency is locked after the "
        "first save. Supplying lineItems replaces the full set. Pass "
        "expectedVersion (from your last GET) for optimistic-lock protection."
    ),
    responses={
        200: {"description": "Purchase order updated"},
        400: {"description": "Not editable (non-DRAFT) or validation failed"},
        404: {"description": "Purchase order or vendor not found"},
        409: {"description": "Version conflict — concurrent edit detected"},
    },
)
def update_purchase_order(
    po_id: UUID,
    body: PurchaseOrderUpdate,
    service: PurchaseOrderServiceDep,
    expected_version: Annotated[
        int | None,
        Query(
            alias="expectedVersion",
            description=(
                "Pass the version from your last GET response. If the purchase "
                "order has been modified since, a 409 is returned."
            ),
        ),
    ] = None,
) -> PurchaseOrderResponse:
    purchase_order = service.update(po_id, body, expected_version)
    return PurchaseOrderResponse.model_validate(purchase_order)


@router.post(
    "/{po_id}/cancel",
    response_model=PurchaseOrderResponse,
    summary="Cancel purchase order",
    description=(
        "Cancel a purchase order (DRAFT or SENT only) -> CANCELED. The "
        "record is preserved but becomes terminal: it can no longer be "
        "edited, sent or converted. A BILLED or already-CANCELED purchase "
        "order cannot be cancelled. A before/after-image audit row is "
        "written atomically with the transition."
    ),
    responses={
        200: {"description": "Purchase order canceled"},
        400: {"description": "Invalid transition (BILLED or already CANCELED)"},
        403: {"description": "Insufficient role to cancel purchase orders"},
        404: {"description": "Purchase order not found"},
    },
    dependencies=[Depends(require_privileged())],
)
def cancel_purchase_order(
    po_id: UUID,
    service: PurchaseOrderServiceDep,
) -> PurchaseOrderResponse:
    service.cancel(po_id)
    # Re-read with vendor + line items eager-loaded for the response.
    purchase_order = service.get_by_id(po_id)
    return PurchaseOrderResponse.model_validate(purchase_order)


@router.delete(
    "/{po_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete purchase order",
    description=(
        "Delete a purchase order. Permitted only for DRAFT or CANCELED "
        "purchase orders; a SENT or BILLED purchase order must be canceled "
        "first. Line items are removed with it. A before-image audit row is "
        "written atomically with the delete. The X-Delete-Type response "
        "header reports 'hard'."
    ),
    responses={
        204: {"description": "Purchase order deleted"},
        400: {"description": "Not deletable in the current status"},
        403: {"description": "Insufficient role to delete purchase orders"},
        404: {"description": "Purchase order not found"},
    },
    dependencies=[Depends(require_privileged())],
)
def delete_purchase_order(
    po_id: UUID,
    service: PurchaseOrderServiceDep,
    response: Response,
) -> None:
    soft_deleted = service.delete(po_id)
    response.headers["X-Delete-Type"] = "soft" if soft_deleted else "hard"


def _safe_filename_token(value: str) -> str:
    """Reduce a free-text value to a safe Content-Disposition filename token.

    Keeps alphanumerics, dash and underscore; collapses everything else to
    underscores so a vendor name with spaces, slashes or quotes can never
    break the header or escape the filename.
    """
    import re

    token = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return token or "vendor"


@router.get(
    "/{po_id}/pdf",
    summary="Download purchase order as PDF",
    description=(
        "Generate and stream the purchase-order PDF. The document is "
        "read-only: no edit controls, action buttons or attachments list. "
        "Available at any status. Owner branding comes from the PO's "
        "immutable snapshot once sent, else the live profile for a Draft "
        "preview."
    ),
    responses={
        200: {"description": "PDF file", "content": {"application/pdf": {}}},
        404: {"description": "Purchase order not found"},
        500: {"description": "PDF could not be generated"},
    },
)
async def download_purchase_order_pdf(
    po_id: UUID,
    service: PurchaseOrderServiceDep,
) -> StreamingResponse:
    import io

    from app.common.export_limiter import run_export

    # Cap concurrent PDF builds and run the blocking render in a worker
    # thread. Loads the PO once (no second query just for the filename).
    pdf_data, purchase_order = await run_export(
        service.generate_pdf_for_download, po_id
    )

    vendor_name = getattr(purchase_order.vendor, "vendor_name", "vendor")
    filename = (
        f"PurchaseOrder_{purchase_order.po_reference}_"
        f"{_safe_filename_token(vendor_name)}.pdf"
    )
    return StreamingResponse(
        io.BytesIO(pdf_data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
