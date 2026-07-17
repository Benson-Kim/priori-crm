import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.common.audit import record_audit_event, status_value
from app.common.database import assert_version
from app.common.document_service import ServiceBase
from app.common.exceptions import (
    AppException,
    BadRequestException,
    ConflictException,
    DatabaseException,
    NotFoundException,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.search import build_search_clause
from app.common.statement import (
    EXCLUDED_STATEMENT_STATUSES,
    CreditEntry,
    DebitEntry,
    StatementGenerator,
)
from app.common.statistics import status_counts
from app.constants.enums import CustomerStatus
from app.modules.customers.models import Customer
from app.modules.customers.schemas import (
    CustomerCreate,
    CustomerResponse,
    CustomerStatement,
    CustomerStatusCounts,
    CustomerSummary,
    CustomerUpdate,
    FinancialSummary,
    StatementSummary,
    StatementTransaction,
)
from app.modules.invoices.models import Invoice, InvoiceStatus, Payment
from app.modules.invoices.schemas import InvoiceResponse
from app.modules.quotes.models import Quote, QuoteStatus

logger = logging.getLogger(__name__)


class CustomerService(ServiceBase):
    """Handles customer CRUD business logic.

    Constructor and the public actor surface come from the shared
    ServiceBase.
    """

    # Create

    def create(self, data: CustomerCreate) -> Customer:
        """Create a new customer."""
        try:
            # Case-insensitive identity: store and compare emails lowercased
            # so 'Alpha@x.com' and 'alpha@x.com' are one customer.
            email = data.email.strip().lower()
            existing = self._db.query(Customer).filter(Customer.email == email).first()
            if existing:
                raise ConflictException(
                    detail=f"Customer with email '{email}' already exists",
                    field="email",
                )

            customer = Customer(
                customer_type=data.customer_type,
                company_name=data.company_name,
                first_name=data.first_name,
                last_name=data.last_name,
                email=email,
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
            raise ConflictException(
                "Customer data violates database constraints"
            ) from e
        except SQLAlchemyError as e:
            logger.exception("Database error creating customer")
            raise DatabaseException("Failed to create customer") from e

    # Read One

    def get_by_id(
        self, customer_id: uuid.UUID, include_deleted: bool = False
    ) -> Customer:
        """Get a customer by ID or raise 404."""
        try:
            query = self._db.query(Customer).filter(Customer.id == customer_id)
            if not include_deleted:
                query = query.filter(Customer.status != CustomerStatus.DELETED)
            customer = query.first()
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
                    raise BadRequestException(
                        detail=f"Invalid status: {status}", field="status"
                    ) from None

            else:
                # Default list (no explicit status / 'all') hides soft-deleted
                # customers. Surfacing them requires status=deleted.
                query = query.filter(Customer.status != CustomerStatus.DELETED)

            if search:
                search_clause = build_search_clause(
                    search,
                    Customer.first_name,
                    Customer.last_name,
                    Customer.email,
                    Customer.company_name,
                )
                if search_clause is not None:
                    query = query.filter(search_clause)

            # Only pay for COUNT(*) when the caller asks for the total
            # ; otherwise derive has_next from the over-fetch.
            total = query.count() if params.with_total else None

            customers = (
                query.order_by(Customer.created_at.desc())
                .offset(params.offset)
                .limit(params.fetch_limit)
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
                f"Listed customers (page {params.page}, total {total})",
                extra={
                    "page": params.page,
                    "per_page": params.per_page,
                    "total": total,
                    "status_filter": status,
                    "search_term": search,
                },
            )

            return PaginatedResponse.create_from_window(
                rows=items, params=params, total=total
            )

        except BadRequestException:
            raise
        except SQLAlchemyError as e:
            logger.exception("Database error listing customers")
            raise DatabaseException("Failed to list customers") from e

    def get_status_counts(self) -> CustomerStatusCounts:
        """Get counts of customers by status."""
        counts_dict = status_counts(
            self._db,
            Customer.status,
            Customer.id,
            error_context="customer",
        )
        # `all` sums every returned status row so the total stays correct if
        # the status set ever expands.
        counts = CustomerStatusCounts(
            all=sum(counts_dict.values()),
            active=counts_dict.get(CustomerStatus.ACTIVE, 0),
            inactive=counts_dict.get(CustomerStatus.INACTIVE, 0),
            suspended=counts_dict.get(CustomerStatus.SUSPENDED, 0),
            deleted=counts_dict.get(CustomerStatus.DELETED, 0),
        )

        logger.debug(f"Customer status counts: {counts}")

        return counts

    # Update

    def update(
        self,
        customer_id: uuid.UUID,
        data: CustomerUpdate,
        expected_version: int | None = None,
    ) -> Customer:
        """Update an existing customer with optimistic locking

        ``expected_version`` mirrors the vendor/invoice/quote/expense
        contract: when provided, the row is locked and the version compared
        atomically, so a stale writer gets a 409 instead of silently
        overwriting concurrent edits to customer master data.
        """
        try:
            customer = self.get_by_id(customer_id)

            # locks the row, compares versions, raises 409 on mismatch.
            assert_version(self._db, Customer, customer_id, expected_version)

            update_data = data.model_dump(exclude_unset=True)

            if not update_data:
                return customer

            # Normalize email to lowercase before conflict check + persist
            if "email" in update_data and update_data["email"] is not None:
                update_data["email"] = update_data["email"].strip().lower()

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

            # Currency freeze: invoices and quotes are pinned to
            # the customer's currency at creation time, so changing it after
            # documents exist would silently desync balances, statements and
            # vendor-side aggregations. Same-value no-ops stay allowed.
            if (
                "currency" in update_data
                and update_data["currency"] is not None
                and update_data["currency"] != customer.currency
            ):
                has_documents = (
                    self._db.query(Invoice.id)
                    .filter(Invoice.customer_id == customer_id)
                    .first()
                    is not None
                    or self._db.query(Quote.id)
                    .filter(Quote.customer_id == customer_id)
                    .first()
                    is not None
                )
                if has_documents:
                    raise BadRequestException(
                        detail=(
                            "Cannot change currency for a customer with "
                            "existing invoices or quotes. All documents are "
                            f"pinned to '{customer.currency}'."
                        ),
                        field="currency",
                    )

            for field, value in update_data.items():
                setattr(customer, field, value)

            customer.version += 1
            self._db.flush()

            logger.info(
                f"Updated customer: {customer.id}",
                extra={
                    "customer_id": str(customer.id),
                    "updated_fields": list(update_data.keys()),
                    "new_version": customer.version,
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
                    extra={"customer_id": str(customer_id)},
                )
                return customer

            # Prevent activation of deleted customers
            if customer.status == CustomerStatus.DELETED:
                raise BadRequestException(
                    detail="Cannot activate a deleted customer. Please restore it first.",
                    field="status",
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
                },
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
                    extra={"customer_id": str(customer_id)},
                )
                return customer

            # Prevent deactivation of deleted customers
            if customer.status == CustomerStatus.DELETED:
                raise BadRequestException(
                    detail="Cannot deactivate a deleted customer", field="status"
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
                        field="balance",
                    )

                open_quotes = (
                    self._db.query(Quote)
                    .filter(
                        Quote.customer_id == customer_id,
                        Quote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT]),
                    )
                    .count()
                )
                if open_quotes > 0:
                    raise BadRequestException(
                        detail=(
                            f"Customer has {open_quotes} open quote(s). "
                            "Please finalize or cancel quotes before deactivating."
                        ),
                        field="quotes",
                    )

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
                },
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

            # Check invoices
            invoice_count = (
                self._db.query(Invoice)
                .filter(Invoice.customer_id == customer_id)
                .count()
            )
            associated_records["invoices"] = invoice_count
            if invoice_count > 0:
                # PARTIAL invoices still carry an outstanding balance and must
                # count as unpaid, otherwise a partially-paid customer looks
                # safe to hard-delete.
                unpaid_count = (
                    self._db.query(Invoice)
                    .filter(
                        Invoice.customer_id == customer_id,
                        Invoice.status.in_(
                            [
                                InvoiceStatus.SENT,
                                InvoiceStatus.PARTIAL,
                                InvoiceStatus.OVERDUE,
                            ]
                        ),
                    )
                    .count()
                )
                warnings.append(
                    f"{invoice_count} invoice(s) associated with this customer "
                    f"({unpaid_count} unpaid)"
                )

            # Check quotes
            quote_count = (
                self._db.query(Quote).filter(Quote.customer_id == customer_id).count()
            )
            associated_records["quotes"] = quote_count
            if quote_count > 0:
                open_quotes = (
                    self._db.query(Quote)
                    .filter(
                        Quote.customer_id == customer_id,
                        Quote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT]),
                    )
                    .count()
                )
                warnings.append(
                    f"{quote_count} quote(s) associated with this customer "
                    f"({open_quotes} still open)"
                )

            # Check payments
            payment_count = (
                self._db.query(Payment)
                .join(Invoice)
                .filter(Invoice.customer_id == customer_id)
                .count()
            )
            associated_records["payments"] = payment_count

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
        self, customer_id: uuid.UUID, hard_delete: bool = False, force: bool = False
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
                # The customer FK on invoices/quotes uses RESTRICT, so
                # check_delete_eligibility gates this path. If force=True the
                # caller bypasses that check and the DB-level RESTRICT is the
                # last line of defence. Isolate the DELETE in a SAVEPOINT so an
                # FK rejection rolls back only the nested block and surfaces as
                # a clean BadRequestException, without poisoning the caller's
                # outer transaction.
                # Durable audit trail — written before the DELETE
                # so the row snapshot is still readable; rolls back with it
                # if the FK guard rejects the delete.
                record_audit_event(
                    self._db,
                    actor_id=self._actor_id,
                    entity_type="customer",
                    entity_id=customer.id,
                    action="hard_deleted",
                    before={
                        "email": customer.email,
                        "display_name": customer.display_name,
                        "balance": str(customer.balance),
                        "forced": force,
                    },
                )
                try:
                    with self._db.begin_nested():
                        self._db.delete(customer)
                        self._db.flush()
                except IntegrityError as e:
                    logger.warning(
                        f"Hard delete blocked by FK constraint: {customer_id}",
                        extra={"customer_id": str(customer_id)},
                    )
                    raise BadRequestException(
                        detail=(
                            "Cannot permanently delete customer with associated "
                            "records (invoices or quotes). Use soft delete to "
                            "preserve data integrity."
                        )
                    ) from e
                delete_type = "hard"

                logger.warning(
                    f"Hard deleted customer: {customer_id}",
                    extra={
                        "customer_id": str(customer_id),
                        "customer_email": customer.email,
                        "customer_name": customer.display_name,
                        "forced": force,
                    },
                )
            else:
                # SOFT DELETE - Set status to deleted
                previous_status = customer.status
                customer.status = CustomerStatus.DELETED
                delete_type = "soft"
                record_audit_event(
                    self._db,
                    actor_id=self._actor_id,
                    entity_type="customer",
                    entity_id=customer.id,
                    action="soft_deleted",
                    before={"status": status_value(previous_status)},
                    after={"status": status_value(CustomerStatus.DELETED)},
                )

                logger.info(
                    f"Soft deleted customer: {customer_id}",
                    extra={
                        "customer_id": str(customer_id),
                        "customer_email": customer.email,
                        "customer_name": customer.display_name,
                    },
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

    def get_financial_summary(self, customer_id: uuid.UUID) -> FinancialSummary:
        """
        Calculate financial summary for customer overview.
        """
        try:
            today = date.today()

            row = (
                self._db.query(
                    # Total unpaid: balance on sent + overdue invoices
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Invoice.status.in_(
                                        [
                                            InvoiceStatus.SENT,
                                            InvoiceStatus.PARTIAL,
                                            InvoiceStatus.OVERDUE,
                                        ]
                                    ),
                                    Invoice.balance_due,
                                ),
                                else_=Decimal("0.00"),
                            )
                        ),
                        Decimal("0.00"),
                    ).label("total_unpaid"),
                    # Overdue: balance on invoices past due date
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    and_(
                                        Invoice.status == InvoiceStatus.OVERDUE,
                                        Invoice.due_date < today,
                                    ),
                                    Invoice.balance_due,
                                ),
                                else_=Decimal("0.00"),
                            )
                        ),
                        Decimal("0.00"),
                    ).label("overdue"),
                    # Total invoiced (all time, all statuses)
                    func.coalesce(
                        func.sum(Invoice.total_due),
                        Decimal("0.00"),
                    ).label("total_invoiced"),
                    # Total paid
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Invoice.status == InvoiceStatus.PAID,
                                    Invoice.total_due,
                                ),
                                else_=Decimal("0.00"),
                            )
                        ),
                        Decimal("0.00"),
                    ).label("total_paid"),
                )
                .filter(Invoice.customer_id == customer_id)
                .one()
            )

            return FinancialSummary(
                total_unpaid=Decimal(str(row.total_unpaid)),
                overdue=Decimal(str(row.overdue)),
                total_invoiced=Decimal(str(row.total_invoiced)),
                total_paid=Decimal(str(row.total_paid)),
            )

        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception("Error calculating financial summary for %s", customer_id)
            raise DatabaseException("Failed to calculate financial summary") from e

    def get_invoices(
        self,
        customer_id: uuid.UUID,
        params: PaginationParams,
        status_filter: str | None = None,
    ) -> PaginatedResponse[InvoiceResponse]:
        """Get paginated invoices for a customer."""
        try:
            # Verify customer exists
            self.get_by_id(customer_id)

            query = self._db.query(Invoice).filter(Invoice.customer_id == customer_id)

            # Apply status filter
            if status_filter and status_filter != "all":
                query = query.filter(Invoice.status == status_filter)

            # Count only on request; over-fetch otherwise.
            total = query.count() if params.with_total else None

            invoices = (
                query.order_by(Invoice.created_at.desc())
                .offset(params.offset)
                .limit(params.fetch_limit)
                .all()
            )

            items = [InvoiceResponse.model_validate(inv) for inv in invoices]

            return PaginatedResponse.create_from_window(
                rows=items, params=params, total=total
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

            # Calculate opening balance (all transactions before period_start)
            opening_balance = self._calculate_balance_at_date(customer_id, period_start)

            # Get all invoices in period
            period_invoices = (
                self._db.query(Invoice)
                .filter(
                    Invoice.customer_id == customer_id,
                    Invoice.transaction_date >= period_start,
                    Invoice.transaction_date <= period_end,
                )
                .order_by(Invoice.transaction_date)
                .all()
            )

            # Get all payments in period
            period_payments = (
                self._db.query(Payment)
                .join(Invoice)
                .filter(
                    Invoice.customer_id == customer_id,
                    Payment.payment_date >= period_start,
                    Payment.payment_date <= period_end,
                )
                .order_by(Payment.payment_date)
                .all()
            )

            # Build entries for the shared generator
            debits = [
                DebitEntry(
                    date=inv.transaction_date,
                    description=f"Invoice {inv.invoice_number}",
                    amount=inv.total_due,
                )
                for inv in period_invoices
            ]
            credits = [
                CreditEntry(
                    date=pmt.payment_date,
                    description=(
                        f"Payment Received — {customer.currency} {pmt.amount} "
                        f"for {pmt.invoice.invoice_number}"
                    ),
                    amount=pmt.amount,
                )
                for pmt in period_payments
            ]

            # Delegate to shared StatementGenerator
            txns, summary_data = StatementGenerator.generate(
                opening_balance=opening_balance,
                period_start=period_start,
                debits=debits,
                credits=credits,
            )

            transactions = [
                StatementTransaction(
                    date=t["date"],
                    description=t["description"],
                    amount=t["amount"],
                    payment=t["payment"],
                    balance=t["balance"],
                )
                for t in txns
            ]

            summary = StatementSummary(
                opening_balance=summary_data["opening_balance"],
                invoiced_amount=summary_data["invoiced_amount"],
                amount_paid=summary_data["amount_paid"],
                balance_due=summary_data["balance_due"],
            )

            return CustomerStatement(
                customer=CustomerResponse.model_validate(customer),
                period_start=period_start,
                period_end=period_end,
                summary=summary,
                transactions=transactions,
                generated_at=datetime.now(UTC),
            )

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
        """Calculate customer balance as of a specific date (invoiced - paid)."""
        # Sum invoice totals before date, excluding DRAFT/CANCELED so the
        # opening balance matches _update_customer_balance and the persisted
        # Customer.balance.
        invoiced = self._db.query(func.sum(Invoice.total_due)).filter(
            Invoice.customer_id == customer_id,
            Invoice.transaction_date < as_of_date,
            Invoice.status.notin_(EXCLUDED_STATEMENT_STATUSES),
        ).scalar() or Decimal("0.00")

        # Sum payments before date with the SAME status predicate as the
        # debit side: a payment recorded against a since-canceled
        # invoice must not push the opening balance negative.
        paid = self._db.query(func.sum(Payment.amount)).join(Invoice).filter(
            Invoice.customer_id == customer_id,
            Payment.payment_date < as_of_date,
            Invoice.status.notin_(EXCLUDED_STATEMENT_STATUSES),
        ).scalar() or Decimal("0.00")

        return Decimal(str(invoiced)) - Decimal(str(paid))

    # PDF GENERATION

    def _render_pdf(self, statement: CustomerStatement) -> bytes:
        """Render a PDF for an already-generated customer statement.

        Orchestration (owner branding + ReportLab generator) is shared with
        invoices/quotes via DocumentPdfRenderer.

        A rendering failure is not swallowed: it surfaces as an explicit 500
        carrying the message so the UI can show "PDF could not be
        generated. Please try again." instead of leaking a stack trace.
        """
        from app.common.pdf_renderer import DocumentPdfRenderer

        try:
            return DocumentPdfRenderer(self._db).render_customer_statement(statement)
        except Exception as exc:
            logger.exception(
                "Failed to render customer statement PDF",
                extra={"customer_id": str(statement.customer.id)},
            )
            raise AppException(
                status_code=500,
                detail="PDF could not be generated. Please try again.",
                error_code="PDF_GENERATION_FAILED",
            ) from exc

    def generate_statement_pdf(
        self,
        customer_id: uuid.UUID,
        period_start: date,
        period_end: date,
    ) -> tuple[bytes, CustomerStatement]:
        """Generate the statement-of-accounts PDF for a customer and period.

        Builds the statement once and returns it alongside the rendered
        bytes, so the download endpoint never re-queries just for the
        attachment filename.
        """
        statement = self.generate_statement(customer_id, period_start, period_end)
        return self._render_pdf(statement), statement
