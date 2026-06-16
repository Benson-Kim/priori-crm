"""
Purchase Order API endpoints — Purchases module.

CRUD (create / get / get-by-number / update / delete), the totals-preview
endpoint deferred from PO-02, and the read-only PDF view/download
(PO-05). List, export, send, convert, cancel and documents land in other
issues.

"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

from app.common.dependencies import PurchaseOrderServiceDep, require_privileged
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCalculationResponse,
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
    PurchaseOrderResponse,
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
