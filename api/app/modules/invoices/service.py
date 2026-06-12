"""Invoice business logic with complex financial calculations and state machine."""

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, lazyload

from app.common.audit import record_audit_event, status_value
from app.common.database import assert_version
from app.common.document_service import BaseDocumentService
from app.common.exceptions import (
    BadRequestException,
    DatabaseException,
    NotFoundException,
)
from app.common.financial import (
    build_line_items,
    calculate_discount,
    sum_line_totals,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import DiscountType, InvoiceStatus
from app.modules.invoices.models import Invoice, InvoiceLineItem, Payment
from app.modules.invoices.queries import (
    InvoiceExportQuery,
    InvoiceStatisticsRepository,
    apply_invoice_filters,
)
from app.modules.invoices.schemas import (
    InvoiceCalculationResponse,
    InvoiceCreate,
    InvoiceDuplicateResponse,
    InvoiceFilterParams,
    InvoiceStatusCounts,
    InvoiceSummary,
    InvoiceUpdate,
    PaymentCreate,
)

logger = logging.getLogger(__name__)


class InvoiceService(BaseDocumentService):
    """
    Service layer for invoice operations.

    Follows the same atomicity and race-condition contracts as QuoteService.
    Shared state-machine, reference-retry and email mechanics come from
    BaseDocumentService; the transition table and reference formats below stay
    invoice-specific.
    """

    MAX_INVOICE_NUMBER_RETRIES = 3

    _document_noun = "invoice"
    _reference_collision_markers = ("invoice_number", "invoice_reference")
    _email_terms: ClassVar[dict[str, str]] = {
        "noun": "invoice",
        "date_label": "Due date",
        "closing": "Thank you for your business.",
    }

    # Two-phase send wiring (DocumentSendMixin / ISSUE-002)
    _send_model = Invoice
    _send_draft_status = InvoiceStatus.DRAFT
    _send_sent_status = InvoiceStatus.SENT

    # Shared preview-totals + reference-retry wiring (ISSUE-012)
    _calculation_response_cls = InvoiceCalculationResponse
    MAX_REFERENCE_RETRIES = MAX_INVOICE_NUMBER_RETRIES

    ALLOWED_TRANSITIONS: ClassVar[dict[InvoiceStatus, list[InvoiceStatus]]] = {
        InvoiceStatus.DRAFT: [InvoiceStatus.SENT, InvoiceStatus.CANCELED],
        InvoiceStatus.SENT: [
            InvoiceStatus.PARTIAL,
            InvoiceStatus.PAID,
            InvoiceStatus.OVERDUE,
            InvoiceStatus.CANCELED,
        ],
        InvoiceStatus.PARTIAL: [
            InvoiceStatus.PAID,
            InvoiceStatus.OVERDUE,
            InvoiceStatus.CANCELED,
        ],
        InvoiceStatus.OVERDUE: [
            InvoiceStatus.PARTIAL,
            InvoiceStatus.PAID,
            InvoiceStatus.CANCELED,
        ],
        InvoiceStatus.PAID: [InvoiceStatus.CANCELED],
        InvoiceStatus.CANCELED: [],
    }

    # CREATE

    def create(self, data: InvoiceCreate, user_id: uuid.UUID | None = None) -> Invoice:
        """
        Create a new invoice with line items and calculations.
        Uses bounded retry for invoice number collisions.
        """
        # Validate customer exists and is active
        from app.constants.enums import CustomerStatus
        from app.modules.customers.models import Customer

        customer = (
            self._db.query(Customer).filter(Customer.id == data.customer_id).first()
        )

        if not customer:
            raise NotFoundException(
                detail=f"Customer with ID '{data.customer_id}' not found",
                resource="customer",
            )

        if customer.status != CustomerStatus.ACTIVE:
            raise BadRequestException(
                detail=f"Cannot create invoice for inactive customer: {customer.display_name}",
                field="customer_id",
            )

        # Single-currency-per-customer: a customer holds exactly one
        # currency and balance summing assumes it. Reject a document whose
        # currency was *explicitly* set to something other than the
        # customer's; otherwise pin it to the customer's currency (the
        # schema default must not silently override it).
        if "currency" in data.model_fields_set and data.currency != customer.currency:
            raise BadRequestException(
                detail=(
                    f"Invoice currency '{data.currency}' does not match the "
                    f"customer's currency '{customer.currency}'. A customer "
                    "can only transact in a single currency."
                ),
                field="currency",
            )
        invoice_currency = customer.currency

        line_items_data = build_line_items(data.line_items)
        subtotal, tax_total = sum_line_totals(line_items_data)

        discount_value = calculate_discount(
            subtotal, data.discount_type, data.discount_amount, data.discount_percentage
        )
        total_due = subtotal - discount_value + tax_total

        def _build() -> Invoice:
            invoice = Invoice(
                invoice_number=self._generate_invoice_number(),
                invoice_reference=self._generate_invoice_reference(),
                customer_id=data.customer_id,
                transaction_date=data.transaction_date,
                due_date=data.due_date,
                currency=invoice_currency,
                status=InvoiceStatus.DRAFT,
                subtotal=subtotal,
                discount_type=data.discount_type,
                discount_amount=data.discount_amount,
                discount_percentage=data.discount_percentage,
                tax_total=tax_total,
                total_due=total_due,
                amount_paid=Decimal("0.00"),
                balance_due=total_due,
                rfq_number=data.rfq_number,
                notes=data.notes,
                created_by=user_id,
            )
            self._db.add(invoice)
            self._db.flush()
            for item in line_items_data:
                self._db.add(InvoiceLineItem(invoice_id=invoice.id, **item))
            self._db.flush()
            return invoice

        invoice = self._with_reference_retry(_build, "invoice")

        logger.info(
            "Created invoice: %s",
            invoice.invoice_number,
            extra={
                "invoice_id": str(invoice.id),
                "customer_id": str(data.customer_id),
                "total_due": float(total_due),
                "created_by": str(user_id) if user_id else None,
            },
        )
        return invoice

    # READ

    # Single source of truth for invoice filtering (ISSUE-011) — module-level
    # in queries.py so InvoiceExportQuery shares the identical function.
    _apply_filters = staticmethod(apply_invoice_filters)

    def get_by_id(self, invoice_id: uuid.UUID) -> Invoice:
        """
        Retrieve invoice by ID with all relationships loaded.
        Returns:
            Invoice: Invoice with line_items, payments, and customer
        """
        try:
            invoice = (
                self._db.query(Invoice)
                .options(
                    joinedload(Invoice.line_items),
                    joinedload(Invoice.payments),
                    joinedload(Invoice.customer),
                )
                .filter(Invoice.id == invoice_id)
                .first()
            )

            if not invoice:
                raise NotFoundException(
                    detail=f"Invoice with ID '{invoice_id}' not found",
                    resource="invoice",
                )

            return invoice

        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Database error retrieving invoice {invoice_id}")
            raise DatabaseException("Failed to retrieve invoice") from e

    def get_by_number(self, invoice_number: str) -> Invoice:
        """
        Retrieve invoice by invoice number.
        """
        try:
            invoice = (
                self._db.query(Invoice)
                .options(
                    joinedload(Invoice.line_items),
                    joinedload(Invoice.payments),
                    joinedload(Invoice.customer),
                )
                .filter(Invoice.invoice_number == invoice_number)
                .first()
            )

            if not invoice:
                raise NotFoundException(
                    detail=f"Invoice '{invoice_number}' not found", resource="invoice"
                )

            return invoice

        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Database error retrieving invoice {invoice_number}")
            raise DatabaseException("Failed to retrieve invoice") from e

    def list_invoices(
        self,
        params: PaginationParams,
        filters: InvoiceFilterParams | None = None,
    ) -> PaginatedResponse[InvoiceSummary]:
        """
        List invoices with pagination and filtering.
        """
        try:
            from app.modules.customers.models import Customer

            # Base query with customer join for display name
            query = self._db.query(
                Invoice.id,
                Invoice.invoice_number,
                Invoice.invoice_reference,
                Invoice.customer_id,
                Invoice.transaction_date,
                Invoice.due_date,
                Invoice.status,
                Invoice.currency,
                Invoice.total_due,
                Invoice.balance_due,
                Invoice.created_at,
                Customer.company_name,
                Customer.first_name,
                Customer.last_name,
            ).join(Customer, Invoice.customer_id == Customer.id)

            query = self._apply_filters(query, filters)

            total = query.count()

            results = (
                query.order_by(Invoice.created_at.desc())
                .offset(params.offset)
                .limit(params.per_page)
                .all()
            )

            items = []
            for row in results:
                if row.company_name:
                    customer_name = row.company_name
                else:
                    customer_name = f"{row.first_name} {row.last_name}".strip()

                items.append(
                    InvoiceSummary(
                        id=row.id,
                        invoice_number=row.invoice_number,
                        invoice_reference=row.invoice_reference,
                        customer_id=row.customer_id,
                        customer_name=customer_name,
                        transaction_date=row.transaction_date,
                        due_date=row.due_date,
                        status=row.status,
                        currency=row.currency,
                        total_due=row.total_due,
                        balance_due=row.balance_due,
                        created_at=row.created_at,
                    )
                )

            logger.debug(
                f"Listed {len(items)} invoices (page {params.page}, total {total})",
                extra={
                    "page": params.page,
                    "per_page": params.per_page,
                    "total": total,
                    "filters": filters.model_dump() if filters else None,
                },
            )

            return PaginatedResponse.create(items=items, total=total, params=params)

        except SQLAlchemyError as e:
            logger.exception("Database error listing invoices")
            raise DatabaseException("Failed to list invoices") from e

    def list_for_export(
        self,
        filters: InvoiceFilterParams | None = None,
        include_line_items: bool = False,
        limit: int | None = None,
    ) -> list[Invoice]:
        """Return full Invoice ORM rows for Excel export, batch-loaded.

        Delegates to InvoiceExportQuery (ISSUE-009).
        """
        return InvoiceExportQuery(self._db).list(
            filters, include_line_items=include_line_items, limit=limit
        )

    def get_status_counts(
        self, customer_id: uuid.UUID | None = None
    ) -> InvoiceStatusCounts:
        """Get counts of invoices by status.

        Delegates to InvoiceStatisticsRepository (ISSUE-009).
        """
        return InvoiceStatisticsRepository(self._db).status_counts(customer_id)

    def get_invoice_statistics(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """Get invoice statistics for dashboard.

        Delegates to InvoiceStatisticsRepository (ISSUE-009).
        """
        return InvoiceStatisticsRepository(self._db).statistics(date_from, date_to)

    # UPDATE

    def update(
        self,
        invoice_id: uuid.UUID,
        data: InvoiceUpdate,
        expected_version: int | None = None,
    ) -> Invoice:
        """
        Update an existing invoice with optimistic locking.

        Editing restrictions based on status:
        - DRAFT: Full editing allowed
        - SENT: Limited editing (no customer/amount changes)
        - PAID/OVERDUE/CANCELED: Read-only (no edits)
        """
        invoice = self.get_by_id(invoice_id)

        if not invoice.is_editable:
            raise BadRequestException(
                detail=f"Cannot edit invoice in {invoice.status} status", field="status"
            )

        # Atomic optimistic-lock guard: locks the row and compares the
        # version under the lock, so concurrent edits conflict instead of
        # silently overwriting.
        assert_version(self._db, Invoice, invoice_id, expected_version)

        update_data = data.model_dump(exclude_unset=True)

        if not update_data:
            return invoice  # No updates

        if invoice.status == InvoiceStatus.SENT:
            restricted_fields = ["customer_id", "transaction_date", "currency"]
            for field in restricted_fields:
                if field in update_data:
                    raise BadRequestException(
                        detail=f"Cannot change {field} after invoice has been sent",
                        field=field,
                    )

        if "discount_type" in update_data:
            new_type = update_data["discount_type"]

            if new_type == DiscountType.AMOUNT:
                update_data.setdefault("discount_percentage", None)
            elif new_type == DiscountType.PERCENTAGE:
                update_data.setdefault("discount_amount", None)
            else:
                update_data.setdefault("discount_amount", None)
                update_data.setdefault("discount_percentage", None)

        # Handle line items update (replace all)
        if "line_items" in update_data:
            self._db.query(InvoiceLineItem).filter(
                InvoiceLineItem.invoice_id == invoice_id
            ).delete()

            line_items_raw: list[dict] = update_data.pop("line_items")
            line_items_data = build_line_items(line_items_raw)
            subtotal, tax_total = sum_line_totals(line_items_data)

            for item in line_items_data:
                self._db.add(InvoiceLineItem(invoice_id=invoice.id, **item))

            update_data["subtotal"] = subtotal
            update_data["tax_total"] = tax_total

        # Recalculate totals if discount or subtotal changed
        recompute_balance = any(
            k in update_data
            for k in [
                "subtotal",
                "discount_type",
                "discount_amount",
                "discount_percentage",
            ]
        )
        if recompute_balance:
            subtotal = update_data.get("subtotal", invoice.subtotal)
            tax_total = update_data.get("tax_total", invoice.tax_total)

            effective_type = update_data.get("discount_type", invoice.discount_type)
            effective_amount = update_data.get(
                "discount_amount", invoice.discount_amount
            )
            effective_percentage = update_data.get(
                "discount_percentage", invoice.discount_percentage
            )

            discount_value = calculate_discount(
                subtotal, effective_type, effective_amount, effective_percentage
            )

            total_due = subtotal - discount_value + tax_total
            balance_due = total_due - invoice.amount_paid

            # Never let an edit drive the balance negative — that
            # would violate the balance_due >= 0 CHECK and surface as a 500.
            if balance_due < Decimal("0.00"):
                raise BadRequestException(
                    detail=(
                        f"Cannot reduce the invoice total below the amount already "
                        f"paid ({invoice.currency} {invoice.amount_paid}). "
                        f"Remove or reduce payments first."
                    ),
                    field="line_items",
                )

            update_data["total_due"] = total_due
            update_data["balance_due"] = balance_due

        # Apply updates
        for field, value in update_data.items():
            setattr(invoice, field, value)

        # Increment version for optimistic locking
        invoice.version += 1

        self._db.flush()

        # An edit that changed the balance of a non-DRAFT invoice must resync
        # the denormalized Customer.balance in the same unit of work.
        if recompute_balance and invoice.status != InvoiceStatus.DRAFT:
            self._update_customer_balance(invoice.customer_id)

        logger.info(
            f"Updated invoice: {invoice.invoice_number}",
            extra={
                "invoice_id": str(invoice.id),
                "updated_fields": list(update_data.keys()),
                "new_version": invoice.version,
            },
        )

        return invoice

    # STATUS TRANSITIONS & ACTIONS

    def _capture_owner_snapshot(self, invoice: Invoice) -> None:
        """Stamp an immutable owner-header snapshot the first time issued.

        Idempotent: once set, the snapshot is never replaced, so editing the
        live owner profile can never re-brand an already-issued invoice
        """
        if invoice.owner_snapshot_id is not None:
            return
        from app.modules.owner.service import OwnerService

        snapshot = OwnerService(self._db).snapshot_current()
        invoice.owner_snapshot_id = snapshot.id

    def mark_as_sent(
        self,
        invoice_id: uuid.UUID,
        sent_at: datetime | None = None,
    ) -> Invoice:
        """Mark invoice as sent. Transitions: DRAFT → SENT.

        Locked load (ISSUE-005): serializes with send_invoice and
        record_payment so a concurrent transition cannot race this one.
        """
        invoice = self._get_locked(invoice_id)
        self._transition(invoice, InvoiceStatus.SENT)
        invoice.sent_at = sent_at or datetime.now(UTC)
        self._capture_owner_snapshot(invoice)
        self._db.flush()
        logger.info(
            "Marked invoice as sent: %s",
            invoice.invoice_number,
            extra={"invoice_id": str(invoice.id), "sent_at": invoice.sent_at},
        )
        return invoice

    def send_invoice(
        self,
        invoice_id: uuid.UUID,
        to_email: str | None = None,
        subject: str | None = None,
        body: str | None = None,
        attach_pdf: bool = True,
    ) -> dict[str, Any]:
        """
        Send invoice via email.

        Split into a short locked phase and an unlocked dispatch.
        The locked phase takes ``SELECT ... FOR UPDATE`` to serialise the
        DRAFT->SENT transition (preventing the TOCTOU race), then *commits*
        to release the row lock. Only afterwards is the email dispatched, so
        the synchronous SES call (and its retries) never runs while the
        invoices row is locked.
        """
        recipient, _subject, _body, sent_at, outbox_id = self._prepare_and_mark_sent(
            invoice_id, to_email, subject, body, attach_pdf=attach_pdf
        )

        # Best-effort immediate delivery, *outside* the row lock. On failure
        # the queued outbox row is retried by the drainer
        # (/internal/email-outbox/drain), so the email is never lost
        # (ISSUE-003) and a slow SES call never holds a write lock.
        from app.common.email_outbox import EmailOutboxService

        delivered = EmailOutboxService(self._db).deliver_now(outbox_id)

        logger.info(
            "Sent invoice: %s",
            invoice_id,
            extra={
                "invoice_id": str(invoice_id),
                "recipient": recipient,
                "attached_pdf": attach_pdf,
                "delivered": delivered,
            },
        )
        return {
            "invoice_id": invoice_id,
            "sent_to": recipient,
            "sent_at": sent_at,
            "message": (
                "Invoice sent successfully"
                if delivered
                else "Invoice queued for delivery; the first attempt failed "
                "and will be retried automatically"
            ),
        }

    def _validate_sendable(self, invoice: Invoice) -> None:
        """Reject sends for canceled invoices (DocumentSendMixin hook)."""
        if invoice.status == InvoiceStatus.CANCELED:
            raise BadRequestException(
                detail="Cannot send a canceled invoice",
                field="status",
            )

    def record_payment(
        self,
        invoice_id: uuid.UUID,
        data: PaymentCreate,
        user_id: uuid.UUID | None = None,
    ) -> Payment:
        """
        Record a payment against an invoice.

        Updates invoice status based on balance:
        - If amount_paid >= total_due → PAID
        - If amount_paid > 0 and < total_due → PARTIAL

        Uses SELECT FOR UPDATE to prevent the TOCTOU race where two concurrent
        payments both pass the overpay check and overpay the invoice.
        Mirrors the locking pattern in send_invoice.
        """
        # Lock the bare invoices row (see send_invoice): suppress the joined
        # customer outer join so Postgres allows FOR UPDATE.
        invoice = (
            self._db.query(Invoice)
            .options(lazyload("*"))
            .filter(Invoice.id == invoice_id)
            .with_for_update()
            .first()
        )
        if not invoice:
            raise NotFoundException(
                detail=f"Invoice with ID '{invoice_id}' not found",
                resource="invoice",
            )

        # Validate invoice status
        if invoice.status == InvoiceStatus.DRAFT:
            raise BadRequestException(
                detail="Cannot record payment for draft invoice. Send it first.",
                field="status",
            )

        if invoice.status == InvoiceStatus.CANCELED:
            raise BadRequestException(
                detail="Cannot record payment for canceled invoice", field="status"
            )

        # Validate payment amount
        if data.amount > invoice.balance_due:
            raise BadRequestException(
                detail=(
                    f"Payment amount ({data.amount}) exceeds balance due ({invoice.balance_due}). "
                    f"Cannot overpay invoice."
                ),
                field="amount",
            )

        # Create payment record
        payment = Payment(
            invoice_id=invoice.id,
            amount=data.amount,
            payment_date=data.payment_date,
            payment_method=data.payment_method,
            reference=data.reference,
            notes=data.notes,
            recorded_by=user_id,
        )

        self._db.add(payment)

        # Update invoice amounts
        invoice.amount_paid += data.amount
        invoice.balance_due = invoice.total_due - invoice.amount_paid

        # Update status based on new balance, routed through the state machine
        # so ALLOWED_TRANSITIONS is enforced and the version bump is owned in
        # one place. _transition increments invoice.version.
        if invoice.balance_due <= 0:
            self._transition(invoice, InvoiceStatus.PAID)
            invoice.paid_at = datetime.now(UTC)
        elif invoice.amount_paid > 0:
            self._transition(invoice, InvoiceStatus.PARTIAL)
        else:
            invoice.version += 1

        self._db.flush()

        self._update_customer_balance(invoice.customer_id)

        # Durable audit trail (ISSUE-022): commits atomically with the payment.
        record_audit_event(
            self._db,
            actor_id=user_id,
            entity_type="invoice",
            entity_id=invoice.id,
            action="payment_recorded",
            after={
                "payment_id": str(payment.id),
                "amount": str(data.amount),
                "new_balance": str(invoice.balance_due),
                "new_status": status_value(invoice.status),
            },
        )

        logger.info(
            "Recorded payment for invoice %s",
            invoice.invoice_number,
            extra={
                "invoice_id": str(invoice.id),
                "payment_id": str(payment.id),
                "amount": float(data.amount),
                "new_balance": float(invoice.balance_due),
                "new_status": invoice.status,
            },
        )

        return payment

    def duplicate_invoice(
        self,
        invoice_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> InvoiceDuplicateResponse:
        """Duplicate an existing invoice as a new DRAFT."""
        try:
            original = self.get_by_id(invoice_id)
            from datetime import timedelta

            new_transaction_date = date.today()
            new_due_date = new_transaction_date + timedelta(days=30)

            def _build() -> InvoiceDuplicateResponse:
                duplicate = Invoice(
                    invoice_number=self._generate_invoice_number(),
                    invoice_reference=self._generate_invoice_reference(),
                    customer_id=original.customer_id,
                    transaction_date=new_transaction_date,
                    due_date=new_due_date,
                    currency=original.currency,
                    status=InvoiceStatus.DRAFT,
                    subtotal=original.subtotal,
                    discount_type=original.discount_type,
                    discount_amount=original.discount_amount,
                    discount_percentage=original.discount_percentage,
                    tax_total=original.tax_total,
                    total_due=original.total_due,
                    amount_paid=Decimal("0.00"),
                    balance_due=original.total_due,
                    rfq_number=original.rfq_number,
                    notes=original.notes,
                    created_by=user_id,
                )
                self._db.add(duplicate)
                self._db.flush()
                for orig_item in original.line_items:
                    self._db.add(
                        InvoiceLineItem(
                            invoice_id=duplicate.id,
                            line_number=orig_item.line_number,
                            item_name=orig_item.item_name,
                            description=orig_item.description,
                            quantity=orig_item.quantity,
                            unit_price=orig_item.unit_price,
                            line_total=orig_item.line_total,
                            tax_type=orig_item.tax_type,
                            tax_amount=orig_item.tax_amount,
                        )
                    )
                self._db.flush()
                logger.info(
                    "Duplicated invoice %s → %s",
                    original.invoice_number,
                    duplicate.invoice_number,
                )
                return InvoiceDuplicateResponse(
                    original_invoice_id=original.id,
                    new_invoice_id=duplicate.id,
                    new_invoice_number=duplicate.invoice_number,
                )

            return self._with_reference_retry(_build, "invoice")

        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception("Error duplicating invoice %s", invoice_id)
            raise DatabaseException("Failed to duplicate invoice") from e

    def cancel_invoice(self, invoice_id: uuid.UUID) -> Invoice:
        """
        Cancel an invoice (terminal state - irreversible).

        Can cancel from any status except CANCELED.
        """
        invoice = self.get_by_id(invoice_id)

        if invoice.status == InvoiceStatus.CANCELED:
            raise BadRequestException(
                detail="Invoice is already canceled", field="status"
            )

        previous_status = invoice.status
        # Route through the state machine so ALLOWED_TRANSITIONS is enforced
        # and the version bump is owned in one place.
        self._transition(invoice, InvoiceStatus.CANCELED)

        self._db.flush()

        # Canceling removes this invoice from the customer's outstanding
        # balance; resync in the same unit of work so it cannot drift.
        self._update_customer_balance(invoice.customer_id)

        # Durable audit trail (ISSUE-022).
        record_audit_event(
            self._db,
            actor_id=self._actor_id,
            entity_type="invoice",
            entity_id=invoice.id,
            action="canceled",
            before={"status": status_value(previous_status)},
            after={"status": status_value(InvoiceStatus.CANCELED)},
        )

        logger.warning(
            f"Canceled invoice: {invoice.invoice_number}",
            extra={"invoice_id": str(invoice.id)},
        )

        return invoice

    # CALCULATIONS & UTILITIES
    # calculate_totals is inherited from BaseDocumentService (ISSUE-012);
    # only the response class is invoice-specific.

    # Customer balance synchronisation

    def _update_customer_balance(self, customer_id: uuid.UUID) -> None:
        """
        Recalculate and persist Customer.balance from outstanding invoices.

        Called after any payment recording or status transition that changes
        the balance_due on invoices belonging to this customer.
        """
        from app.modules.customers.models import Customer

        outstanding = (
            self._db.query(
                func.coalesce(func.sum(Invoice.balance_due), Decimal("0.00"))
            )
            .filter(
                Invoice.customer_id == customer_id,
                Invoice.status.notin_([InvoiceStatus.CANCELED, InvoiceStatus.DRAFT]),
            )
            .scalar()
        ) or Decimal("0.00")

        customer = self._db.query(Customer).filter(Customer.id == customer_id).first()
        if customer:
            customer.balance = Decimal(str(outstanding))
            self._db.flush()
            logger.debug(
                "Updated customer balance to %s",
                outstanding,
                extra={"customer_id": str(customer_id)},
            )

    # NIGHTLY SCHEDULER

    def bulk_transition_overdue(self) -> int:
        """
        Bulk-update past-due, unpaid invoices to OVERDUE.

        Mirrors ExpenseService.bulk_transition_overdue. Matches SENT and
        PARTIAL invoices whose due_date has passed and that still carry an
        outstanding balance — the same predicate used by the overdue stats
        SQL (check_is_overdue semantics: a non-terminal, past-due document).
        Uses synchronize_session=False and expire_all so subsequent reads
        reflect the new status. Returns the number of rows transitioned.
        """
        try:
            today = date.today()

            updated = (
                self._db.query(Invoice)
                .filter(
                    Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL]),
                    Invoice.due_date < today,
                    Invoice.balance_due > 0,
                )
                .update(
                    {
                        Invoice.status: InvoiceStatus.OVERDUE,
                        Invoice.version: Invoice.version + 1,
                    },
                    synchronize_session=False,
                )
            )
            self._db.flush()
            self._db.expire_all()

            logger.info(
                "Bulk transitioned %d invoices to OVERDUE",
                updated,
                extra={"count": updated, "as_of": str(today)},
            )
            return updated

        except SQLAlchemyError as e:
            logger.exception("Error in bulk overdue transition")
            raise DatabaseException("Failed to transition overdue invoices") from e

    def _generate_invoice_number(self) -> str:
        """Generate a unique invoice number via ReferenceGenerator."""
        return self._ref_gen().generate(
            model=Invoice,
            column=Invoice.invoice_number,
            prefix="INV",
            lock_key="invoice_number",
            width=3,
            use_date_scope=True,
        )

    def _generate_invoice_reference(self) -> str:
        """Generate a unique invoice reference via ReferenceGenerator."""
        return self._ref_gen().generate(
            model=Invoice,
            column=Invoice.invoice_reference,
            prefix="IN",
            lock_key="invoice_reference_gen",
            width=4,
            use_date_scope=False,
        )

    # Public aliases for cross-module use (e.g., QuoteService.convert_to_invoice)
    def generate_invoice_number(self) -> str:
        """Public wrapper for invoice number generation."""
        return self._generate_invoice_number()

    def generate_invoice_reference(self) -> str:
        """Public wrapper for invoice reference generation."""
        return self._generate_invoice_reference()

    # PDF GENERATION

    def _render_pdf(self, invoice: Invoice) -> bytes:
        """Render a PDF for an already-loaded invoice.

        Orchestration (owner branding + ReportLab generator) is shared with
        quotes via DocumentPdfRenderer (ISSUE-009).
        """
        from app.common.pdf_renderer import DocumentPdfRenderer

        return DocumentPdfRenderer(self._db).render_invoice(invoice)

    def generate_pdf(self, invoice_id: uuid.UUID) -> bytes:
        """Generate PDF for an invoice by id."""
        return self._render_pdf(self.get_by_id(invoice_id))

    def generate_pdf_for_download(self, invoice_id: uuid.UUID) -> tuple[bytes, Invoice]:
        """Generate the invoice PDF and return it with the loaded invoice.

        The PDF download endpoint needs both the rendered bytes and the
        invoice (for the attachment filename). Loading the invoice once here
        and rendering from it avoids the second get_by_id the router used to
        do just for the filename.
        """
        invoice = self.get_by_id(invoice_id)
        return self._render_pdf(invoice), invoice
