import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    DatabaseException,
    NotFoundException,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import CustomerStatus
from app.modules.customers.models import Customer
from app.modules.customers.schemas import (
    CustomerCreate,
    CustomerResponse,
    CustomerStatement,
    CustomerStatusCounts,
    CustomerUpdate,
    CustomerSummary,
    FinancialSummary,
    StatementSummary,
    StatementTransaction,
)

logger = logging.getLogger(__name__)

class CustomerService:
    """Handles customer CRUD business logic."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # Create

    def create(self, data: CustomerCreate) -> Customer:
        """Create a new customer."""
        try:
            existing = self._db.query(Customer).filter(Customer.email == data.email).first()
            if existing:
                raise ConflictException(
                    detail=f"Customer with email '{data.email}' already exists",
                    field="email",
                )

            customer = Customer(
                customer_type=data.customer_type,
                company_name=data.company_name,
                first_name=data.first_name,
                last_name=data.last_name,
                email=data.email,
                phone=data.phone,
                website=data.website,
                vat_number=data.vat_number,
                currency=data.currency,
                address=data.address,
                address2=data.address2,
                country=data.country,
                province=data.province,
                city=data.city,
                postal_code=data.postal_code,
            )

            self._db.add(customer)
            self._db.flush()

            logger.info(
                f"Created customer: {customer.id}",
                extra={
                    "customer_id": str(customer.id),
                    "customer_type": customer.customer_type,
                    "email": customer.email,
                },
            )

            return customer

        except ConflictException:
            raise
        except IntegrityError as e:
            logger.exception("Database integrity error creating customer")
            raise ConflictException("Customer data violates database constraints") from e
        except SQLAlchemyError as e:
            logger.exception("Database error creating customer")
            raise DatabaseException("Failed to create customer") from e

    # Read One

    def get_by_id(self, customer_id: uuid.UUID) -> Customer:
        """Get a customer by ID or raise 404."""
        try:
            customer = self._db.query(Customer).filter(Customer.id == customer_id).first()
            if customer is None:
                raise NotFoundException(
                    detail=f"Customer with ID '{customer_id}' not found",
                    resource="customer",
                )
            return customer
        
        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Database error retrieving customer {customer_id}")
            raise DatabaseException("Failed to retrieve customer") from e

    # Read Many

    def list_customers(
        self,
        params: PaginationParams,
        status: str | None = None,
        search: str | None = None,
    ) -> PaginatedResponse[CustomerResponse]:
        """List customers with pagination, optional status filter and search."""
        try:
            query = self._db.query(Customer)

            if status and status != "all":
                try:
                    query = query.filter(Customer.status == CustomerStatus(status))
                except ValueError:
                    raise BadRequestException(detail=f"Invalid status: {status}", field="status")
            
            if search:
                search_term = f"%{search}%"
                query = query.filter(
                    or_(
                        Customer.first_name.ilike(search_term),
                        Customer.last_name.ilike(search_term),
                        Customer.email.ilike(search_term),
                        Customer.company_name.ilike(search_term),
                    )
                )

            total = query.count()

            # Get status counts for tabs
            customers = (
                query.order_by(Customer.created_at.desc())
                .offset(params.offset)
                .limit(params.per_page)
                .all()
            )

            items = [
                CustomerSummary(
                    id=c.id,
                    customer_type=c.customer_type,
                    status=c.status,
                    display_name=c.display_name,
                    email=c.email,
                    phone=c.phone,
                    balance=c.balance,
                    currency=c.currency,
                    created_at=c.created_at,
                )
                for c in customers
            ]
            
            logger.debug(
                f"Listed {len(items)} customers (page {params.page}, total {total})",
                extra={
                    "page": params.page, "per_page": params.per_page, "total": total,
                    "status_filter": status, "search_term": search,
                },
            )
            
            return PaginatedResponse.create(items=items, total=total, params=params)
        
        except BadRequestException:
            raise
        except SQLAlchemyError as e:
            logger.exception("Database error listing customers")
            raise DatabaseException("Failed to list customers") from e

    def get_status_counts(self) -> CustomerStatusCounts:
        """Get counts of customers by status."""
        try:
            results = (
                self._db.query(Customer.status, func.count(Customer.id).label("count"),)
                .group_by(Customer.status)
                .all()
            )
            counts_dict  = {row_status: count for row_status, count in results}
            total = sum(counts_dict.values())
            
            counts = CustomerStatusCounts(
                all=total,
                active=counts_dict.get(CustomerStatus.ACTIVE, 0),
                inactive=counts_dict.get(CustomerStatus.INACTIVE, 0),
                suspended=counts_dict.get(CustomerStatus.SUSPENDED, 0),
                deleted=counts_dict.get(CustomerStatus.DELETED, 0),
            )

            logger.debug(f"Customer status counts: {counts}")

            return counts

        except SQLAlchemyError as e:
            logger.exception("Database error getting status counts")
            raise DatabaseException("Failed to get status counts") from e

    # Update


    def update(self, customer_id: uuid.UUID, data: CustomerUpdate) -> Customer:
        """Update an existing customer."""
        try:
            customer = self.get_by_id(customer_id)

            update_data = data.model_dump(exclude_unset=True)
            
            if not update_data:
                return customer

            # Check for email conflict if email is being updated
            if "email" in update_data and update_data["email"] != customer.email:
                existing = (
                    self._db.query(Customer)
                    .filter(
                        Customer.email == update_data["email"], 
                        Customer.id != customer_id,
                    )
                    .first()
                )

                if existing:
                    raise ConflictException(
                        detail=f"Email '{update_data['email']}' is already in use",
                        field="email",
                    )

            for field, value in update_data.items():
                setattr(customer, field, value)

            self._db.flush()
            
            logger.info(
                f"Updated customer: {customer.id}",
                extra={
                    "customer_id": str(customer.id), "updated_fields": list(update_data.keys()),
                },
            )

            return customer

        except (NotFoundException, ConflictException):
            raise
        except IntegrityError as e:
            logger.exception(f"Integrity error updating customer {customer_id}")
            raise ConflictException("Update violates database constraints") from e
        except SQLAlchemyError as e:
            logger.exception(f"Database error updating customer {customer_id}")
            raise DatabaseException("Failed to update customer") from e

    # Activate

    def activate(self, customer_id: uuid.UUID) -> Customer:
        """Activate a customer account."""
        try:
            customer = self.get_by_id(customer_id)
            
            # Check if already active (idempotent operation)
            if customer.status == CustomerStatus.ACTIVE:
                logger.info(
                    f"Customer {customer_id} already active",
                    extra={"customer_id": str(customer_id)}
                )
                return customer
            
            # Prevent activation of deleted customers
            if customer.status == CustomerStatus.DELETED:
                raise BadRequestException(
                    detail="Cannot activate a deleted customer. Please restore it first.",
                    field="status"
                )
            
            # Store previous status for logging
            previous_status = customer.status
            
            # Update status to active
            customer.status = CustomerStatus.ACTIVE
            self._db.flush()
            
            logger.info(
                f"Activated customer: {customer.id}",
                extra={
                    "customer_id": str(customer.id),
                    "previous_status": previous_status,
                }
            )
            
            return customer
            
        except (NotFoundException, BadRequestException):
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Database error activating customer {customer_id}")
            raise DatabaseException("Failed to activate customer") from e

    
    # Deactivate
    
    def deactivate(self, customer_id: uuid.UUID, force: bool = False) -> Customer:
        """Deactivate a customer account."""
        try:
            customer = self.get_by_id(customer_id)
            
            # Check if already inactive (idempotent operation)
            if customer.status == CustomerStatus.INACTIVE:
                logger.info(
                    f"Customer {customer_id} already inactive",
                    extra={"customer_id": str(customer_id)}
                )
                return customer
            
            # Prevent deactivation of deleted customers
            if customer.status == CustomerStatus.DELETED:
                raise BadRequestException(
                    detail="Cannot deactivate a deleted customer",
                    field="status"
                )
            
            # Validate business rules (unless forced)
            if not force:
                # Check for outstanding balance
                if customer.balance > 0:
                    raise BadRequestException(
                        detail=(
                            f"Customer has outstanding balance of {customer.currency} {customer.balance}. "
                            "Please settle all invoices before deactivating, or use force=true to override."
                        ),
                        field="balance"
                    )
                
                # TODO: Check for open quotes when Quote module exists
                # open_quotes = self._db.query(Quote).filter(
                #     Quote.customer_id == customer_id,
                #     Quote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT])
                # ).count()
                # if open_quotes > 0:
                #     raise BadRequestException(
                #         detail=(
                #             f"Customer has {open_quotes} open quote(s). "
                #             "Please finalize or cancel quotes before deactivating."
                #         ),
                #         field="quotes"
                #     )
            
            # Store previous status for logging
            previous_status = customer.status
            
            # Update status to inactive
            customer.status = CustomerStatus.INACTIVE
            self._db.flush()
            
            logger.info(
                f"Deactivated customer: {customer.id}",
                extra={
                    "customer_id": str(customer.id),
                    "previous_status": previous_status,
                    "had_balance": float(customer.balance),
                    "forced": force,
                }
            )
            
            return customer
            
        except (NotFoundException, BadRequestException):
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Database error deactivating customer {customer_id}")
            raise DatabaseException("Failed to deactivate customer") from e

    def check_delete_eligibility(self, customer_id: uuid.UUID) -> dict[str, Any]:
        """Check if customer can be deleted and what related records exist."""
        try:
            customer = self.get_by_id(customer_id)
            
            warnings: list[str] = []
            associated_records: dict[str, int] = {}
            
            # TODO: Check invoices when Invoice module exists
            # invoice_count = self._db.query(Invoice).filter(
            #     Invoice.customer_id == customer_id
            # ).count()
            # associated_records["invoices"] = invoice_count
            # if invoice_count > 0:
            #     unpaid_count = self._db.query(Invoice).filter(
            #         Invoice.customer_id == customer_id,
            #         Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.OVERDUE])
            #     ).count()
            #     warnings.append(
            #         f"{invoice_count} invoice(s) associated with this customer "
            #         f"({unpaid_count} unpaid)"
            #     )
            
            # TODO: Check quotes when Quote module exists
            # quote_count = self._db.query(Quote).filter(
            #     Quote.customer_id == customer_id
            # ).count()
            # associated_records["quotes"] = quote_count
            # if quote_count > 0:
            #     open_quotes = self._db.query(Quote).filter(
            #         Quote.customer_id == customer_id,
            #         Quote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT])
            #     ).count()
            #     warnings.append(
            #         f"{quote_count} quote(s) associated with this customer "
            #         f"({open_quotes} still open)"
            #     )
            
            # TODO: Check payments when Payment module exists
            # payment_count = self._db.query(Payment).join(Invoice).filter(
            #     Invoice.customer_id == customer_id
            # ).count()
            # associated_records["payments"] = payment_count
            
            # Check outstanding balance
            if customer.balance > 0:
                warnings.append(
                    f"Customer has outstanding balance: {customer.currency} {customer.balance}"
                )
            
            # Determine delete eligibility
            # Hard delete is only allowed if there are no warnings
            can_hard_delete = len(warnings) == 0
            can_soft_delete = True  # Soft delete is always allowed
            
            # Build response message
            if can_hard_delete:
                message = "Customer can be safely deleted. No associated records found."
                delete_type = "hard_allowed"
            else:
                message = (
                    "Customer has associated records or outstanding balance. "
                    "Soft delete recommended to preserve data integrity."
                )
                delete_type = "soft_only"
            
            return {
                "can_delete": can_soft_delete,
                "can_hard_delete": can_hard_delete,
                "delete_type": delete_type,
                "warnings": warnings,
                "associated_records": associated_records,
                "message": message,
            }
            
        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Error checking delete eligibility for {customer_id}")
            raise DatabaseException("Failed to check delete eligibility") from e

    
    # Delete

    def delete(
        self,
        customer_id: uuid.UUID,
        hard_delete: bool = False,
        force: bool = False
    ) -> dict[str, Any]:
        """Delete a customer (soft delete by default)."""
        try:
            customer = self.get_by_id(customer_id)
            
            # Check eligibility if hard delete is requested and not forced
            eligibility: dict[str, Any] = {}
            if hard_delete and not force:
                eligibility = self.check_delete_eligibility(customer_id)
                
                if not eligibility["can_hard_delete"]:
                    raise BadRequestException(
                        detail=(
                            "Cannot permanently delete customer with associated records. "
                            f"Issues: {'; '.join(eligibility['warnings'])}. "
                            "Use soft delete (default) to preserve data, or set force=true to override."
                        )
                    )
            
            # Perform deletion
            deleted_at = datetime.now(UTC)
            
            if hard_delete:
                # HARD DELETE - Permanent removal
                # TODO: Handle foreign key constraints when Invoice/Quote modules exist
                # Options:
                # 1. CASCADE: Delete all related invoices/quotes (destructive)
                # 2. SET NULL: Set customer_id to NULL in related records (orphan records)
                # 3. RESTRICT: Block deletion if related records exist (current behavior)
                
                # For now, delete the customer record
                # Database foreign keys will either cascade or raise an error
                self._db.delete(customer)
                delete_type = "hard"
                
                logger.warning(
                    f"Hard deleted customer: {customer_id}",
                    extra={
                        "customer_id": str(customer_id),
                        "customer_email": customer.email,
                        "customer_name": customer.display_name,
                        "forced": force,
                    }
                )
            else:
                # SOFT DELETE - Set status to deleted
                customer.status = CustomerStatus.DELETED
                delete_type = "soft"
                
                logger.info(
                    f"Soft deleted customer: {customer_id}",
                    extra={
                        "customer_id": str(customer_id),
                        "customer_email": customer.email,
                        "customer_name": customer.display_name,
                    }
                )

            self._db.flush()
            
            return {
                "customer_id": customer_id,
                "delete_type": delete_type,
                "deleted_at": deleted_at,
                "warnings": eligibility.get("warnings", []),
                "associated_records": eligibility.get("associated_records", {}),
            }

        except (NotFoundException, BadRequestException):
            raise
        except IntegrityError as e:
            # This catches foreign key violations if Invoice/Quote modules exist
            logger.exception(f"Integrity error deleting customer {customer_id}")
            raise BadRequestException(
                detail=(
                    "Cannot delete customer due to database constraints. "
                    "Customer has related records that must be handled first. "
                    "Use soft delete to preserve data integrity."
                )
            ) from e
        except SQLAlchemyError as e:
            logger.exception(f"Database error deleting customer {customer_id}")
            raise DatabaseException("Failed to delete customer") from e

    def batch_update_status(
        self, 
        customer_ids: list[uuid.UUID], 
        new_status: CustomerStatus,
    ) -> int:
        """Batch update customer status"""
        if len(customer_ids) == 0:
            return 0

        if len(customer_ids) > settings.BATCH_SIZE:
            raise BadRequestException(
                detail=(
                    f"Batch size {len(customer_ids)} exceeds maximum "
                    f"of {settings.BATCH_SIZE}. Split into smaller batches."
                ),
                field="customer_ids",
            )

        try:
            result = (
                self._db.query(Customer)
                .filter(Customer.id.in_(customer_ids))
                .update(
                    {Customer.status: new_status},
                    synchronize_session=False,
                )
            )

            if result != len(customer_ids):
                logger.warning(
                    "Batch update affected %d rows but %d IDs were provided "
                    "(some IDs may not exist)",
                    result,
                    len(customer_ids),
                )

            self._db.flush()

            logger.info(
                f"Batch updated {result} customers to status '{new_status}'",
                extra={
                    "customer_ids": [str(cid) for cid in customer_ids],
                    "new_status": new_status,
                    "count": result,
                },
            )

            return result

        except SQLAlchemyError as e:
            logger.exception("Database error in batch status update")
            raise DatabaseException("Failed to batch update customers") from e

    def get_financial_summary(self, customer_id: uuid.UUID) -> FinancialSummary:
        """Calculate financial summary for customer overview."""
        try:
            
            customer = self.get_by_id(customer_id)
            
            # TODO: Replace with actual invoice queries when Invoice module exists
            # For now, return zeros
            return FinancialSummary(
                total_unpaid=Decimal("0.00"),
                overdue=Decimal("0.00"),
                total_invoiced=Decimal("0.00"),
                total_paid=Decimal("0.00"),
            )
            
            # FUTURE IMPLEMENTATION (uncomment when Invoice module exists):
            # from app.modules.invoices.models import Invoice, InvoiceStatus
            #
            # invoices = self._db.query(Invoice).filter(Invoice.customer_id == customer_id)
            #
            # total_unpaid = (
            #     invoices
            #     .filter(Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.OVERDUE]))
            #     .with_entities(func.sum(Invoice.balance))
            #     .scalar() or Decimal("0.00")
            # )
            
            # # Overdue amount (past due date)
            # overdue = (
            #     invoices
            #     .filter(
            #         Invoice.status == InvoiceStatus.OVERDUE,
            #         Invoice.due_date < datetime.now(UTC)
            #     )
            #     .with_entities(func.sum(Invoice.balance))
            #     .scalar() or Decimal("0.00")
            # )
            
            # # Total invoiced (all time)
            # total_invoiced = (
            #     invoices
            #     .with_entities(func.sum(Invoice.total))
            #     .scalar() or Decimal("0.00")
            # )
            
            # # Total paid (all time)
            # total_paid = (
            #     invoices
            #     .filter(Invoice.status == InvoiceStatus.PAID)
            #     .with_entities(func.sum(Invoice.total))
            #     .scalar() or Decimal("0.00")
            # )
            
            # return FinancialSummary(
            #     total_unpaid=total_unpaid,
            #     overdue=overdue,
            #     total_invoiced=total_invoiced,
            #     total_paid=total_paid,
            # )
            
        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Error calculating financial summary for {customer_id}")
            raise DatabaseException("Failed to calculate financial summary") from e

    def get_invoices(
        self,
        customer_id: uuid.UUID,
        params: PaginationParams,
        status_filter: str | None = None,
    ) -> PaginatedResponse:  # noqa: F821
    # ) -> PaginatedResponse["InvoiceResponse"]:  # noqa: F821
        """ Get paginated invoices for a customer. """
        try:
            # Verify customer exists
            self.get_by_id(customer_id)

            # from app.modules.invoices.models import Invoice
            # from app.modules.invoices.schemas import InvoiceResponse
            
            # query = (
            #     self._db.query(Invoice)
            #     .filter(Invoice.customer_id == customer_id)
            # )
            
            # # Apply status filter
            # if status_filter and status_filter != "all":
            #     query = query.filter(Invoice.status == status_filter)
            
            # # Get total
            # total = query.count()
            
            # # Apply pagination and sorting
            # invoices = (
            #     query
            #     .order_by(Invoice.created_at.desc())
            #     .offset(params.offset)
            #     .limit(params.limit)
            #     .all()
            # )
            
            # items = [InvoiceResponse.model_validate(inv) for inv in invoices]
            
            # return PaginatedResponse.create(items=items, total=total, params=params)
            raise NotImplementedError(
                "Invoice module not yet implemented. "
                "This endpoint will be available after Invoice module is created."
            )
            
        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Error fetching invoices for {customer_id}")
            raise DatabaseException("Failed to fetch invoices") from e

    def generate_statement(
        self,
        customer_id: uuid.UUID,
        period_start: date,
        period_end: date,
    ) -> CustomerStatement:
        """Generate a statement of accounts for a customer."""
        try:
             # Verify customer exists
            customer = self.get_by_id(customer_id)

            return CustomerStatement(
                customer=CustomerResponse.model_validate(customer),
                period_start=period_start,
                period_end=period_end,
                summary=StatementSummary(
                    opening_balance=Decimal("0.00"),
                    invoiced_amount=Decimal("0.00"),
                    amount_paid=Decimal("0.00"),
                    balance_due=Decimal("0.00"),
                ),
                transactions=[],
                generated_at=datetime.now(UTC),
            )

            # from app.modules.invoices.models import Invoice
            # from app.modules.payments.models import Payment
            
            # # Calculate opening balance (all transactions before period_start)
            # opening_balance = self._calculate_balance_at_date(
            #     customer_id,
            #     period_start
            # )
            
            # # Get all invoices in period
            # period_invoices = (
            #     self._db.query(Invoice)
            #     .filter(
            #         Invoice.customer_id == customer_id,
            #         Invoice.invoice_date >= period_start,
            #         Invoice.invoice_date <= period_end,
            #     )
            #     .order_by(Invoice.invoice_date)
            #     .all()
            # )
            
            # # Get all payments in period
            # period_payments = (
            #     self._db.query(Payment)
            #     .join(Invoice)
            #     .filter(
            #         Invoice.customer_id == customer_id,
            #         Payment.payment_date >= period_start,
            #         Payment.payment_date <= period_end,
            #     )
            #     .order_by(Payment.payment_date)
            #     .all()
            # )
            
            # # Build transaction ledger
            # transactions: list[StatementTransaction] = []
            # running_balance = opening_balance
            
            # # Add opening balance row
            # transactions.append(
            #     StatementTransaction(
            #         date=period_start,
            #         description="Opening Balance",
            #         amount=opening_balance,
            #         payment=Decimal("0.00"),
            #         balance=opening_balance,
            #     )
            # )
            
            # # Merge invoices and payments chronologically
            # all_transactions = sorted(
            #     [("invoice", inv) for inv in period_invoices] +
            #     [("payment", pmt) for pmt in period_payments],
            #     key=lambda x: x[1].invoice_date if x[0] == "invoice" else x[1].payment_date
            # )
            
            # invoiced_amount = Decimal("0.00")
            # amount_paid = Decimal("0.00")
            
            # for trans_type, trans in all_transactions:
            #     if trans_type == "invoice":
            #         running_balance += trans.total
            #         invoiced_amount += trans.total
                    
            #         transactions.append(
            #             StatementTransaction(
            #                 date=trans.invoice_date,
            #                 description=f"Invoice {trans.invoice_number}",
            #                 amount=trans.total,
            #                 payment=Decimal("0.00"),
            #                 balance=running_balance,
            #             )
            #         )
                    
            #     else:  # payment
            #         running_balance -= trans.amount
            #         amount_paid += trans.amount
                    
            #         transactions.append(
            #             StatementTransaction(
            #                 date=trans.payment_date,
            #                 description=f"Payment Received — KES {trans.amount} for {trans.invoice.invoice_number}",
            #                 amount=Decimal("0.00"),
            #                 payment=trans.amount,
            #                 balance=running_balance,
            #             )
            #         )
            
            # # Build summary
            # summary = StatementSummary(
            #     opening_balance=opening_balance,
            #     invoiced_amount=invoiced_amount,
            #     amount_paid=amount_paid,
            #     balance_due=running_balance,
            # )
            
            # return CustomerStatement(
            #     customer=CustomerResponse.model_validate(customer),
            #     period_start=period_start,
            #     period_end=period_end,
            #     summary=summary,
            #     transactions=transactions,
            #     generated_at=datetime.now(UTC),
            # )
            
        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Error generating statement for {customer_id}")
            raise DatabaseException("Failed to generate statement") from e

    def _calculate_balance_at_date(
        self,
        customer_id: uuid.UUID,
        as_of_date: date,
    ) -> Decimal:
        """Calculate customer balance as of a specific date."""
        return 0
        # from app.modules.invoices.models import Invoice
        # from app.modules.payments.models import Payment
        
        # # Sum all invoices before date
        # invoiced = (
        #     self._db.query(func.sum(Invoice.total))
        #     .filter(
        #         Invoice.customer_id == customer_id,
        #         Invoice.invoice_date < as_of_date,
        #     )
        #     .scalar() or Decimal("0.00")
        # )
        
        # # Sum all payments before date
        # paid = (
        #     self._db.query(func.sum(Payment.amount))
        #     .join(Invoice)
        #     .filter(
        #         Invoice.customer_id == customer_id,
        #         Payment.payment_date < as_of_date,
        #     )
        #     .scalar() or Decimal("0.00")
        # )
        
        # return invoiced - paid
        return Decimal("0.00")