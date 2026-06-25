"""
Purchase Order API endpoints — Purchases module.

CRUD (create / get / get-by-number / update / delete), the totals-preview
endpoint, and the list view (list / counts / Excel export), send, cancel, PDF,
and per-PO document attachments (upload / list / download / delete).

"""

import logging
import secrets
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

from app.common.analytics import PurchaseOrderEvent, emit_event
from app.common.dependencies import PurchaseOrderServiceDep, require_privileged
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.uploads import validate_upload
from app.lib.storage import storage_service
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCalculationResponse,
    PurchaseOrderCreate,
    PurchaseOrderDocumentResponse,
    PurchaseOrderDuplicateResponse,
    PurchaseOrderFilterParams,
    PurchaseOrderLineItemCreate,
    PurchaseOrderPaymentCreate,
    PurchaseOrderPaymentResponse,
    PurchaseOrderPaymentUpdate,
    PurchaseOrderResponse,
    PurchaseOrderSendRequest,
    PurchaseOrderSendResponse,
    PurchaseOrderStatusCounts,
    PurchaseOrderSummary,
    PurchaseOrderUpdate,
)

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
    # po_saved reflects the form-save outcome (the status the row landed in);
    # po_created is emitted by the service with the richer creation payload.
    emit_event(
        PurchaseOrderEvent.PO_SAVED,
        {"po_id": str(purchase_order.id), "status": str(purchase_order.status)},
    )
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

    # Fire-and-forget analytics: the active tab (status filter, "all" when
    # unset) and the number of rows actually exported (post-truncation).
    emit_event(
        PurchaseOrderEvent.PO_LIST_EXPORTED,
        {
            "active_filter_tab": filter_status or "all",
            "row_count": len(purchase_orders),
        },
    )

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


@router.get(
    "/{po_id}/payments/export/excel",
    summary="Export a purchase order's payments to Excel",
    description=(
        "Export all payments recorded against a single purchase order as an "
        ".xlsx workbook (one row per payment, ordered by payment date). The "
        "workbook is rendered off the event loop. Returns 404 if the purchase "
        "order does not exist."
    ),
    responses={
        200: {
            "description": "Excel file",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
        },
        404: {"description": "Purchase order not found"},
    },
)
async def export_purchase_order_payments_to_excel(
    po_id: UUID,
    service: PurchaseOrderServiceDep,
) -> StreamingResponse:
    import io

    from app.common.excel import ExcelExporter
    from app.common.export_limiter import run_export

    purchase_order, payments = service.list_payments_for_export(po_id)

    # Fire-and-forget analytics: only the PO id and the row count, no PII.
    emit_event(
        PurchaseOrderEvent.PO_PAYMENTS_EXPORTED,
        {"po_id": str(po_id), "row_count": len(payments)},
    )

    exporter = ExcelExporter()
    xlsx_bytes = await run_export(
        exporter.export_purchase_order_payments, purchase_order, payments
    )

    filename = (
        f"PurchaseOrder_{purchase_order.po_reference}_"
        f"Payments_{date.today().strftime('%Y%m%d')}.xlsx"
    )
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
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
    service: PurchaseOrderServiceDep,
) -> PurchaseOrderCalculationResponse:
    return service.calculate_totals(line_items)


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
        403: {"description": "Insufficient role to send purchase orders"},
        404: {"description": "Purchase order not found"},
    },
    dependencies=[Depends(require_privileged())],
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
    "/{po_id}/payments",
    response_model=PurchaseOrderPaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a payment against a purchase order",
    description=(
        "Record an auditable payment against a SENT purchase order, reducing "
        "its balance_due. When the balance reaches zero the purchase order is "
        "settled to PAID. Overpayment is rejected. A DRAFT purchase order must "
        "be sent first, and an already-PAID purchase order cannot take further "
        "payment. Optionally supply documentId to link a proof-of-payment "
        "document previously uploaded to this purchase order; the document "
        "must belong to the same PO or a 404 is returned."
    ),
    responses={
        201: {"description": "Payment recorded"},
        400: {"description": "Not SENT, already paid, or overpayment"},
        403: {"description": "Insufficient role to record payments"},
        404: {"description": "Purchase order or proof-of-payment document not found"},
    },
    dependencies=[Depends(require_privileged())],
)
def record_purchase_order_payment(
    po_id: UUID,
    body: PurchaseOrderPaymentCreate,
    service: PurchaseOrderServiceDep,
) -> PurchaseOrderPaymentResponse:
    payment = service.record_payment(po_id, body, user_id=service.actor_id)
    return PurchaseOrderPaymentResponse.model_validate(payment)


@router.put(
    "/{po_id}/payments/{payment_id}",
    response_model=PurchaseOrderPaymentResponse,
    summary="Edit a recorded payment",
    description=(
        "Edit a payment recorded against a purchase order. Changing the "
        "amount re-derives amount_paid / balance_due and may re-open a PAID "
        "purchase order to SENT or settle a SENT one to PAID. The new amount "
        "cannot overpay the purchase order. Returns 404 if the payment does "
        "not belong to the purchase order."
    ),
    responses={
        200: {"description": "Payment updated"},
        400: {"description": "Overpayment or validation error"},
        403: {"description": "Insufficient role to edit payments"},
        404: {"description": "Purchase order or payment not found"},
    },
    dependencies=[Depends(require_privileged())],
)
def update_purchase_order_payment(
    po_id: UUID,
    payment_id: UUID,
    body: PurchaseOrderPaymentUpdate,
    service: PurchaseOrderServiceDep,
) -> PurchaseOrderPaymentResponse:
    payment = service.update_payment(
        po_id, payment_id, body, user_id=service.actor_id
    )
    return PurchaseOrderPaymentResponse.model_validate(payment)


@router.delete(
    "/{po_id}/payments/{payment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a recorded payment",
    description=(
        "Delete a payment recorded against a purchase order. amount_paid / "
        "balance_due are re-derived and, if this payment had settled the "
        "purchase order, it reverts from PAID to SENT. Any proof-of-payment "
        "documents grouped under the payment are retained on the purchase "
        "order (their payment link is cleared). Returns 404 if the payment "
        "does not belong to the purchase order."
    ),
    responses={
        204: {"description": "Payment deleted"},
        403: {"description": "Insufficient role to delete payments"},
        404: {"description": "Purchase order or payment not found"},
    },
    dependencies=[Depends(require_privileged())],
)
def delete_purchase_order_payment(
    po_id: UUID,
    payment_id: UUID,
    service: PurchaseOrderServiceDep,
) -> None:
    service.delete_payment(po_id, payment_id, user_id=service.actor_id)


@router.post(
    "/{po_id}/mark-as-sent",
    response_model=PurchaseOrderResponse,
    summary="Mark a purchase order as sent (no email)",
    description=(
        "Transition a DRAFT purchase order to SENT WITHOUT dispatching an "
        "email — for when it has been sent to the vendor offline. Stamps "
        "sent_at and freezes the owner-header snapshot exactly like Send, but "
        "queues no email. Only DRAFT purchase orders can be marked sent."
    ),
    responses={
        200: {"description": "Purchase order marked as sent"},
        400: {"description": "Not DRAFT"},
        403: {"description": "Insufficient role to mark purchase orders sent"},
        404: {"description": "Purchase order not found"},
    },
    dependencies=[Depends(require_privileged())],
)
def mark_purchase_order_as_sent(
    po_id: UUID,
    service: PurchaseOrderServiceDep,
) -> PurchaseOrderResponse:
    service.mark_as_sent(po_id)
    # Re-read with vendor + line items eager-loaded for the response.
    purchase_order = service.get_by_id(po_id)
    return PurchaseOrderResponse.model_validate(purchase_order)


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
    emit_event(PurchaseOrderEvent.PO_VIEWED, {"po_id": str(po_id)})
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
    emit_event(
        PurchaseOrderEvent.PO_SAVED,
        {"po_id": str(po_id), "status": str(purchase_order.status)},
    )
    return PurchaseOrderResponse.model_validate(purchase_order)


# NOTE: Cancel is DISABLED in v1. The CANCELED status and
# PurchaseOrderService.cancel() are retained (dormant) but no route is
# exposed, so a PO can never be canceled through the API. Re-enabling is a
# code-only change: restore this endpoint and the SENT->CANCELED edge in
# PurchaseOrderService.ALLOWED_TRANSITIONS.


@router.delete(
    "/{po_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete purchase order",
    description=(
        "Delete a purchase order. Permitted only for DRAFT purchase orders; "
        "a SENT or PAID purchase order is a permanent record and cannot be "
        "deleted. Line items are removed with it. A before-image audit row "
        "is written atomically with the delete. The X-Delete-Type response "
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
    # This is the ORIGINAL document: full amount, no payments shown.
    pdf_data, purchase_order = await run_export(
        service.generate_pdf_for_download, po_id, False
    )

    emit_event(PurchaseOrderEvent.PO_PDF_DOWNLOADED, {"po_id": str(po_id)})

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


@router.get(
    "/{po_id}/pdf/statement",
    summary="Download the current (balance-aware) purchase-order PDF",
    description=(
        "Generate and stream the CURRENT purchase-order PDF: the same "
        "document as /pdf but with Amount Paid and Balance Due rows appended "
        "to the summary, reflecting payments recorded so far. Read-only and "
        "available at any status; owner branding comes from the PO's "
        "immutable snapshot once sent, else the live profile."
    ),
    responses={
        200: {"description": "PDF file", "content": {"application/pdf": {}}},
        404: {"description": "Purchase order not found"},
        500: {"description": "PDF could not be generated"},
    },
)
async def download_purchase_order_statement_pdf(
    po_id: UUID,
    service: PurchaseOrderServiceDep,
) -> StreamingResponse:
    import io

    from app.common.export_limiter import run_export

    # CURRENT document: payments applied + running balance.
    pdf_data, purchase_order = await run_export(
        service.generate_pdf_for_download, po_id, True
    )

    emit_event(PurchaseOrderEvent.PO_PDF_DOWNLOADED, {"po_id": str(po_id)})

    vendor_name = getattr(purchase_order.vendor, "vendor_name", "vendor")
    filename = (
        f"PurchaseOrder_{purchase_order.po_reference}_"
        f"{_safe_filename_token(vendor_name)}_current.pdf"
    )
    return StreamingResponse(
        io.BytesIO(pdf_data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# DOCUMENT MANAGEMENT  (per-PO attachments)


@router.get(
    "/{po_id}/documents",
    response_model=list[PurchaseOrderDocumentResponse],
    summary="List purchase-order documents",
    description="Retrieve all documents attached to a purchase order.",
    responses={
        200: {"description": "Document list"},
        404: {"description": "Purchase order not found"},
    },
)
def list_purchase_order_documents(
    po_id: UUID,
    service: PurchaseOrderServiceDep,
) -> list[PurchaseOrderDocumentResponse]:
    purchase_order = service.get_by_id(po_id)
    return [
        PurchaseOrderDocumentResponse.model_validate(doc)
        for doc in purchase_order.documents
    ]


@router.post(
    "/{po_id}/documents",
    response_model=PurchaseOrderDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document to a purchase order",
    description=(
        "Attach a supporting document to a purchase order. The file is "
        "validated (type allow-list, max size, magic-byte content sniff) and "
        "written to object storage, then its metadata is persisted. "
        "Attachments can be added from the View screen at any status "
        "(including SENT / BILLED / CANCELED) and do not re-open edit mode. "
        "source must be 'form', 'view' or 'payment_modal' (proof-of-payment "
        "uploaded in the Record Payment modal). storage_key is never returned."
    ),
    responses={
        201: {"description": "Document uploaded"},
        400: {"description": "Unsupported type, oversize, or invalid source"},
        404: {"description": "Purchase order not found"},
    },
)
def upload_purchase_order_document(
    po_id: UUID,
    service: PurchaseOrderServiceDep,
    file: UploadFile = File(...),
    source: str = Form("form"),
    payment_id: UUID | None = Form(None, alias="paymentId"),
) -> PurchaseOrderDocumentResponse:
    from app.common.exceptions import BadRequestException
    from app.constants.enums import DocumentSource

    user_id = service.actor_id

    # PO documents support form | view | payment_modal (proof-of-payment
    # uploaded in the Record Payment modal): reject any other value with a
    # clear 400 rather than letting the DB CHECK surface an opaque error.
    try:
        doc_source = DocumentSource(source)
    except ValueError as exc:
        raise BadRequestException(
            detail="source must be 'form', 'view' or 'payment_modal'.",
            field="source",
        ) from exc
    if doc_source not in (
        DocumentSource.FORM,
        DocumentSource.VIEW,
        DocumentSource.PAYMENT_MODAL,
    ):
        raise BadRequestException(
            detail="source must be 'form', 'view' or 'payment_modal'.",
            field="source",
        )

    # Confirm the PO exists before touching storage — raises NotFoundException
    # otherwise, so we never write an object for a non-existent PO.
    service.get_by_id(po_id)

    # When the upload is proof-of-payment for a specific payment, validate the
    # payment belongs to this PO before touching storage (scoped lookup raises
    # 404 on a foreign / unknown payment), so a document can never be grouped
    # under another PO's payment.
    if payment_id is not None:
        service.get_payment(po_id, payment_id)

    mime_type = file.content_type or "application/octet-stream"

    # Single source of truth for the upload attack surface:
    # sanitize name -> extension -> MIME -> size -> magic-byte content sniff.
    # Leaves the stream rewound to 0, ready to persist.
    safe_basename, _ext, file_size_bytes = validate_upload(
        file.file, file.filename or "unnamed_file", mime_type
    )

    unique_prefix = secrets.token_hex(8)
    filename = f"{unique_prefix}_{safe_basename}"
    directory = f"purchase_orders/{po_id}"

    storage_key = storage_service.upload_file(file.file, directory, filename)

    document = service.attach_document(
        po_id=po_id,
        filename=safe_basename,  # preserve the original display name
        file_size_bytes=file_size_bytes,
        mime_type=mime_type,
        storage_key=storage_key,
        source=doc_source,
        user_id=user_id,
        payment_id=payment_id,
    )
    return PurchaseOrderDocumentResponse.model_validate(document)


@router.get(
    "/{po_id}/documents/{document_id}/download",
    summary="Download a purchase-order document",
    description=(
        "Stream a document attached to a purchase order. Requires "
        "authentication; the document must belong to the given purchase "
        "order. The file is served through a path-confined storage handle — "
        "never a raw client-supplied path — so storage keys cannot be used "
        "for arbitrary file reads."
    ),
    responses={
        200: {"description": "Document file stream"},
        401: {"description": "Authentication required"},
        404: {"description": "Document not found on this purchase order"},
    },
)
def download_purchase_order_document(
    po_id: UUID,
    document_id: UUID,
    service: PurchaseOrderServiceDep,
) -> StreamingResponse:
    # Ownership: get_document is scoped to (po_id, document_id) and the
    # service depends on CurrentUser, so an unauthenticated caller is rejected
    # before this point and UUID enumeration cannot leak another PO's file.
    document = service.get_document(po_id, document_id)

    emit_event(
        PurchaseOrderEvent.PO_DOCUMENT_DOWNLOADED,
        {"po_id": str(po_id), "document_id": str(document_id)},
    )

    # download_stream serves through a path-confined handle (local) or
    # straight from get_object (S3); never a raw client-supplied path.
    file_stream = storage_service.download_stream(document.storage_key)

    return StreamingResponse(
        file_stream,
        media_type=document.mime_type,
        headers={"Content-Disposition": f'attachment; filename="{document.filename}"'},
    )


@router.delete(
    "/{po_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a purchase-order document",
    description=(
        "Remove a document from a purchase order. The storage object is also "
        "deleted after the DB record is removed."
    ),
    responses={
        204: {"description": "Document deleted"},
        403: {"description": "Insufficient role to delete documents"},
        404: {"description": "Document not found on this purchase order"},
    },
    dependencies=[Depends(require_privileged())],
)
def delete_purchase_order_document(
    po_id: UUID,
    document_id: UUID,
    service: PurchaseOrderServiceDep,
) -> None:
    storage_key = service.delete_document(po_id, document_id)
    success = storage_service.delete_file(storage_key)

    if success:
        logger.info(
            "Document file deleted from storage",
            extra={"storage_key": storage_key},
        )
    else:
        logger.warning(
            "Failed to delete document file from storage — orphaned file may remain",
            extra={"storage_key": storage_key},
        )
