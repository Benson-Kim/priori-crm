"""
Expense API endpoints — Purchases module.
"""
import logging
import os
import secrets
import shutil
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, FileResponse

from app.common.dependencies import ExpenseServiceDep
from app.common.pagination import PaginatedResponse, PaginationParams
from app.lib.storage import storage_service
from app.modules.expenses.schemas import (
    ExpenseCalculationResponse,
    ExpenseCreate,
    ExpenseDocumentResponse,
    ExpenseFilterParams,
    ExpenseLineItemCreate,
    ExpensePaymentCreate,
    ExpensePaymentResponse,
    ExpenseResponse,
    ExpenseStatisticsResponse,
    ExpenseStatusCounts,
    ExpenseSummary,
    ExpenseUpdate,
    MarkAsPaidRequest,
)
from app.modules.expenses.service import ExpenseService

logger = logging.getLogger(__name__)

# Upload security constants
MAX_UPLOAD_SIZE_BYTES = 10_485_760  # 10 MB
ALLOWED_MIME_TYPES = frozenset({
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
})
ALLOWED_EXTENSIONS = frozenset({
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".doc", ".docx", ".xls", ".xlsx", ".txt", ".csv",
})

router = APIRouter()



# CREATE


@router.post(
    "",
    response_model=ExpenseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new expense",
    description=(
        "Create a new expense record linked to a vendor. "
        "Calculates subtotal, tax_total, and total_due from line items. "
        "Initial status is always PENDING. No discount in v1."
    ),
    responses={
        201: {"description": "Expense created successfully"},
        400: {"description": "Validation error or no line items supplied"},
        404: {"description": "Vendor not found"},
        409: {"description": "Reference number collision (auto-retried)"},
    },
)
def create_expense(
    body: ExpenseCreate,
    service: ExpenseServiceDep,
) -> ExpenseResponse:
    user_id = None   # TODO: replace with current_user.id when auth exists
    expense = service.create(body, user_id=user_id)
    return ExpenseResponse.model_validate(expense)



# LIST & AGGREGATES   (fixed paths before /{expense_id})


@router.get(
    "",
    response_model=PaginatedResponse[ExpenseSummary],
    summary="List expenses",
    description="Paginated, filterable list of expense records.",
    responses={
        200: {"description": "Paginated expense list"},
    },
)
def list_expenses(
    service: ExpenseServiceDep,
    page:     Annotated[int, Query(ge=1,       description="Page number (1-indexed)")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")]       = 10,
    filter_status: Annotated[
        str | None,
        Query(alias="status", description="pending | paid | overdue"),
    ] = None,
    vendor_id: Annotated[
        UUID | None,
        Query(alias="vendorId", description="Filter by vendor ID"),
    ] = None,
    date_from: Annotated[
        date | None,
        Query(alias="dateFrom", description="expense_date >= this value"),
    ] = None,
    date_to: Annotated[
        date | None,
        Query(alias="dateTo", description="expense_date <= this value"),
    ] = None,
    due_date_from: Annotated[
        date | None,
        Query(alias="dueDateFrom", description="due_date >= this value"),
    ] = None,
    due_date_to: Annotated[
        date | None,
        Query(alias="dueDateTo", description="due_date <= this value"),
    ] = None,
    is_recurring: Annotated[
        bool | None,
        Query(alias="isRecurring", description="Filter by recurring flag"),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Search expense number, reference, or vendor name"),
    ] = None,
) -> PaginatedResponse[ExpenseSummary]:
    params = PaginationParams(page=page, per_page=per_page)
    filters = ExpenseFilterParams(
        status=filter_status,
        vendor_id=vendor_id,
        date_from=date_from,
        date_to=date_to,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
        is_recurring=is_recurring,
        search=search,
    )
    return service.list_expenses(params, filters)


@router.get(
    "/counts",
    response_model=ExpenseStatusCounts,
    summary="Get expense status counts",
    description=(
        "Status counts for the filter-tab bar. "
        "Overdue count includes PENDING expenses past their due_date."
    ),
    responses={
        200: {"description": "Counts by status"},
    },
)
def get_expense_counts(service: ExpenseServiceDep) -> ExpenseStatusCounts:
    return service.get_status_counts()


@router.get(
    "/stats/summary",
    response_model=ExpenseStatisticsResponse,
    summary="Get expense statistics",
    description=(
        "Aggregated payables statistics for dashboard display. "
        "Defaults to the current calendar month."
    ),
    responses={
        200: {"description": "Expense statistics"},
    },
)
def get_expense_statistics(
    service: ExpenseServiceDep,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to:   Annotated[date | None, Query(alias="dateTo")]   = None,
) -> ExpenseStatisticsResponse:
    stats = service.get_statistics(date_from, date_to)
    return ExpenseStatisticsResponse(**stats)


@router.post(
    "/calculate",
    response_model=ExpenseCalculationResponse,
    summary="Preview expense totals",
    description=(
        "Calculate subtotal, tax_total, and total_due from line items "
        "without persisting. Used for real-time frontend recalculation. "
        "No discount applied in v1."
    ),
    responses={
        200: {"description": "Calculated totals"},
        400: {"description": "Invalid line items"},
    },
)
def calculate_expense_totals(
    line_items: list[ExpenseLineItemCreate],
) -> ExpenseCalculationResponse:
    return ExpenseService.calculate_totals(line_items)


@router.get(
    "/export/excel",
    summary="Export expenses to Excel",
    description=(
        "Export currently-filtered expenses as .xlsx. "
        "Not yet implemented."
    ),
    responses={
        200: {
            "description": "Excel file",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
        },
        501: {"description": "Not yet implemented"},
    },
)
def export_expenses_to_excel(
    service: ExpenseServiceDep,
    filter_status: Annotated[str | None, Query(alias="status")]       = None,
    vendor_id:     Annotated[UUID | None, Query(alias="vendorId")]    = None,
    date_from:     Annotated[date | None, Query(alias="dateFrom")]    = None,
    date_to:       Annotated[date | None, Query(alias="dateTo")]      = None,
    is_recurring:  Annotated[bool | None, Query(alias="isRecurring")] = None,
) -> StreamingResponse:
    from fastapi import HTTPException
    raise HTTPException(status_code=501, detail="Excel export not yet implemented")


@router.get(
    "/number/{expense_number}",
    response_model=ExpenseResponse,
    summary="Get expense by number",
    description="Retrieve an expense by system number (e.g. EXP-20260519-001).",
    responses={
        200: {"description": "Expense details"},
        404: {"description": "Expense not found"},
    },
)
def get_expense_by_number(
    expense_number: str,
    service: ExpenseServiceDep,
) -> ExpenseResponse:
    expense = service.get_by_number(expense_number)
    return ExpenseResponse.model_validate(expense)



# SINGLE RESOURCE  (/{expense_id} — must follow all fixed paths)


@router.get(
    "/{expense_id}",
    response_model=ExpenseResponse,
    summary="Get expense details",
    description=(
        "Retrieve complete expense record including line items, "
        "payments, and documents."
    ),
    responses={
        200: {"description": "Expense details"},
        404: {"description": "Expense not found"},
    },
)
def get_expense(
    expense_id: UUID,
    service: ExpenseServiceDep,
) -> ExpenseResponse:
    expense = service.get_by_id(expense_id)
    return ExpenseResponse.model_validate(expense)


@router.put(
    "/{expense_id}",
    response_model=ExpenseResponse,
    summary="Update expense",
    description=(
        "Update an expense record. "
        "Only PENDING and OVERDUE expenses are editable. "
        "PAID expenses are read-only. "
        "currency is always locked after first save. "
        "Supplying line_items replaces the full set."
    ),
    responses={
        200: {"description": "Expense updated"},
        400: {"description": "Not editable or validation failed"},
        404: {"description": "Expense or vendor not found"},
        409: {"description": "Version conflict — concurrent edit detected"},
    },
)
def update_expense(
    expense_id: UUID,
    body: ExpenseUpdate,
    service: ExpenseServiceDep,
    expected_version: Annotated[
        int | None,
        Query(
            alias="expectedVersion",
            description=(
                "Pass the version from your last GET response. "
                "If the expense has been modified since, a 409 is returned."
            ),
        ),
    ] = None,
) -> ExpenseResponse:
    expense = service.update(expense_id, body, expected_version)
    return ExpenseResponse.model_validate(expense)


@router.delete(
    "/{expense_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete expense",
    description=(
        "Permanently delete an expense and all associated line items, "
        "payments, and documents. Irreversible. "
        "Manager / Owner role required when the expense has recorded payments."
    ),
    responses={
        204: {"description": "Expense deleted"},
        404: {"description": "Expense not found"},
    },
)
def delete_expense(
    expense_id: UUID,
    service: ExpenseServiceDep,
) -> None:
    # had_payments returned for future role-gate enforcement
    service.delete(expense_id)



# ACTIONS


@router.post(
    "/{expense_id}/mark-paid",
    response_model=ExpenseResponse,
    summary="Mark expense as paid",
    description=(
        "Quick-action: set status to PAID without creating a payment record "
        "For a full audit trail use POST /{expense_id}/payments."
    ),
    responses={
        200: {"description": "Expense marked as paid"},
        400: {"description": "Expense already in PAID status"},
        404: {"description": "Expense not found"},
    },
)
def mark_expense_as_paid(
    expense_id: UUID,
    service: ExpenseServiceDep,
    body: MarkAsPaidRequest | None = None,
) -> ExpenseResponse:
    paid_at = body.paid_at if body else None
    expense = service.mark_as_paid(expense_id, paid_at)
    return ExpenseResponse.model_validate(expense)


@router.post(
    "/{expense_id}/payments",
    response_model=ExpensePaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a payment",
    description=(
        "Record an auditable payment against an expense. "
        "Cumulative payments equal to or exceeding total_due transition "
        "the expense to PAID. Partial payments leave status unchanged."
    ),
    responses={
        201: {"description": "Payment recorded"},
        400: {"description": "Amount exceeds balance or expense already paid"},
        404: {"description": "Expense not found"},
    },
)
def record_expense_payment(
    expense_id: UUID,
    body: ExpensePaymentCreate,
    service: ExpenseServiceDep,
) -> ExpensePaymentResponse:
    user_id = None   # TODO: replace with current_user.id
    payment = service.record_payment(expense_id, body, user_id=user_id)
    return ExpensePaymentResponse.model_validate(payment)


@router.post(
    "/{expense_id}/duplicate",
    response_model=ExpenseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate expense",
    description=(
        "Create a copy of an expense as a new PENDING record. "
        "Line items are copied; payments and documents are not."
    ),
    responses={
        201: {"description": "Expense duplicated"},
        404: {"description": "Original expense not found"},
        409: {"description": "Reference collision after max retries"},
    },
)
def duplicate_expense(
    expense_id: UUID,
    service: ExpenseServiceDep,
) -> ExpenseResponse:
    user_id = None   # TODO: replace with current_user.id
    duplicate = service.duplicate(expense_id, user_id=user_id)
    return ExpenseResponse.model_validate(duplicate)



# DOCUMENT MANAGEMENT


@router.get(
    "/{expense_id}/documents",
    response_model=list[ExpenseDocumentResponse],
    summary="List expense documents",
    description="Retrieve all documents attached to an expense.",
    responses={
        200: {"description": "Document list"},
        404: {"description": "Expense not found"},
    },
)
def list_expense_documents(
    expense_id: UUID,
    service: ExpenseServiceDep,
) -> list[ExpenseDocumentResponse]:
    expense = service.get_by_id(expense_id)
    return [
        ExpenseDocumentResponse.model_validate(doc)
        for doc in expense.documents
    ]


@router.post(
    "/{expense_id}/documents",
    response_model=ExpenseDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register an attached document",
    description=(
        "Register document metadata after the file has been written to object "
        "storage by the client. The client must complete the storage upload "
        "before calling this endpoint. storage_key is the object path returned "
        "by the storage service. source must be: form | view | payment_modal."
    ),
    responses={
        201: {"description": "Document registered"},
        404: {"description": "Expense not found"},
    },
)
def attach_expense_document(
    expense_id: UUID,
    service: ExpenseServiceDep,
    file: UploadFile = File(...),
    source: str = Form("form"),
) -> ExpenseDocumentResponse:
    from app.constants.enums import DocumentSource
    user_id = None   # TODO: replace with current_user.id
    doc_source = DocumentSource(source)

    raw_filename = file.filename or "unnamed_file"
    safe_basename = PurePosixPath(raw_filename).name   # strips ../
    if not safe_basename or safe_basename.startswith("."):
        safe_basename = "unnamed_file"

    _, ext = os.path.splitext(safe_basename)
    ext_lower = ext.lower()
    if ext_lower not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File type '{ext_lower}' is not allowed. "
                f"Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            ),
        )

    mime_type = file.content_type or "application/octet-stream"
    if mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"MIME type '{mime_type}' is not allowed. "
                "Upload a PDF, image, or office document."
            ),
        )

    file.file.seek(0, 2)  # seek to end
    file_size_bytes = file.file.tell()
    file.file.seek(0)     # reset

    if file_size_bytes > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File size ({file_size_bytes / 1_048_576:.1f} MB) exceeds "
                f"the {MAX_UPLOAD_SIZE_BYTES / 1_048_576:.0f} MB limit."
            ),
        )

    if file_size_bytes == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    unique_prefix = secrets.token_hex(8)
    filename = f"{unique_prefix}_{safe_basename}"
    directory = f"expenses/{expense_id}"

    storage_key = storage_service.upload_file(file.file, directory, filename)

    document = service.attach_document(
        expense_id=expense_id,
        filename=safe_basename,  # preserve original display name
        file_size_bytes=file_size_bytes,
        mime_type=mime_type,
        storage_key=storage_key,
        source=doc_source,
        user_id=user_id,
    )
    return ExpenseDocumentResponse.model_validate(document)


@router.get(
    "/{expense_id}/documents/{document_id}/download",
    summary="Get document download info",
    description=(
        "Returns document metadata. Pre-signed URL generation is a TODO "
        "pending storage service integration."
    ),
    responses={
        200: {"description": "Document metadata"},
        404: {"description": "Document not found on this expense"},
        501: {"description": "Pre-signed URL generation not yet implemented"},
    },
)
def get_expense_document_download(
    expense_id: UUID,
    document_id: UUID,
    service: ExpenseServiceDep,
):
    document = service.get_document(expense_id, document_id)
    if not storage_service.file_exists(document.storage_key):
        raise HTTPException(
            status_code=404,
            detail="File not found on server",
        )
    return FileResponse(
        path=document.storage_key,
        filename=document.filename,
        media_type=document.mime_type,
    )


@router.delete(
    "/{expense_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
    description=(
        "Remove a document from an expense. "
        "The storage object is also deleted after the DB record is removed."
    ),
    responses={
        204: {"description": "Document deleted"},
        404: {"description": "Document not found on this expense"},
    },
)
def delete_expense_document(
    expense_id: UUID,
    document_id: UUID,
    service: ExpenseServiceDep,
) -> None:
    storage_key = service.delete_document(expense_id, document_id)
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



# SCHEDULER  (internal — hidden from public OpenAPI docs)


@router.post(
    "/internal/transition-overdue",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
    summary="Nightly overdue transition (internal)",
    description=(
        "Bulk-transitions PENDING expenses past their due_date to OVERDUE. "
        "Called by the nightly scheduler. Not a public client endpoint."
    ),
)
def trigger_overdue_transition(service: ExpenseServiceDep) -> dict:
    updated = service.bulk_transition_overdue()
    return {
        "transitioned": updated,
        "message":      f"{updated} expense(s) transitioned to OVERDUE",
    }