"""
Customer API endpoints.
"""

import logging
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.common.dependencies import CurrentUser, CustomerServiceDep
from app.common.exceptions import (
    DatabaseException,
    ForbiddenException,
    NotFoundException,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.statement import default_statement_period
from app.constants.enums import UserRole
from app.modules.customers.schemas import (
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

router = APIRouter()


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
) -> PaginatedResponse[CustomerSummary]:
    """List customers with pagination, status filter, and search."""
    params = PaginationParams(page=page, per_page=per_page)
    return service.list_customers(params, status=status, search=search)


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
    today = date.today()
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
) -> CustomerResponse:
    """Update an existing customer."""
    customer = service.update(customer_id, body)
    return CustomerResponse.model_validate(customer)


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
