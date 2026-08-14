"""
Customer API endpoints.
"""

import logging
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse

from app.common.analytics import emit_event
from app.common.dependencies import CurrentUser, CustomerServiceDep
from app.common.exceptions import (
    DatabaseException,
    ForbiddenException,
    NotFoundException,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.reporting_time import reporting_date
from app.common.routing import CommitOnSuccessRoute
from app.common.statement import default_statement_period
from app.constants.enums import BillingCurrency, UserRole
from app.modules.customers.schemas import (
    BillingProfileResponse,
    BillingProfileUpdate,
    CustomerCreate,
    CustomerDeleteCheckResponse,
    CustomerDeleteResponse,
    CustomerDetailResponse,
    CustomerResponse,
    CustomerStatusCounts,
    CustomerSummary,
    CustomerUpdate,
    FinancialSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(route_class=CommitOnSuccessRoute)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new customer",
    description="Create a new customer record with validation.",
    responses={
        201: {"description": "Customer created successfully"},
        400: {"description": "Invalid request data"},
        409: {"description": "Customer with email already exists"},
    },
)
def create_customer(
    body: CustomerCreate,
    service: CustomerServiceDep,
) -> CustomerResponse:
    """Create a new customer."""
    customer = service.create(body)
    return CustomerResponse.model_validate(customer)


@router.get(
    "",
    summary="List customers",
    description="Get a paginated list of customers with optional filtering and search.",
    responses={
        200: {"description": "List of customers"},
        400: {"description": "Invalid query parameters"},
    },
)
def list_customers(
    service: CustomerServiceDep,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    per_page: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 10,
    status: Annotated[
        str | None,
        Query(
            description="Filter by status: all, active, inactive, suspended, deleted"
        ),
    ] = None,
    search: Annotated[
        str | None,
        Query(description="Search in name, email, or company name"),
    ] = None,
    with_total: Annotated[
        bool,
        Query(
            alias="withTotal",
            description="Include total/total_pages (runs a COUNT(*); off by default)",
        ),
    ] = False,
    unsynced: Annotated[
        bool,
        Query(
            description=(
                "Only customers with at least one billing profile not yet "
                "pushed to accounting"
            ),
        ),
    ] = False,
) -> PaginatedResponse[CustomerSummary]:
    """List customers with pagination, status filter, and search."""
    params = PaginationParams(page=page, per_page=per_page, with_total=with_total)
    return service.list_customers(
        params, status=status, search=search, unsynced=unsynced
    )


@router.get(
    "/counts",
    summary="Get customer status counts",
    description="Get the count of customers grouped by status for dashboard displays.",
    responses={
        200: {"description": "Customer counts by status"},
    },
)
def get_customer_counts(service: CustomerServiceDep) -> CustomerStatusCounts:
    """Get customer counts by status for filter tabs."""
    return service.get_status_counts()


@router.get(
    "/{customer_id}",
    summary="Get customer details",
    description="Retrieve detailed information for a specific customer including invoices and statements.",
    responses={
        200: {"description": "Customer details"},
        404: {"description": "Customer not found"},
    },
)
def get_customer(
    customer_id: UUID,
    service: CustomerServiceDep,
) -> CustomerDetailResponse:
    """Get a single customer by ID with real invoices and statement."""
    customer = service.get_by_id(customer_id)
    financial = service.get_financial_summary(customer_id)

    # Fetch recent invoices (first page, 5 items)
    recent_invoices_page = service.get_invoices(
        customer_id,
        PaginationParams(page=1, per_page=5),
    )
    recent_invoices = [
        {
            "id": str(inv.id),
            "invoice_number": inv.invoice_number,
            "invoice_reference": inv.invoice_reference,
            "amount": float(inv.total_due),
            "balance": float(inv.balance_due),
            "status": inv.status,
            "date": inv.transaction_date.strftime("%d %b %Y"),
        }
        for inv in recent_invoices_page.items
    ]

    # Generate current-year statement
    today = reporting_date()
    try:
        statement = service.generate_statement(
            customer_id,
            period_start=date(today.year, 1, 1),
            period_end=today,
        )
    except (NotFoundException, DatabaseException):
        # Only tolerate expected, narrow failures here so the detail view
        # still renders without a statement. Unexpected errors must
        # propagate to the global exception handlers.
        logger.exception("Failed to generate statement for customer %s", customer_id)
        statement = None

    return CustomerDetailResponse(
        customer=CustomerResponse.model_validate(customer),
        financial_summary=financial,
        recent_invoices=recent_invoices,
        statement=statement,
    )


@router.put(
    "/{customer_id}",
    summary="Update customer",
    description="Update an existing customer's information.",
    responses={
        200: {"description": "Customer updated successfully"},
        404: {"description": "Customer not found"},
        409: {"description": "Email already in use by another customer"},
    },
)
def update_customer(
    customer_id: UUID,
    body: CustomerUpdate,
    service: CustomerServiceDep,
    expected_version: Annotated[
        int,
        Query(
            alias="expectedVersion",
            description=(
                "Pass the version from your last GET response. "
                "If the customer has been modified since, a 409 is returned."
            ),
        ),
    ],
) -> CustomerResponse:
    """Update an existing customer with optimistic locking."""
    customer = service.update(customer_id, body, expected_version)
    return CustomerResponse.model_validate(customer)


@router.patch(
    "/{customer_id}/profiles/{currency}",
    summary="Edit a billing profile",
    description=(
        "Update payment terms, tax treatment or credit limit of one "
        "per-currency billing profile. Any accepted change marks the "
        "profile out of sync until it is pushed to accounting again."
    ),
    responses={
        200: {"description": "Billing profile updated (now unsynced)"},
        404: {"description": "Customer or billing profile not found"},
        409: {"description": "Profile modified by another user (stale version)"},
    },
)
def update_billing_profile(
    customer_id: UUID,
    currency: BillingCurrency,
    body: BillingProfileUpdate,
    service: CustomerServiceDep,
    expected_version: Annotated[
        int,
        Query(
            alias="expectedVersion",
            description=(
                "Pass the profile version from your last GET response. "
                "If the profile has been modified since, a 409 is returned."
            ),
        ),
    ],
) -> BillingProfileResponse:
    """Edit a billing profile with optimistic locking; resets its sync flag."""
    profile = service.update_profile(
        customer_id, currency.value, body, expected_version
    )
    return BillingProfileResponse.model_validate(profile)


@router.post(
    "/{customer_id}/profiles/{currency}/sync",
    status_code=status.HTTP_200_OK,
    summary="Mark a billing profile as posted to accounting",
    description=(
        "Flip one per-currency billing profile to synced and stamp "
        "synced_at. Idempotent: re-syncing an already-synced profile "
        "refreshes the timestamp."
    ),
    responses={
        200: {"description": "Billing profile marked synced"},
        404: {"description": "Customer or billing profile not found"},
    },
)
def sync_billing_profile(
    customer_id: UUID,
    currency: BillingCurrency,
    service: CustomerServiceDep,
) -> BillingProfileResponse:
    """Mark one billing profile as pushed to accounting."""
    profile = service.sync_profile(customer_id, currency.value)
    return BillingProfileResponse.model_validate(profile)


@router.post(
    "/{customer_id}/profiles/sync-all",
    status_code=status.HTTP_200_OK,
    summary="Mark all billing profiles as posted to accounting",
    description="Flip every billing profile of the customer to synced.",
    responses={
        200: {"description": "All billing profiles marked synced"},
        404: {"description": "Customer or billing profiles not found"},
    },
)
def sync_all_billing_profiles(
    customer_id: UUID,
    service: CustomerServiceDep,
) -> list[BillingProfileResponse]:
    """Mark every billing profile of a customer as pushed to accounting."""
    profiles = service.sync_all_profiles(customer_id)
    return [BillingProfileResponse.model_validate(p) for p in profiles]


@router.post(
    "/{customer_id}/activate",
    status_code=status.HTTP_200_OK,
    summary="Activate customer account",
    description="Change customer status from inactive/suspended to active.",
    responses={
        200: {"description": "Customer activated successfully"},
        400: {"description": "Customer cannot be activated (e.g., already deleted)"},
        404: {"description": "Customer not found"},
    },
)
def activate_customer(
    customer_id: UUID,
    service: CustomerServiceDep,
) -> CustomerResponse:
    """Activate a customer account."""
    customer = service.activate(customer_id)
    return CustomerResponse.model_validate(customer)


@router.post(
    "/{customer_id}/deactivate",
    status_code=status.HTTP_200_OK,
    summary="Deactivate customer account",
    description="Change customer status from active to inactive.",
    responses={
        200: {"description": "Customer deactivated successfully"},
        400: {
            "description": "Cannot deactivate: customer has outstanding balance or open transactions"
        },
        404: {"description": "Customer not found"},
    },
)
def deactivate_customer(
    customer_id: UUID,
    service: CustomerServiceDep,
    force: Annotated[
        bool,
        Query(description="Skip validation checks for outstanding balance/quotes"),
    ] = False,
) -> CustomerResponse:
    """Deactivate a customer account."""
    customer = service.deactivate(customer_id, force=force)
    return CustomerResponse.model_validate(customer)


@router.get(
    "/{customer_id}/delete-check",
    summary="Pre-delete validation check",
    description="Check what will happen if this customer is deleted.",
    responses={
        200: {"description": "Delete eligibility information retrieved"},
        404: {"description": "Customer not found"},
    },
)
def check_customer_delete_eligibility(
    customer_id: UUID,
    service: CustomerServiceDep,
) -> CustomerDeleteCheckResponse:
    """Check if customer can be deleted and preview consequences."""
    result = service.check_delete_eligibility(customer_id)

    return CustomerDeleteCheckResponse(
        can_delete=result["can_delete"],
        can_hard_delete=result["can_hard_delete"],
        delete_type=result["delete_type"],
        warnings=result["warnings"],
        associated_records=result["associated_records"],
        message=result["message"],
    )


@router.delete(
    "/{customer_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete customer",
    description="Soft delete (default) or permanently remove customer record.",
    responses={
        200: {"description": "Customer deleted successfully"},
        400: {"description": "Cannot delete: customer has associated records"},
        404: {"description": "Customer not found"},
    },
)
def delete_customer(
    customer_id: UUID,
    service: CustomerServiceDep,
    current_user: CurrentUser,
    hard_delete: Annotated[
        bool,
        Query(description="Permanently delete record (default: False = soft delete)"),
    ] = False,
    force: Annotated[
        bool,
        Query(description="Skip safety checks (DANGEROUS - use only for test data)"),
    ] = False,
) -> CustomerDeleteResponse:
    """Delete a customer (soft delete by default)."""
    # Destructive hard-delete is restricted to privileged roles.
    if hard_delete and not UserRole(current_user.role).is_privileged:
        raise ForbiddenException(
            detail="You do not have permission to permanently delete customers.",
            required_permission="admin/manager",
        )
    result = service.delete(customer_id, hard_delete=hard_delete, force=force)

    return CustomerDeleteResponse(
        customer_id=result["customer_id"],
        delete_type=result["delete_type"],
        deleted_at=result["deleted_at"],
        warnings=result.get("warnings", []),
        associated_records=result.get("associated_records", {}),
    )


@router.get(
    "/{customer_id}/financial-summary",
    summary="Get customer financial summary",
    description="Get aggregated financial metrics for customer overview.",
)
def get_customer_financial_summary(
    customer_id: UUID,
    service: CustomerServiceDep,
) -> FinancialSummary:
    """Get financial summary for customer overview."""
    return service.get_financial_summary(customer_id)


@router.get(
    "/{customer_id}/invoices",
    summary="Get customer invoices",
    description="Get paginated list of invoices for a specific customer.",
)
def get_customer_invoices(
    customer_id: UUID,
    service: CustomerServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 10,
    status: Annotated[str | None, Query()] = None,
) -> PaginatedResponse:
    """Get customer's invoice history with pagination and filtering."""
    params = PaginationParams(page=page, per_page=per_page)
    return service.get_invoices(customer_id, params, status_filter=status)


@router.get(
    "/{customer_id}/statement",
    summary="Generate customer statement",
    description="Generate statement of accounts for a date range.",
)
def generate_customer_statement(
    customer_id: UUID,
    service: CustomerServiceDep,
    period_start: Annotated[date | None, Query(description="Period start date")] = None,
    period_end: Annotated[date | None, Query(description="Period end date")] = None,
):
    """Generate a statement of accounts."""
    # Default to the last 12 months (shared with the vendor statement).
    period_start, period_end = default_statement_period(period_start, period_end)

    return service.generate_statement(customer_id, period_start, period_end)


def _safe_filename_token(value: str) -> str:
    """Reduce a free-text value to a safe Content-Disposition filename token.

    Keeps alphanumerics, dash and underscore; collapses everything else to
    underscores so a customer name with spaces, slashes or quotes can never
    break the header or escape the filename.
    """
    import re

    token = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return token or "customer"


def _statement_display_name(statement) -> str:
    """Resolve the customer display name used in export filenames."""
    customer = statement.customer
    return (
        customer.company_name
        or f"{customer.first_name} {customer.last_name}".strip()
        or customer.email
    )


@router.get(
    "/{customer_id}/statement/pdf",
    summary="Download customer statement as PDF",
    description=(
        "Generate and stream the customer statement-of-accounts PDF for a "
        "date range (defaults to the last 12 months). Renders the same "
        "content as the Statements tab: owner branding header, account "
        "summary and the chronological transactions ledger with a running "
        "balance. Owner branding always comes from the live profile."
    ),
    responses={
        200: {"description": "PDF file", "content": {"application/pdf": {}}},
        404: {"description": "Customer not found"},
        500: {"description": "PDF could not be generated"},
    },
)
async def download_customer_statement_pdf(
    customer_id: UUID,
    service: CustomerServiceDep,
    period_start: Annotated[date | None, Query(description="Period start date")] = None,
    period_end: Annotated[date | None, Query(description="Period end date")] = None,
) -> StreamingResponse:
    import io

    from app.common.export_limiter import run_export

    # Default to the last 12 months (shared with the vendor statement).
    period_start, period_end = default_statement_period(period_start, period_end)

    # Cap concurrent PDF builds and run the statement build + blocking
    # render in a worker thread. The statement is returned too, so the
    # filename never needs a second query.
    pdf_data, statement = await run_export(
        service.generate_statement_pdf, customer_id, period_start, period_end
    )

    emit_event(
        "customer_statement_pdf_downloaded",
        {"customer_id": str(customer_id), "row_count": len(statement.transactions)},
    )

    display_name = _statement_display_name(statement)
    filename = (
        f"CustomerStatement_{_safe_filename_token(display_name)}_"
        f"{period_start.isoformat()}_to_{period_end.isoformat()}.pdf"
    )
    return StreamingResponse(
        io.BytesIO(pdf_data),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get(
    "/{customer_id}/statement/excel",
    summary="Export customer statement to Excel",
    description=(
        "Export the customer statement-of-accounts for a date range "
        "(defaults to the last 12 months) as an .xlsx workbook: period and "
        "account summary block plus one row per ledger transaction. The "
        "workbook is rendered off the event loop."
    ),
    responses={
        200: {
            "description": "Excel file",
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            },
        },
        404: {"description": "Customer not found"},
    },
)
async def export_customer_statement_excel(
    customer_id: UUID,
    service: CustomerServiceDep,
    period_start: Annotated[date | None, Query(description="Period start date")] = None,
    period_end: Annotated[date | None, Query(description="Period end date")] = None,
) -> StreamingResponse:
    import io

    from app.common.excel import ExcelExporter
    from app.common.export_limiter import run_export

    # Default to the last 12 months (shared with the vendor statement).
    period_start, period_end = default_statement_period(period_start, period_end)

    statement = service.generate_statement(customer_id, period_start, period_end)

    # Cap concurrency and build the workbook off the event loop.
    exporter = ExcelExporter()
    xlsx_bytes = await run_export(exporter.export_customer_statement, statement)

    emit_event(
        "customer_statement_excel_exported",
        {"customer_id": str(customer_id), "row_count": len(statement.transactions)},
    )

    display_name = _statement_display_name(statement)
    filename = (
        f"CustomerStatement_{_safe_filename_token(display_name)}_"
        f"{period_start.isoformat()}_to_{period_end.isoformat()}.xlsx"
    )
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
