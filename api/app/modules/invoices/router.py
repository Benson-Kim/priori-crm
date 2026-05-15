"""Invoice API endpoints with comprehensive documentation."""
import logging
from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse

from app.common.dependencies import InvoiceServiceDep
from app.common.pagination import PaginatedResponse, PaginationParams
from app.modules.invoices.schemas import (
    InvoiceCalculationResponse,
    InvoiceCreate,
    InvoiceDetailResponse,
    InvoiceDuplicateResponse,
    InvoiceFilterParams,
    InvoiceLineItemCreate,
    InvoiceMarkSentRequest,
    InvoiceResponse,
    InvoiceSendRequest,
    InvoiceSendResponse,
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
    # TODO: Add current user dependency when auth module exists
    # current_user: CurrentUser,
) -> InvoiceResponse:
    """
    Create a new invoice.
    
    **Required Fields:**
    - `customerId`: Must be an active customer
    - `lineItems`: At least one line item required
    - `dueDate`: Must be on or after transaction date
    
    **Automatic Calculations:**
    - Line totals: quantity × unit_price
    - Tax amounts: line_total × tax_rate
    - Subtotal: sum of all line totals
    - Discount: fixed amount OR percentage of subtotal
    - Total due: subtotal - discount + tax
    - Balance due: total_due (no payments yet)
    
    **Invoice Numbering:**
    - `invoice_number`: Auto-generated (e.g., INV-20260315-001)
    - `invoice_reference`: User-facing reference (e.g., IN-0101)
    
    **Initial Status:**
    - Always created as DRAFT
    - Use "Send" or "Mark as Sent" to change status
    
    **Example Request:**
    ```json
    {
      "customerId": "uuid",
      "transactionDate": "2026-03-30",
      "dueDate": "2026-04-29",
      "currency": "KES",
      "lineItems": [
        {
          "description": "Microsoft 365 Suite",
          "quantity": 120.55,
          "unitPrice": 120.55,
          "taxType": "vat_16"
        }
      ],
      "discountType": "percentage",
      "discountPercentage": 10.0,
      "notes": "Thank you for your business"
    }
    ```
    """
    # user_id = current_user.id if current_user else None
    user_id = None  # TODO: Replace with actual user ID
    
    invoice = service.create(body, user_id=user_id)
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
        Query(description="Filter by status: draft, sent, partial, paid, overdue, canceled"),
    ] = None,
    customer_id: Annotated[
        UUID | None,
        Query(description="Filter by customer ID", alias="customerId"),
    ] = None,
    date_from: Annotated[
        date | None,
        Query(description="Filter invoices from this date (transaction_date)", alias="dateFrom"),
    ] = None,
    date_to: Annotated[
        date | None,
        Query(description="Filter invoices up to this date (transaction_date)", alias="dateTo"),
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
) -> PaginatedResponse[InvoiceSummary]:
    """
    List invoices with pagination and filtering.
    
    **Performance Note:**
    Uses lightweight InvoiceSummary (no line items/payments) for fast loading.
    Returns ~200 bytes per invoice vs ~1KB for full InvoiceResponse.
    
    **Filter Options:**
    - **status**: Filter by lifecycle status
    - **customerId**: Show invoices for specific customer
    - **dateFrom/dateTo**: Filter by invoice date range
    - **dueDateFrom/dueDateTo**: Filter by due date range
    - **search**: Case-insensitive search in invoice number, reference, customer name
    
    **Sorting:**
    - Default: Most recent first (created_at DESC)
    
    **Response Fields:**
    - `customer_name`: Computed from customer's company_name or first_name + last_name
    - `is_overdue`: Computed property (due_date < today AND balance > 0)
    - `days_overdue`: Computed property (0 if not overdue)
    
    **Example:**
    ```
    GET /api/v1/invoices?status=overdue&page=1&per_page=10
    ```
    """
    params = PaginationParams(page=page, per_page=per_page)
    
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
def get_invoice_counts(service: InvoiceServiceDep) -> InvoiceStatusCounts:
    """
    Get invoice counts grouped by status.
    
    **Use Cases:**
    - Display filter tabs with counts (e.g., "Overdue (60)")
    - Dashboard metrics
    - Status overview widget
    
    **Special Handling:**
    - `overdue` count: Calculated from invoices past due_date with balance > 0
    - `all` count: Total invoices (sum of all statuses)
    
    **Example Response:**
    ```json
    {
      "all": 160,
      "draft": 20,
      "sent": 40,
      "partial": 10,
      "paid": 40,
      "overdue": 60,
      "canceled": 10
    }
    ```
    """
    return service.get_status_counts()


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
    
    **Includes:**
    - Complete invoice data (all fields)
    - All line items (ordered by line_number)
    - All payment records (ordered by payment_date DESC)
    - Customer relationship (eager loaded)
    
    **Response Size:**
    - Approximately 1-5KB depending on number of line items/payments
    - Use list endpoint with InvoiceSummary for better performance
    
    **Computed Properties:**
    - `is_editable`: True if status = DRAFT or SENT
    - `is_overdue`: True if past due_date with balance > 0
    - `days_overdue`: Number of days past due (0 if not overdue)
    
    **Use Cases:**
    - Invoice detail page
    - Edit invoice form (pre-populate)
    - PDF generation
    - Email preview
    """
    invoice = service.get_by_id(invoice_id)
    return InvoiceResponse.model_validate(invoice)


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
    
    Useful for public-facing invoice lookup or QR code scanning.
    
    **Example:**
    ```
    GET /api/v1/invoices/number/INV-20260315-001
    ```
    """
    invoice = service.get_by_number(invoice_number)
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
    
    **Editing Restrictions by Status:**
    
    | Status | Editable Fields |
    |--------|----------------|
    | DRAFT | All fields (full editing) |
    | SENT | Due date, notes, RFQ number only |
    | PAID | None (read-only) |
    | OVERDUE | None (read-only) |
    | CANCELED | None (read-only) |
    
    **DRAFT Status:**
    - Full editing allowed
    - Can change customer, dates, line items, amounts
    - Can add/remove line items
    - Can change discount settings
    
    **SENT Status:**
    - Customer LOCKED (cannot change)
    - Transaction date LOCKED (cannot change)
    - Amount LOCKED (cannot change line items)
    - Can extend due date
    - Can edit notes and RFQ number
    
    **Optimistic Locking:**
    - Include `expected_version` query parameter
    - Returns 409 Conflict if invoice was modified by another user
    - Frontend should refresh and retry
    
    **Automatic Recalculations:**
    - If line items change: All totals recalculated
    - If discount changes: Total due recalculated
    - Version number incremented on every update
    
    **Example Request:**
    ```json
    {
      "dueDate": "2026-05-15",
      "lineItems": [
        {
          "description": "Updated description",
          "quantity": 150,
          "unitPrice": 125.50,
          "taxType": "vat_16"
        }
      ],
      "notes": "Updated notes"
    }
    ```
    
    **Example with Optimistic Locking:**
    ```
    PUT /api/v1/invoices/{id}?expected_version=3
    ```
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
    
    **Status Transition:**
    - DRAFT → SENT
    
    **Use Cases:**
    - Invoice sent via external method (printed, fax, etc.)
    - Manual status update
    - Testing/debugging
    
    **Sets:**
    - `status` = SENT
    - `sent_at` = now (or provided timestamp)
    
    **Does NOT:**
    - Send email
    - Generate PDF
    - Notify customer
    
    **Example:**
    ```json
    POST /api/v1/invoices/{id}/mark-sent
    {
      "sentAt": "2026-03-30T10:00:00Z"
    }
    ```
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
    
    **Workflow:**
    1. Validate invoice (not CANCELED)
    2. Determine recipient (customer email or override)
    3. Generate email subject and body (template or custom)
    4. Generate PDF attachment (if attach_pdf = true)
    5. Send email via email service (AWS SES)
    6. Mark as SENT if currently DRAFT
    
    **Status Transition:**
    - DRAFT → SENT (automatically)
    - Other statuses remain unchanged
    
    **Email Template:**
    - Subject: "Invoice {reference} from {company_name}"
    - Body: Professional template with invoice details
    - Attachment: PDF of invoice (if attach_pdf = true)
    
    **Customization:**
    ```json
    {
      "toEmail": "override@example.com",
      "subject": "Custom subject",
      "body": "Custom email body",
      "attachPdf": true
    }
    ```
    
    **Default Behavior (no body):**
    - Sends to customer.email
    - Uses auto-generated subject
    - Uses professional template
    - Attaches PDF
    
    **Error Handling:**
    - Returns 400 if customer has no email
    - Returns 502 if email service fails
    - Retries failed sends automatically (up to 3 times)
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
        400: {"description": "Payment amount exceeds balance or invoice is DRAFT/CANCELED"},
        404: {"description": "Invoice not found"},
    },
)
def record_payment(
    invoice_id: UUID,
    body: PaymentCreate,
    service: InvoiceServiceDep,
    # TODO: current_user: CurrentUser,
) -> PaymentResponse:
    """
    Record a payment against an invoice.
    
    **Status Transitions:**
    - If balance becomes 0: Status → PAID
    - If partial payment: Status → PARTIAL
    - If overpaid: Returns 400 error
    
    **Automatic Updates:**
    - `invoice.amount_paid` += payment.amount
    - `invoice.balance_due` = total_due - amount_paid
    - `invoice.status` updated based on balance
    - `invoice.paid_at` set when fully paid
    
    **Validation:**
    - Amount must be > 0
    - Amount cannot exceed balance_due
    - Invoice must not be DRAFT (send it first)
    - Invoice must not be CANCELED
    
    **Payment Methods:**
    - cash
    - bank_transfer
    - check
    - card
    - mobile_money
    - other
    
    **Example:**
    ```json
    {
      "amount": 608.07,
      "paymentDate": "2026-03-30",
      "paymentMethod": "bank_transfer",
      "reference": "TXN-123456789",
      "notes": "Payment via M-Pesa"
    }
    ```
    
    **Partial Payment Example:**
    ```json
    {
      "amount": 300.00,
      "paymentDate": "2026-03-30",
      "paymentMethod": "cash",
      "notes": "First installment"
    }
    ```
    """
    # user_id = current_user.id if current_user else None
    user_id = None  # TODO: Replace with actual user ID
    
    payment = service.record_payment(invoice_id, body, user_id)
    return PaymentResponse.model_validate(payment)


@router.post(
    "/{invoice_id}/duplicate",
    response_model=InvoiceResponse,
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
    # TODO: current_user: CurrentUser,
) -> InvoiceResponse:
    """
    Duplicate an existing invoice.
    
    **Copied:**
    - Customer
    - Line items (descriptions, quantities, prices, taxes)
    - Discount settings
    - Currency
    - RFQ number
    - Notes
    
    **Reset:**
    - Invoice number (new auto-generated)
    - Invoice reference (new auto-generated)
    - Status → DRAFT
    - Transaction date → today
    - Due date → today + 30 days
    - Payments → none (empty)
    - Amount paid → 0
    - Balance due → total_due
    - sent_at → null
    - paid_at → null
    
    **Use Cases:**
    - Recurring invoices
    - Template-based invoicing
    - Quick re-invoicing for same customer
    - Monthly subscription billing
    
    **Workflow:**
    1. Duplicate original invoice
    2. Reset dates and status
    3. Clear payment history
    4. Return new DRAFT invoice
    5. User can edit before sending
    
    **Example Response:**
    ```json
    {
      "id": "new-uuid",
      "invoice_number": "INV-20260330-005",
      "invoice_reference": "IN-0156",
      "status": "draft",
      "transaction_date": "2026-03-30",
      "due_date": "2026-04-29",
      ...
    }
    ```
    """
    # user_id = current_user.id if current_user else None
    user_id = None  # TODO: Replace with actual user ID
    
    duplicate = service.duplicate_invoice(invoice_id, user_id)
    return InvoiceResponse.model_validate(duplicate)


@router.post(
    "/{invoice_id}/cancel",
    response_model=InvoiceResponse,
    summary="Cancel invoice",
    description="Cancel an invoice (terminal state - irreversible).",
    responses={
        200: {"description": "Invoice canceled successfully"},
        400: {"description": "Invoice already canceled"},
        404: {"description": "Invoice not found"},
    },
)
def cancel_invoice(
    invoice_id: UUID,
    service: InvoiceServiceDep,
) -> InvoiceResponse:
    """
    Cancel an invoice.
    
    **Status Transition:**
    - Any status → CANCELED
    
    **Behavior:**
    - CANCELED is a terminal state
    - Cannot be edited after cancellation
    - Cannot be sent after cancellation
    - Cannot record payments after cancellation
    - Cannot reverse cancellation
    
    **Use Cases:**
    - Voiding an invoice
    - Correcting an error (cancel + create new)
    - Customer request
    
    **Warning:**
    This action is irreversible. Consider:
    - Creating a credit note instead
    - Documenting reason for cancellation
    - Notifying customer
    
    **Frontend Recommendation:**
    - Show confirmation dialog
    - Require reason/notes
    - Warn about irreversibility
    """
    invoice = service.cancel_invoice(invoice_id)
    return InvoiceResponse.model_validate(invoice)


# CALCULATIONS & PREVIEWS

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
) -> InvoiceCalculationResponse:
    """
    Calculate invoice totals without saving.
    
    **Use Cases:**
    - Live calculation as user types in create/edit form
    - Preview totals before submission
    - Frontend validation
    - Total display in invoice builder
    
    **Calculation Steps:**
    1. For each line item:
       - Calculate line_total = quantity × unit_price
       - Calculate tax_amount = line_total × tax_rate
    2. Calculate subtotal = sum(line_total)
    3. Calculate discount_value:
       - If amount: discount_value = discount_amount
       - If percentage: discount_value = subtotal × (percentage / 100)
    4. Calculate tax_total = sum(tax_amount)
    5. Calculate total_due = subtotal - discount_value + tax_total
    
    **Performance:**
    - No database queries
    - Instant calculation
    - Stateless (no side effects)
    """
    from decimal import Decimal
    from app.constants.enums import DiscountType
    
    # Convert discount parameters
    dt = DiscountType(discount_type) if discount_type else None
    da = Decimal(str(discount_amount)) if discount_amount else None
    dp = Decimal(str(discount_percentage)) if discount_percentage else None
    
    return InvoiceService.calculate_totals(line_items, dt, da, dp)


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
        501: {"description": "PDF generation not yet implemented"},
    },
)
def download_invoice_pdf(
    invoice_id: UUID,
    service: InvoiceServiceDep,
) -> StreamingResponse:
    """
    Generate and download invoice as PDF.
    
    **PDF Contents:**
    - Company logo and details (sender)
    - Customer details (recipient)
    - Invoice metadata (number, dates, currency)
    - Line items table
    - Tax breakdown
    - Financial totals (subtotal, discount, tax, total)
    - Notes
    - Payment instructions
    
    **Filename:**
    - Format: `Invoice_{invoice_reference}.pdf`
    - Example: `Invoice_IN-0101.pdf`
    
    **Headers:**
    - Content-Type: application/pdf
    - Content-Disposition: attachment; filename="Invoice_IN-0101.pdf"
    
    **TODO:**
    - Implement PDF generation with WeasyPrint or ReportLab
    - Add company logo from settings
    - Support custom templates
    - Add QR code for online payment
    
    **Example:**
    ```
    GET /api/v1/invoices/{id}/pdf
    ```
    
    Downloads: `Invoice_IN-0101.pdf`
    """
    invoice = service.get_by_id(invoice_id)
    
    # TODO: Implement actual PDF generation
    # pdf_data = service.generate_pdf(invoice_id)
    
    # For now, return not implemented
    from fastapi import HTTPException
    raise HTTPException(
        status_code=501,
        detail="PDF generation not yet implemented"
    )
    
    # Future implementation:
    # return StreamingResponse(
    #     io.BytesIO(pdf_data),
    #     media_type="application/pdf",
    #     headers={
    #         "Content-Disposition": f'attachment; filename="Invoice_{invoice.invoice_reference}.pdf"'
    #     }
    # )


@router.get(
    "/export/excel",
    summary="Export invoices to Excel",
    description="Export filtered invoices to Excel spreadsheet.",
    responses={
        200: {
            "description": "Excel file",
            "content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}},
        },
        501: {"description": "Excel export not yet implemented"},
    },
)
def export_invoices_to_excel(
    service: InvoiceServiceDep,
    status: Annotated[str | None, Query()] = None,
    customer_id: Annotated[UUID | None, Query(alias="customerId")] = None,
    date_from: Annotated[date | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[date | None, Query(alias="dateTo")] = None,
    include_line_items: Annotated[
        bool,
        Query(alias="includeLineItems", description="Include line items in separate sheet")
    ] = False,
) -> StreamingResponse:
    """
    Export invoices to Excel.
    
    **Sheets:**
    1. Invoices: All invoice data
    2. Line Items (optional): All line items if include_line_items=true
    
    **Columns (Invoices sheet):**
    - Invoice Number
    - Invoice Reference
    - Customer Name
    - Transaction Date
    - Due Date
    - Status
    - Currency
    - Subtotal
    - Discount
    - Tax
    - Total Due
    - Amount Paid
    - Balance Due
    - Days Overdue (if applicable)
    
    **Filtering:**
    - Same filters as list endpoint
    - Exports ALL matching records (no pagination)
    
    **Filename:**
    - Format: `Invoices_Export_YYYYMMDD.xlsx`
    - Example: `Invoices_Export_20260330.xlsx`
    
    **TODO:**
    - Implement with openpyxl or xlsxwriter
    - Add formatting (headers, currency, dates)
    - Add formulas (sum totals)
    - Add filters and freeze panes
    
    **Example:**
    ```
    GET /api/v1/invoices/export/excel?status=overdue&includeLineItems=true
    ```
    """
    from fastapi import HTTPException
    raise HTTPException(
        status_code=501,
        detail="Excel export not yet implemented"
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
) -> dict:
    """
    Get invoice statistics for dashboard.
    
    **Metrics:**
    - Total invoices count
    - Total invoiced amount
    - Total paid amount
    - Total outstanding balance
    - Average invoice value
    - Average days to payment
    - Overdue count and amount
    
    **Date Range:**
    - Defaults to current month if not specified
    - Filters by transaction_date
    
    **Example Response:**
    ```json
    {
      "total_invoices": 160,
      "total_invoiced": 500000.00,
      "total_paid": 350000.00,
      "total_outstanding": 150000.00,
      "average_invoice_value": 3125.00,
      "average_days_to_payment": 25,
      "overdue_count": 60,
      "overdue_amount": 75000.00
    }
    ```
    
    **TODO:** Implement actual statistics calculation
    """
    from fastapi import HTTPException
    raise HTTPException(
        status_code=501,
        detail="Statistics endpoint not yet implemented"
    )