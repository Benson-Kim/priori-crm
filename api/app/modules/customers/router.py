"""
Customer API endpoints.
"""

import logging
from datetime import date, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, status

from app.common.dependencies import CustomerServiceDep
from app.common.pagination import PaginatedResponse, PaginationParams
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
    MockInvoice,
    MockStatement,
)

logger = logging.getLogger(__name__)

router = APIRouter()



_MOCK_INVOICES = [
    MockInvoice(id=uuid4(), invoice_number="INV-20240709", amount=2300.00, balance=2300.00, status="Overdue (2 days)", date="07 Mar 2026"),
    MockInvoice(id=uuid4(), invoice_number="INV-20240709", amount=3750.00, balance=2300.00, status="Sent", date="07 Mar 2026"),
    MockInvoice(id=uuid4(), invoice_number="INV-20240709", amount=3750.00, balance=2300.00, status="Paid", date="07 Mar 2026"),
    MockInvoice(id=uuid4(), invoice_number="INV-20240709", amount=3750.00, balance=2300.00, status="Canceled", date="07 Mar 2026"),
    MockInvoice(id=uuid4(), invoice_number="INV-20240711", amount=1900.00, balance=2300.00, status="Overdue (2 days)", date="07 Mar 2026"),
    MockInvoice(id=uuid4(), invoice_number="INV-20240713", amount=3150.00, balance=2300.00, status="Sent", date="07 Mar 2026"),
    MockInvoice(id=uuid4(), invoice_number="INV-20240714", amount=2500.00, balance=2300.00, status="Overdue (2 days)", date="07 Mar 2026"),
    MockInvoice(id=uuid4(), invoice_number="INV-20240714", amount=2500.00, balance=2300.00, status="Sent", date="07 Mar 2026"),
]

_MOCK_STATEMENTS = [
    MockStatement(id=uuid4(), period="01-01-2024 To 31-12-2024", opening_balance=70704.00, invoiced_amount=0.00, amount_paid=70704.00, balance_due=0.00),
    MockStatement(id=uuid4(), period="01-01-2025 To 31-12-2025", opening_balance=140408.00, invoiced_amount=0.00, amount_paid=140408.00, balance_due=0.00),
    MockStatement(id=uuid4(), period="01-01-2026 To 14-05-2026", opening_balance=205004.00, invoiced_amount=0.00, amount_paid=205004.00, balance_due=0.00),
]



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
        Query(description="Filter by status: all, active, inactive, suspended, deleted"),
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
    """Get a single customer by ID, including mock invoices and statements."""
    customer = service.get_by_id(customer_id)
    financial = service.get_financial_summary(customer_id)

    return CustomerDetailResponse(
        customer=CustomerResponse.model_validate(customer),
        financial_summary=financial,
        invoices=_MOCK_INVOICES,
        statements=_MOCK_STATEMENTS,
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
        400: {"description": "Cannot deactivate: customer has outstanding balance or open transactions"},
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
    period_start: Annotated[date, Query(description="Period start date")] = None,
    period_end: Annotated[date, Query(description="Period end date")] = None,
):
    """Generate a statement of accounts."""
    # Default to last 12 months
    if period_end is None:
        period_end = date.today()
    if period_start is None:
        period_start = period_end - timedelta(days=365)

    return service.generate_statement(customer_id, period_start, period_end)