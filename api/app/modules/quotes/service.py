"""Quote business logic with financial calculations and state machine."""

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload, lazyload

from app.common.audit import record_audit_event, status_value
from app.common.database import assert_version
from app.common.document_service import BaseDocumentService
from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    DatabaseException,
    NotFoundException,
)
from app.common.financial import (
    build_line_items,
    calculate_discount,
    calculate_subtotal_vat,
    neutralize_line_tax,
    sum_line_totals,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import DiscountType, QuoteStatus, TaxType
from app.modules.quotes.models import Quote, QuoteLineItem
from app.modules.quotes.queries import (
    QuoteExportQuery,
    QuoteStatisticsRepository,
    apply_quote_filters,
)
from app.modules.quotes.schemas import (
    QuoteCalculationResponse,
    QuoteCreate,
    QuoteFilterParams,
    QuoteStatusCounts,
    QuoteSummary,
    QuoteUpdate,
)

logger = logging.getLogger(__name__)


class QuoteService(BaseDocumentService):
    """
    Service layer for quote operations.

    Atomicity contract
    ------------------
    All mutating methods call self._db.flush() only. The session commit/rollback
    is owned by get_db() in database.py. Multi-step mutations use
    self._db.begin_nested() (SAVEPOINT) so inner failures roll back only
    the affected work, never the entire session.

    Race-condition contract
    -----------------------
    Sequential identifier generation uses pg_advisory_xact_lock so only one
    transaction can enter the generation critical section per unique key.
    Unique DB constraints + retry loops serve as safety nets.

    Editable-field matrix
    ---------------------------------
    The single, authoritative statement of which fields may change in which
    status. Enforced by ``QuoteResponse.is_editable`` and the SENT
    restricted-field guard in ``update()`` below; mirrors the invoices policy.

      DRAFT     fully editable (customer, dates, currency, line items,
                discount, notes).
      SENT      limited: line items / discount / notes / due_date only —
                customer_id, transaction_date and currency are frozen.
      APPROVED  read-only.
      INVOICED  read-only (converted; the invoice owns the figures).
      EXPIRED   read-only.
    """

    # Max retries for number collision (advisory lock makes this very rare)
    MAX_QUOTE_NUMBER_RETRIES = 3

    _document_noun = "quote"
    _reference_collision_markers = ("quote_number", "quote_reference")
    _email_terms: ClassVar[dict[str, str]] = {
        "noun": "quote",
        "date_label": "Valid until",
        "closing": "Thank you for considering our proposal.",
    }

    # Two-phase send wiring (DocumentSendMixin)
    _send_model = Quote
    _send_draft_status = QuoteStatus.DRAFT
    _send_sent_status = QuoteStatus.SENT

    # Shared preview-totals + reference-retry wiring
    _calculation_response_cls = QuoteCalculationResponse
    MAX_REFERENCE_RETRIES = MAX_QUOTE_NUMBER_RETRIES

    # Formal state machine — enforced via _transition()
    ALLOWED_TRANSITIONS: ClassVar[dict[QuoteStatus, list[QuoteStatus]]] = {
        QuoteStatus.DRAFT: [
            QuoteStatus.SENT,
            QuoteStatus.APPROVED,
            QuoteStatus.EXPIRED,
        ],
        # SENT must be APPROVED before it can be INVOICED — no direct
        # SENT -> INVOICED edge, so conversion can never skip approval
        QuoteStatus.SENT: [
            QuoteStatus.APPROVED,
            QuoteStatus.EXPIRED,
        ],
        QuoteStatus.APPROVED: [QuoteStatus.INVOICED, QuoteStatus.EXPIRED],
        QuoteStatus.INVOICED: [],  # terminal
        QuoteStatus.EXPIRED: [QuoteStatus.SENT],
    }

    def create(self, data: QuoteCreate, user_id: uuid.UUID | None = None) -> Quote:
        """Create a new quote with line items and automatic calculations."""
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
                detail=f"Cannot create quote for inactive customer: {customer.display_name}",
                field="customer_id",
            )

        # Single-currency-per-customer: reject a quote whose currency
        # was explicitly set to something other than the customer's; else
        # pin it to the customer's currency.
        if "currency" in data.model_fields_set and data.currency != customer.currency:
            raise BadRequestException(
                detail=(
                    f"Quote currency '{data.currency}' does not match the "
                    f"customer's currency '{customer.currency}'. A customer "
                    "can only transact in a single currency."
                ),
                field="currency",
            )
        quote_currency = customer.currency

        line_items_data = build_line_items(data.line_items)
        subtotal, tax_total = sum_line_totals(line_items_data)

        discount_value = calculate_discount(
            subtotal, data.discount_type, data.discount_amount, data.discount_percentage
        )
        total_due = subtotal - discount_value + tax_total

        def _build() -> Quote:
            quote = Quote(
                quote_number=self._generate_quote_number(),
                quote_reference=self._generate_quote_reference(),
                customer_id=data.customer_id,
                transaction_date=data.transaction_date,
                due_date=data.due_date,
                currency=quote_currency,
                status=QuoteStatus.DRAFT,
                subtotal=subtotal,
                discount_type=data.discount_type,
                discount_amount=data.discount_amount,
                discount_percentage=data.discount_percentage,
                tax_total=tax_total,
                total_due=total_due,
                rfq_rfp_number=data.rfq_rfp_number,
                notes=data.notes,
                created_by=user_id,
            )
            self._db.add(quote)
            self._db.flush()
            for item in line_items_data:
                self._db.add(QuoteLineItem(quote_id=quote.id, **item))
            self._db.flush()
            return quote

        quote = self._with_reference_retry(_build, "quote")

        logger.info(
            "Created quote: %s",
            quote.quote_number,
            extra={
                "quote_id": str(quote.id),
                "customer_id": str(data.customer_id),
                "total_due": float(total_due),
                "created_by": str(user_id) if user_id else None,
            },
        )
        return quote

    # READ

    # Single source of truth for quote filtering — module-level
    # in queries.py so QuoteExportQuery shares the identical function.
    _apply_filters = staticmethod(apply_quote_filters)

    def get_by_id(self, quote_id: uuid.UUID) -> Quote:
        """
        Retrieve quote by ID with all relationships loaded.

        Returns:
            Quote: Quote with line_items and customer
        """
        try:
            quote = (
                self._db.query(Quote)
                .options(
                    joinedload(Quote.line_items),
                    joinedload(Quote.customer),
                )
                .filter(Quote.id == quote_id)
                .first()
            )

            if not quote:
                raise NotFoundException(
                    detail=f"Quote with ID '{quote_id}' not found", resource="quote"
                )

            return quote

        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Database error retrieving quote {quote_id}")
            raise DatabaseException("Failed to retrieve quote") from e

    def get_by_number(self, quote_number: str) -> Quote:
        """
        Retrieve quote by quote number.
        """
        try:
            quote = (
                self._db.query(Quote)
                .options(
                    joinedload(Quote.line_items),
                    joinedload(Quote.customer),
                )
                .filter(Quote.quote_number == quote_number)
                .first()
            )

            if not quote:
                raise NotFoundException(
                    detail=f"Quote '{quote_number}' not found", resource="quote"
                )

            return quote

        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Database error retrieving quote {quote_number}")
            raise DatabaseException("Failed to retrieve quote") from e

    def list_quotes(
        self,
        params: PaginationParams,
        filters: QuoteFilterParams | None = None,
    ) -> PaginatedResponse[QuoteSummary]:
        """
        List quotes with pagination and filtering.
        """
        try:
            from app.modules.customers.models import Customer

            # Base query with customer join for display name
            query = self._db.query(
                Quote.id,
                Quote.quote_number,
                Quote.quote_reference,
                Quote.customer_id,
                Quote.transaction_date,
                Quote.due_date,
                Quote.status,
                Quote.currency,
                Quote.total_due,
                Quote.created_at,
                Customer.first_name,
                Customer.last_name,
                Customer.company_name,
                Customer.customer_type,
            ).join(Customer, Quote.customer_id == Customer.id)

            query = self._apply_filters(query, filters)

            # Count only when requested; over-fetch otherwise.
            total = query.count() if params.with_total else None

            results = (
                query.order_by(Quote.created_at.desc())
                .offset(params.offset)
                .limit(params.fetch_limit)
                .all()
            )

            items = []
            for row in results:
                if row.customer_type == "business" and row.company_name:
                    display_name = row.company_name
                else:
                    display_name = f"{row.first_name} {row.last_name}".strip()

                items.append(
                    QuoteSummary(
                        id=row.id,
                        quote_number=row.quote_number,
                        quote_reference=row.quote_reference,
                        customer_id=row.customer_id,
                        customer_name=display_name,
                        transaction_date=row.transaction_date,
                        due_date=row.due_date,
                        status=row.status,
                        currency=row.currency,
                        total_due=row.total_due,
                        created_at=row.created_at,
                    )
                )

            logger.debug(
                f"Listed quotes (page {params.page}, total {total})",
                extra={
                    "page": params.page,
                    "per_page": params.per_page,
                    "total": total,
                    "filters": filters.model_dump() if filters else None,
                },
            )

            return PaginatedResponse.create_from_window(
                rows=items, params=params, total=total
            )

        except SQLAlchemyError as e:
            logger.exception("Database error listing quotes")
            raise DatabaseException("Failed to list quotes") from e

    def list_for_export(
        self,
        filters: QuoteFilterParams | None = None,
        include_line_items: bool = False,
        limit: int | None = None,
    ) -> list[Quote]:
        """Return full Quote ORM rows for Excel export, batch-loaded.

        Delegates to QuoteExportQuery.
        """
        return QuoteExportQuery(self._db).list(
            filters, include_line_items=include_line_items, limit=limit
        )

    def get_status_counts(self) -> QuoteStatusCounts:
        """Get counts of quotes by status.

        Delegates to QuoteStatisticsRepository.
        """
        return QuoteStatisticsRepository(self._db).status_counts()

    # UPDATE

    def update(
        self,
        quote_id: uuid.UUID,
        data: QuoteUpdate,
        expected_version: int | None = None,
    ) -> Quote:
        """
        Update an existing quote with optimistic locking.

        Editing restrictions based on status:
        - DRAFT: Full editing allowed
        - SENT: Limited editing (no customer/amount changes)
        - APPROVED/INVOICED/EXPIRED: Read-only (no edits)
        """
        quote = self.get_by_id(quote_id)

        if not quote.is_editable:
            raise BadRequestException(
                detail=f"Cannot edit quote in {quote.status} status", field="status"
            )

        # Atomic optimistic-lock guard: locks the row and compares the version,
        # replacing the previous non-atomic Python compare that allowed silent
        # last-write-wins
        assert_version(self._db, Quote, quote_id, expected_version)

        update_data = data.model_dump(exclude_unset=True, mode="python")

        if not update_data:
            return quote  # No updates

        if quote.status == QuoteStatus.SENT:
            restricted_fields = {"customer_id", "transaction_date", "currency"}
            for field in restricted_fields:
                if field in update_data:
                    raise BadRequestException(
                        detail=f"Cannot change {field} after quote has been sent",
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
            self._db.query(QuoteLineItem).filter(
                QuoteLineItem.quote_id == quote_id
            ).delete()

            line_items_raw: list[dict] = update_data.pop("line_items")
            line_items_data = build_line_items(line_items_raw)
            subtotal, tax_total = sum_line_totals(line_items_data)

            for item in line_items_data:
                self._db.add(QuoteLineItem(quote_id=quote.id, **item))

            update_data["subtotal"] = subtotal
            update_data["tax_total"] = tax_total

        if any(
            k in update_data
            for k in (
                "subtotal",
                "discount_type",
                "discount_amount",
                "discount_percentage",
            )
        ):
            s = update_data.get("subtotal", quote.subtotal)
            t = update_data.get("tax_total", quote.tax_total)

            effective_type = update_data.get("discount_type", quote.discount_type)
            effective_amount = update_data.get("discount_amount", quote.discount_amount)
            effective_percentage = update_data.get(
                "discount_percentage", quote.discount_percentage
            )

            disc = calculate_discount(
                s, effective_type, effective_amount, effective_percentage
            )
            update_data["total_due"] = s - disc + t

        # Apply updates
        for field, value in update_data.items():
            setattr(quote, field, value)

        quote.version += 1
        self._db.flush()

        logger.info(
            f"Updated quote: {quote.quote_number}",
            extra={
                "quote_id": str(quote.id),
                "updated_fields": list(update_data.keys()),
                "new_version": quote.version,
            },
        )

        return quote

    # STATUS TRANSITIONS & ACTIONS

    def _capture_owner_snapshot(self, quote: Quote) -> None:
        """Stamp an immutable owner-header snapshot the first time issued."""
        if quote.owner_snapshot_id is not None:
            return
        from app.modules.owner.service import OwnerService

        snapshot = OwnerService(self._db).snapshot_current()
        quote.owner_snapshot_id = snapshot.id

    def mark_as_sent(
        self,
        quote_id: uuid.UUID,
        sent_at: datetime | None = None,
    ) -> Quote:
        """Mark quote as sent. Transitions: DRAFT → SENT.

        Locked load: serializes with send_quote and
        convert_to_invoice so a concurrent transition cannot race this one.
        """
        quote = self._get_locked(quote_id)
        self._transition(quote, QuoteStatus.SENT)
        quote.sent_at = sent_at or datetime.now(UTC)
        self._capture_owner_snapshot(quote)
        self._db.flush()
        logger.info(
            "Marked quote as sent: %s",
            quote.quote_number,
            extra={"quote_id": str(quote.id), "sent_at": quote.sent_at},
        )
        return quote

    def _validate_sendable(self, quote: Quote) -> None:
        """Reject sends for converted quotes (DocumentSendMixin hook)."""
        if quote.status == QuoteStatus.INVOICED:
            raise BadRequestException(
                detail="Cannot send a quote that has already been converted to an invoice",
                field="status",
            )

    def send_quote(
        self,
        quote_id: uuid.UUID,
        to_email: str | None = None,
        subject: str | None = None,
        body: str | None = None,
        attach_pdf: bool = True,
    ) -> dict[str, Any]:
        """
        Send quote via email (two-phase).

        The locked phase (shared DocumentSendMixin._prepare_and_mark_sent)
        re-reads the row FOR UPDATE, validates, transitions DRAFT -> SENT and
        commits — releasing the lock. The SES dispatch below then runs with
        no lock held, so a slow or failing email call can no longer hold a
        write lock on the quote or pin a pool connection behind network I/O.
        """
        recipient, _subject, _body, sent_at, outbox_id = self._prepare_and_mark_sent(
            quote_id, to_email, subject, body, attach_pdf=attach_pdf
        )

        # Best-effort immediate delivery, *outside* the row lock; failures
        # stay queued for the outbox drainer (mirrors send_invoice).
        from app.common.email_outbox import EmailOutboxService

        delivered = EmailOutboxService(self._db).deliver_now(outbox_id)

        logger.info(
            "Sent quote: %s",
            quote_id,
            extra={
                "quote_id": str(quote_id),
                "recipient": recipient,
                "attached_pdf": attach_pdf,
                "delivered": delivered,
            },
        )
        return {
            "quote_id": quote_id,
            "sent_to": recipient,
            "sent_at": sent_at,
            "message": (
                "Quote sent successfully"
                if delivered
                else "Quote queued for delivery; the first attempt failed "
                "and will be retried automatically"
            ),
        }

    def approve_quote(
        self,
        quote_id: uuid.UUID,
        approved_at: datetime | None = None,
        approved_by: uuid.UUID | None = None,
    ) -> Quote:
        """Approve a quote. Transitions: DRAFT/SENT → APPROVED.

        Locked load: serializes with mark_as_sent / send_quote /
        convert_to_invoice so a concurrent transition cannot race the
        eligibility checks below (race-condition contract: all status
        transitions load FOR UPDATE). ``is_expired`` reads only column
        attributes (status, due_date), so it needs nothing off the
        relationships suppressed by the bare lazyload("*") row.
        """
        quote = self._get_locked(quote_id)
        if quote.status not in [QuoteStatus.SENT, QuoteStatus.DRAFT]:
            raise BadRequestException(
                detail=f"Can only approve SENT or DRAFT quotes. Current status: {quote.status}",
                field="status",
            )
        if quote.is_expired:
            raise BadRequestException(
                detail="Cannot approve an expired quote. Update the due date first.",
                field="due_date",
            )
        self._transition(quote, QuoteStatus.APPROVED)  # enforces state machine
        quote.approved_at = approved_at or datetime.now(UTC)
        quote.approved_by = approved_by
        self._capture_owner_snapshot(quote)
        self._db.flush()
        logger.info(
            "Approved quote: %s",
            quote.quote_number,
            extra={
                "quote_id": str(quote.id),
                "approved_by": str(approved_by) if approved_by else None,
            },
        )
        return quote

    def convert_to_invoice(
        self,
        quote_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Convert an approved quote to a new invoice.

        Atomicity: all mutations run inside a single SAVEPOINT so a
        mid-operation failure cannot leave orphan invoices or a half-converted quote.

        Concurrencythe quote is re-read FOR UPDATE before the
        eligibility check, so two concurrent conversions serialize on the row
        lock and the loser observes INVOICED/related_invoice_id and is
        rejected — one APPROVED quote can never yield two invoices.

        invoice number generation is delegated to InvoiceService.
        """
        from app.constants.enums import InvoiceStatus
        from app.modules.invoices.models import Invoice, InvoiceLineItem
        from app.modules.invoices.service import InvoiceService

        # Lock the bare row; lazyload('*') keeps the FOR UPDATE off the
        # customer outer join (PostgreSQL rejects FOR UPDATE on the nullable
        # side of an outer join — same pattern as send_quote.
        quote = (
            self._db.query(Quote)
            .options(lazyload("*"))
            .filter(Quote.id == quote_id)
            .with_for_update()
            .first()
        )
        if not quote:
            raise NotFoundException(
                detail=f"Quote with ID '{quote_id}' not found", resource="quote"
            )
        if not quote.can_convert_to_invoice:
            if quote.status != QuoteStatus.APPROVED:
                raise BadRequestException(
                    detail="Only approved quotes can be converted to invoices.",
                    field="status",
                )

            if quote.is_expired:
                raise BadRequestException(
                    detail="Expired quotes cannot be converted to invoices.",
                    field="status",
                )

            if quote.related_invoice_id is not None:
                raise BadRequestException(
                    detail="This quote has already been converted to an invoice.",
                    field="status",
                )

        try:
            sp = self._db.begin_nested()
            invoice_svc = InvoiceService(self._db)
            invoice_number = invoice_svc.generate_invoice_number()
            invoice_reference = invoice_svc.generate_invoice_reference()

            invoice = Invoice(
                invoice_number=invoice_number,
                invoice_reference=invoice_reference,
                customer_id=quote.customer_id,
                transaction_date=date.today(),
                due_date=quote.due_date,
                currency=quote.currency,
                status=InvoiceStatus.DRAFT,
                subtotal=quote.subtotal,
                discount_type=quote.discount_type,
                discount_amount=quote.discount_amount,
                discount_percentage=quote.discount_percentage,
                tax_total=quote.tax_total,
                total_due=quote.total_due,
                amount_paid=Decimal("0.00"),
                balance_due=quote.total_due,
                rfq_number=quote.rfq_rfp_number,
                notes=quote.notes,
                created_by=user_id,
            )
            self._db.add(invoice)
            self._db.flush()

            for quote_item in quote.line_items:
                self._db.add(
                    InvoiceLineItem(
                        invoice_id=invoice.id,
                        line_number=quote_item.line_number,
                        item_name=quote_item.item_name,
                        description=quote_item.description,
                        quantity=quote_item.quantity,
                        unit_price=quote_item.unit_price,
                        line_total=quote_item.line_total,
                        tax_type=quote_item.tax_type,
                        tax_amount=quote_item.tax_amount,
                    )
                )
            self._db.flush()

            if quote.status == QuoteStatus.SENT:
                self._transition(quote, QuoteStatus.APPROVED)
                quote.approved_at = datetime.now(UTC)
                self._capture_owner_snapshot(quote)

            self._transition(quote, QuoteStatus.INVOICED)
            quote.invoiced_at = datetime.now(UTC)
            quote.related_invoice_id = invoice.id
            self._db.flush()
            sp.commit()  # release savepoint — all steps succeeded

            logger.info(
                "Converted quote %s → invoice %s",
                quote.quote_number,
                invoice.invoice_number,
                extra={"quote_id": str(quote.id), "invoice_id": str(invoice.id)},
            )
            return {
                "quote_id": quote.id,
                "invoice_id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "message": "Quote converted to invoice successfully",
            }

        except IntegrityError as e:
            logger.exception("Error converting quote %s to invoice", quote_id)
            raise ConflictException("Failed to create invoice from quote") from e
        except SQLAlchemyError as e:
            logger.exception("Database error converting quote %s", quote_id)
            raise DatabaseException("Failed to convert quote to invoice") from e

    def delete_quote(self, quote_id: uuid.UUID) -> None:
        """
        Delete a quote (soft restriction - only DRAFT quotes can be deleted).
        """
        quote = self.get_by_id(quote_id)

        if quote.status != QuoteStatus.DRAFT:
            raise BadRequestException(
                detail=f"Can only delete DRAFT quotes. Current status: {quote.status}",
                field="status",
            )

        try:
            # Capture the before-image while the row is still live; after the
            # DELETE flush the ORM instance is no longer authoritative.
            quote_number = quote.quote_number
            before = {
                "quote_number": quote.quote_number,
                "status": status_value(quote.status),
                "customer_id": str(quote.customer_id),
                "total_due": str(quote.total_due),
            }

            self._db.delete(quote)
            self._db.flush()

            # Durable audit trail: a hard delete must leave
            # evidence, committed atomically with the deletion itself.
            record_audit_event(
                self._db,
                actor_id=self._actor_id,
                entity_type="quote",
                entity_id=quote_id,
                action="hard_deleted",
                before=before,
            )

            logger.warning(
                f"Deleted quote: {quote_number}",
                extra={"quote_id": str(quote_id)},
            )

        except SQLAlchemyError as e:
            logger.exception(f"Error deleting quote {quote_id}")
            raise DatabaseException("Failed to delete quote") from e

    # CALCULATIONS & UTILITIES
    # calculate_totals is inherited from BaseDocumentService;
    # only the response class is quote-specific.

    def get_quote_statistics(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """Get quote statistics for dashboard.

        Delegates to QuoteStatisticsRepository.
        """
        return QuoteStatisticsRepository(self._db).statistics(date_from, date_to)

    # NIGHTLY SCHEDULER

    def bulk_transition_expired(self) -> int:
        """
        Bulk-update past-due DRAFT/SENT quotes to EXPIRED.

        Mirrors the expense/invoice overdue jobs. Matches the model
        is_expired predicate: a quote that is neither APPROVED nor INVOICED
        and whose due_date has passed. Stamps expired_at and bumps version
        once. Uses synchronize_session=False and expire_all so subsequent
        reads reflect the new status. Returns the number of rows transitioned.

        Deliberate exception to the locked-load transition contract: this
        is a single atomic UPDATE whose WHERE clause re-checks eligibility
        row-by-row, so no FOR UPDATE pre-read is needed.
        """
        try:
            now = datetime.now(UTC)
            today = now.date()

            updated = (
                self._db.query(Quote)
                .filter(
                    Quote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT]),
                    Quote.due_date < today,
                )
                .update(
                    {
                        Quote.status: QuoteStatus.EXPIRED,
                        Quote.expired_at: now,
                        Quote.version: Quote.version + 1,
                    },
                    synchronize_session=False,
                )
            )
            self._db.flush()
            self._db.expire_all()

            logger.info(
                "Bulk transitioned %d quotes to EXPIRED",
                updated,
                extra={"count": updated, "as_of": str(today)},
            )
            return updated

        except SQLAlchemyError as e:
            logger.exception("Error in bulk expired transition")
            raise DatabaseException("Failed to transition expired quotes") from e

    def _generate_quote_number(self) -> str:
        """Generate a unique quote number via ReferenceGenerator."""
        return self._ref_gen().generate(
            model=Quote,
            column=Quote.quote_number,
            prefix="QTE",
            lock_key="quote_number",
            width=3,
            use_date_scope=True,
        )

    def _generate_quote_reference(self) -> str:
        """Generate a unique quote reference via ReferenceGenerator."""
        return self._ref_gen().generate(
            model=Quote,
            column=Quote.quote_reference,
            prefix="QT",
            lock_key="quote_reference_gen",
            width=4,
            use_date_scope=False,
        )

    # PDF GENERATION

    def _render_pdf(self, quote: Quote) -> bytes:
        """Render a PDF for an already-loaded quote.

        Orchestration (owner branding + ReportLab generator) is shared with
        invoices via DocumentPdfRenderer.
        """
        from app.common.pdf_renderer import DocumentPdfRenderer

        return DocumentPdfRenderer(self._db).render_quote(quote)

    def generate_pdf(self, quote_id: uuid.UUID) -> bytes:
        """Generate PDF for a quote by id."""
        return self._render_pdf(self.get_by_id(quote_id))

    def generate_pdf_for_download(self, quote_id: uuid.UUID) -> tuple[bytes, Quote]:
        """Generate the quote PDF and return it with the loaded quote.

        Loads the quote once and renders from it, so the download endpoint
        does not re-query just for the attachment filename (mirrors
        InvoiceService.generate_pdf_for_download).
        """
        quote = self.get_by_id(quote_id)
        return self._render_pdf(quote), quote
