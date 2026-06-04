"""Quote business logic with financial calculations and state machine."""
import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, case, and_, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    DatabaseException,
    NotFoundException,
)
from app.common.financial import build_line_items, calculate_discount, calculate_line_item, sum_line_totals
from app.common.reference import ReferenceGenerator
from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import DiscountType, QuoteStatus, TaxType
from app.modules.quotes.models import Quote, QuoteLineItem
from app.modules.quotes.schemas import (
    QuoteCalculationResponse,
    QuoteCreate,
    QuoteFilterParams,
    QuoteLineItemCreate,
    QuoteStatisticsResponse,
    QuoteStatusCounts,
    QuoteSummary,
    QuoteUpdate,
)

logger = logging.getLogger(__name__)


class QuoteService:
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
    """

    # Max retries for number collision (advisory lock makes this very rare)
    MAX_QUOTE_NUMBER_RETRIES = 3

    # Formal state machine — enforced via _transition()
    ALLOWED_TRANSITIONS: dict[QuoteStatus, list[QuoteStatus]] = {
        QuoteStatus.DRAFT:    [QuoteStatus.SENT, QuoteStatus.EXPIRED],
        QuoteStatus.SENT:     [QuoteStatus.APPROVED, QuoteStatus.INVOICED, QuoteStatus.EXPIRED],
        QuoteStatus.APPROVED: [QuoteStatus.INVOICED, QuoteStatus.EXPIRED],
        QuoteStatus.INVOICED: [],          # terminal
        QuoteStatus.EXPIRED:  [QuoteStatus.SENT],
    }

    def __init__(self, db: Session, current_user=None) -> None:
        self._db = db
        self._current_user = current_user
        self._actor_id = getattr(current_user, "id", None)

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _transition(self, quote: Quote, new_status: QuoteStatus) -> None:
        """
        Enforce ALLOWED_TRANSITIONS and bump version atomically.

        Raises BadRequestException for any invalid transition so callers
        never need to know the rules themselves (DI-3 fix).
        """
        current = QuoteStatus(quote.status)
        allowed = self.ALLOWED_TRANSITIONS.get(current, [])
        if new_status not in allowed:
            raise BadRequestException(
                detail=(
                    f"Cannot transition quote from '{current}' to '{new_status}'. "
                    f"Allowed: {[s.value for s in allowed] or 'none (terminal state)'}"
                ),
                field="status",
            )
        quote.status = new_status
        quote.version += 1

    def _ref_gen(self) -> ReferenceGenerator:
        """Lazy accessor for the shared reference generator."""
        return ReferenceGenerator(self._db)


    def create(self, data: QuoteCreate, user_id: uuid.UUID | None = None) -> Quote:
        """Create a new quote with line items and automatic calculations."""
        from app.constants.enums import CustomerStatus
        from app.modules.customers.models import Customer

        customer = self._db.query(Customer).filter(Customer.id == data.customer_id).first()
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

        # Single-currency-per-customer (P-11): reject a quote whose currency
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

        last_error: Exception | None = None
        for attempt in range(self.MAX_QUOTE_NUMBER_RETRIES + 1):
            sp = self._db.begin_nested()  # SAVEPOINT — not session rollback (R-2 fix)
            try:
                quote_number = self._generate_quote_number()
                quote_reference = self._generate_quote_reference()
                quote = Quote(
                    quote_number=quote_number,
                    quote_reference=quote_reference,
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
                sp.commit()  # release savepoint
                logger.info(
                    "Created quote: %s", quote.quote_number,
                    extra={
                        "quote_id": str(quote.id),
                        "customer_id": str(data.customer_id),
                        "total_due": float(total_due),
                        "created_by": str(user_id) if user_id else None,
                    },
                )
                return quote

            except IntegrityError as e:
                sp.rollback()  # only this savepoint rolls back (R-2 fix)
                last_error = e
                is_collision = (
                    "quote_number" in str(e.orig) or "quote_reference" in str(e.orig)
                )
                if is_collision and attempt < self.MAX_QUOTE_NUMBER_RETRIES:
                    logger.warning("Quote number/reference collision, retry %d", attempt + 1)
                    continue
                raise ConflictException("Quote data violates database constraints") from e

        raise ConflictException(
            "Failed to generate unique quote number after retries"
        ) from last_error



    # READ

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
                    detail=f"Quote with ID '{quote_id}' not found",
                    resource="quote"
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
                    detail=f"Quote '{quote_number}' not found",
                    resource="quote"
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
            query = (
                self._db.query(
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
                )
                .join(Customer, Quote.customer_id == Customer.id)
            )

            # Apply filters
            if filters:
                if filters.status:
                    query = query.filter(Quote.status == filters.status)
                
                if filters.customer_id:
                    query = query.filter(Quote.customer_id == filters.customer_id)
                
                if filters.date_from:
                    query = query.filter(Quote.transaction_date >= filters.date_from)
                
                if filters.date_to:
                    query = query.filter(Quote.transaction_date <= filters.date_to)
                
                if filters.due_date_from:
                    query = query.filter(Quote.due_date >= filters.due_date_from)
                
                if filters.due_date_to:
                    query = query.filter(Quote.due_date <= filters.due_date_to)
                
                if filters.search:
                    search_term = f"%{filters.search}%"
                    query = query.filter(
                        or_(
                            Quote.quote_number.ilike(search_term),
                            Quote.quote_reference.ilike(search_term),
                            Customer.first_name.ilike(search_term),
                            Customer.last_name.ilike(search_term),
                            Customer.company_name.ilike(search_term),
                        )
                    )

            total = query.count()

            results = (
                query
                .order_by(Quote.created_at.desc())
                .offset(params.offset)
                .limit(params.per_page)
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
                f"Listed {len(items)} quotes (page {params.page}, total {total})",
                extra={
                    "page": params.page,
                    "per_page": params.per_page,
                    "total": total,
                    "filters": filters.model_dump() if filters else None,
                }
            )

            return PaginatedResponse.create(items=items, total=total, params=params)

        except SQLAlchemyError as e:
            logger.exception("Database error listing quotes")
            raise DatabaseException("Failed to list quotes") from e

    def get_status_counts(self) -> QuoteStatusCounts:
        """
        Get counts of quotes by status in a single SQL query.

        The 'expired' bucket counts DRAFT/SENT quotes whose due_date has
        passed; computed via CASE to avoid a second round-trip (S-3 fix).
        """
        try:
            today = date.today()
            rows = (
                self._db.query(
                    Quote.status,
                    func.count(Quote.id).label("cnt"),
                    func.sum(
                        case(
                            (
                                and_(
                                    Quote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT]),
                                    Quote.due_date < today,
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("expired_cnt"),
                )
                .group_by(Quote.status)
                .all()
            )

            counts_dict: dict[str, int] = {}
            expired_count = 0
            total = 0
            for status_val, cnt, exp_cnt in rows:
                counts_dict[status_val] = cnt
                total += cnt
                expired_count += exp_cnt or 0

            return QuoteStatusCounts(
                all=total,
                draft=counts_dict.get(QuoteStatus.DRAFT, 0),
                sent=counts_dict.get(QuoteStatus.SENT, 0),
                approved=counts_dict.get(QuoteStatus.APPROVED, 0),
                invoiced=counts_dict.get(QuoteStatus.INVOICED, 0),
                expired=expired_count,
            )

        except SQLAlchemyError as e:
            logger.exception("Database error getting status counts")
            raise DatabaseException("Failed to get status counts") from e


    
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
                detail=f"Cannot edit quote in {quote.status} status",
                field="status"
            )

        if expected_version is not None and quote.version != expected_version:
            raise ConflictException(
                detail=(
                    f"Quote has been modified by another user. "
                    f"Expected version {expected_version}, current version {quote.version}"
                )
            )

        update_data = data.model_dump(exclude_unset=True, mode="python")

        if not update_data:
            return quote  # No updates

        if quote.status == QuoteStatus.SENT:
            restricted_fields = {"customer_id", "transaction_date", "currency"}
            for field in restricted_fields:
                if field in update_data:
                    raise BadRequestException(
                        detail=f"Cannot change {field} after quote has been sent",
                        field=field
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

        if any(k in update_data for k in ("subtotal", "discount_type", "discount_amount", "discount_percentage")):
            s = update_data.get("subtotal", quote.subtotal)
            t = update_data.get("tax_total", quote.tax_total)
            
            effective_type       = update_data.get("discount_type", quote.discount_type)
            effective_amount     = update_data.get("discount_amount", quote.discount_amount)
            effective_percentage = update_data.get("discount_percentage", quote.discount_percentage)

            disc = calculate_discount(s, effective_type, effective_amount, effective_percentage)
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
            }
        )

        return quote

    
    # STATUS TRANSITIONS & ACTIONS
    
    def mark_as_sent(
        self,
        quote_id: uuid.UUID,
        sent_at: datetime | None = None,
    ) -> Quote:
        """Mark quote as sent. Transitions: DRAFT → SENT."""
        quote = self.get_by_id(quote_id)
        self._transition(quote, QuoteStatus.SENT)  # enforces state machine (DI-3 fix)
        quote.sent_at = sent_at or datetime.now(UTC)
        self._db.flush()
        logger.info("Marked quote as sent: %s", quote.quote_number,
                    extra={"quote_id": str(quote.id), "sent_at": quote.sent_at})
        return quote


    def send_quote(
        self,
        quote_id: uuid.UUID,
        to_email: str | None = None,
        subject: str | None = None,
        body: str | None = None,
        attach_pdf: bool = True,
    ) -> dict[str, Any]:
        """
        Send quote via email.

        TOCTOU fix (R-3): re-fetches the quote with SELECT FOR UPDATE so no
        concurrent transaction can transition the same row simultaneously.
        """
        # Row-level lock for the duration of this transaction
        quote = (
            self._db.query(Quote)
            .options(joinedload(Quote.customer))
            .filter(Quote.id == quote_id)
            .with_for_update()
            .first()
        )
        if not quote:
            raise NotFoundException(detail=f"Quote '{quote_id}' not found", resource="quote")

        if quote.status == QuoteStatus.INVOICED:
            raise BadRequestException(
                detail="Cannot send a quote that has already been converted to an invoice",
                field="status",
            )

        recipient = to_email or quote.customer.email
        if not recipient:
            raise BadRequestException(
                detail="No email address available for this customer",
                field="to_email",
            )

        email_subject = subject or self._generate_email_subject(quote)
        email_body   = body   or self._generate_email_body(quote)

        # Dispatch email
        from app.lib.email import email_service
        email_service.send_document_email(
            recipient=recipient,
            subject=email_subject,
            body_text=email_body,
        )

        sent_at = datetime.now(UTC)
        if quote.status == QuoteStatus.DRAFT:
            self._transition(quote, QuoteStatus.SENT)  # enforces state machine (DI-3 fix)
            quote.sent_at = sent_at
            self._db.flush()

        logger.info(
            "Sent quote: %s", quote.quote_number,
            extra={"quote_id": str(quote.id), "recipient": recipient, "attached_pdf": attach_pdf},
        )
        return {
            "quote_id": quote.id,
            "sent_to": recipient,
            "sent_at": sent_at,
            "message": "Quote sent successfully",
        }


    def approve_quote(
        self,
        quote_id: uuid.UUID,
        approved_at: datetime | None = None,
        approved_by: uuid.UUID | None = None,
    ) -> Quote:
        """Approve a quote. Transitions: DRAFT/SENT → APPROVED."""
        quote = self.get_by_id(quote_id)
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
        self._transition(quote, QuoteStatus.APPROVED)  # enforces state machine (DI-3 fix)
        quote.approved_at = approved_at or datetime.now(UTC)
        quote.approved_by = approved_by
        self._db.flush()
        logger.info(
            "Approved quote: %s", quote.quote_number,
            extra={"quote_id": str(quote.id), "approved_by": str(approved_by) if approved_by else None},
        )
        return quote


    def convert_to_invoice(
        self,
        quote_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Convert an approved quote to a new invoice.

        Atomicity (R-1 fix): all mutations run inside a single SAVEPOINT so a
        mid-operation failure cannot leave orphan invoices or a half-converted quote.

        SRP fix (M-2): invoice number generation is delegated to InvoiceService.
        """
        from app.constants.enums import InvoiceStatus
        from app.modules.invoices.models import Invoice, InvoiceLineItem
        from app.modules.invoices.service import InvoiceService

        quote = self.get_by_id(quote_id)
        if not quote.can_convert_to_invoice:
            raise BadRequestException(
                detail=(
                    f"Quote cannot be converted. Status: {quote.status}, "
                    f"Expired: {quote.is_expired}, "
                    f"Already converted: {quote.related_invoice_id is not None}"
                ),
                field="status",
            )

        try:
            sp = self._db.begin_nested()  
            invoice_svc = InvoiceService(self._db)
            invoice_number    = invoice_svc.generate_invoice_number()
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
                self._db.add(InvoiceLineItem(
                    invoice_id=invoice.id,
                    line_number=quote_item.line_number,
                    item_name=quote_item.item_name,       # preserved — DI-1 fix
                    description=quote_item.description,
                    quantity=quote_item.quantity,
                    unit_price=quote_item.unit_price,
                    line_total=quote_item.line_total,
                    tax_type=quote_item.tax_type,
                    tax_amount=quote_item.tax_amount,
                ))
            self._db.flush()

            self._transition(quote, QuoteStatus.INVOICED)  # state machine (DI-3 fix)
            quote.invoiced_at       = datetime.now(UTC)
            quote.related_invoice_id = invoice.id
            self._db.flush()
            sp.commit()  # release savepoint — all steps succeeded

            logger.info(
                "Converted quote %s → invoice %s",
                quote.quote_number, invoice.invoice_number,
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


    def duplicate_quote(
        self,
        quote_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> Quote:
        """Duplicate an existing quote as a new DRAFT."""
        try:
            original = self.get_by_id(quote_id)
            from datetime import timedelta
            new_transaction_date = date.today()
            new_due_date = new_transaction_date + timedelta(days=30)
            last_error: Exception | None = None

            for attempt in range(self.MAX_QUOTE_NUMBER_RETRIES + 1):
                sp = self._db.begin_nested()  # SAVEPOINT (R-2 fix)
                try:
                    duplicate = Quote(
                        quote_number=self._generate_quote_number(),
                        quote_reference=self._generate_quote_reference(),
                        customer_id=original.customer_id,
                        transaction_date=new_transaction_date,
                        due_date=new_due_date,
                        currency=original.currency,
                        status=QuoteStatus.DRAFT,
                        subtotal=original.subtotal,
                        discount_type=original.discount_type,
                        discount_amount=original.discount_amount,
                        discount_percentage=original.discount_percentage,
                        tax_total=original.tax_total,
                        total_due=original.total_due,
                        rfq_rfp_number=original.rfq_rfp_number,
                        notes=original.notes,
                        created_by=user_id,
                    )
                    self._db.add(duplicate)
                    self._db.flush()
                    for orig_item in original.line_items:
                        self._db.add(QuoteLineItem(
                            quote_id=duplicate.id,
                            line_number=orig_item.line_number,
                            item_name=orig_item.item_name,
                            description=orig_item.description,
                            quantity=orig_item.quantity,
                            unit_price=orig_item.unit_price,
                            line_total=orig_item.line_total,
                            tax_type=orig_item.tax_type,
                            tax_amount=orig_item.tax_amount,
                        ))
                    self._db.flush()
                    sp.commit()  # release savepoint
                    logger.info(
                        "Duplicated quote %s → %s",
                        original.quote_number, duplicate.quote_number,
                        extra={"original_id": str(original.id), "duplicate_id": str(duplicate.id)},
                    )
                    return duplicate

                except IntegrityError as exc:
                    sp.rollback()  # only this savepoint (R-2 fix)
                    last_error = exc
                    is_collision = (
                        "quote_number" in str(exc.orig) or "quote_reference" in str(exc.orig)
                    )
                    if is_collision and attempt < self.MAX_QUOTE_NUMBER_RETRIES:
                        logger.warning("Collision during duplicate, retry %d", attempt + 1)
                        continue
                    raise ConflictException("Quote data violates database constraints") from exc

            raise ConflictException(
                "Failed to generate unique quote number after retries"
            ) from last_error

        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception("Error duplicating quote %s", quote_id)
            raise DatabaseException("Failed to duplicate quote") from e


    def delete_quote(self, quote_id: uuid.UUID) -> None:
        """
        Delete a quote (soft restriction - only DRAFT quotes can be deleted).
        """
        quote = self.get_by_id(quote_id)

        if quote.status != QuoteStatus.DRAFT:
            raise BadRequestException(
                detail=f"Can only delete DRAFT quotes. Current status: {quote.status}",
                field="status"
            )

        try:
            self._db.delete(quote)
            self._db.flush()

            logger.warning(
                f"Deleted quote: {quote.quote_number}",
                extra={"quote_id": str(quote.id)}
            )

        except SQLAlchemyError as e:
            logger.exception(f"Error deleting quote {quote_id}")
            raise DatabaseException("Failed to delete quote") from e

    
    # CALCULATIONS & UTILITIES
    

    @classmethod
    def calculate_totals(
        cls,
        line_items: list[QuoteLineItemCreate],
        discount_type: DiscountType | None = None,
        discount_amount: Decimal | None = None,
        discount_percentage: Decimal | None = None,
    ) -> QuoteCalculationResponse:
        """Calculate quote totals without saving (preview)."""
        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")
        calculated_items = []

        built_items = build_line_items(line_items)
        subtotal, tax_total = sum_line_totals(built_items)
        for item in built_items:
            calculated_items.append({
                "item_name":   item["item_name"],
                "description": item["description"],
                "quantity":    float(item["quantity"]),
                "unit_price":  float(item["unit_price"]),
                "line_total":  float(item["line_total"]),
                "tax_type":    item["tax_type"],
                "tax_amount":  float(item["tax_amount"]),
            })

        discount_value = calculate_discount(subtotal, discount_type, discount_amount, discount_percentage)
        total_due = subtotal - discount_value + tax_total

        return QuoteCalculationResponse(
            subtotal=subtotal,
            discount_value=discount_value,
            tax_total=tax_total,
            total_due=total_due,
            line_items=calculated_items,
        )


    def get_quote_statistics(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """
        Get quote statistics for dashboard using SQL aggregates.

        S-1 fix: all aggregations are pushed to the database — no full table
        scan into Python memory.  Defaults to the current calendar month.
        """
        try:
            from datetime import timedelta

            if not date_from and not date_to:
                today = date.today()
                date_from = date(today.year, today.month, 1)
                date_to = (
                    date(today.year, 12, 31)
                    if today.month == 12
                    else date(today.year, today.month + 1, 1) - timedelta(days=1)
                )

            today = date.today()

            # Single aggregate query — no Python-side loops over rows
            agg = (
                self._db.query(
                    func.count(Quote.id).label("total_quotes"),
                    func.coalesce(func.sum(Quote.total_due), Decimal("0")).label("total_quoted"),
                    func.coalesce(
                        func.sum(
                            case((Quote.status == QuoteStatus.APPROVED, Quote.total_due), else_=0)
                        ), Decimal("0")
                    ).label("total_approved"),
                    func.coalesce(
                        func.sum(
                            case((Quote.status == QuoteStatus.INVOICED, Quote.total_due), else_=0)
                        ), Decimal("0")
                    ).label("total_invoiced"),
                    func.count(
                        case((Quote.status.in_([QuoteStatus.APPROVED, QuoteStatus.INVOICED]), Quote.id))
                    ).label("converted_count"),
                    func.coalesce(
                        func.avg(
                            case(
                                (
                                    and_(
                                        Quote.approved_at.isnot(None),
                                        Quote.status.in_([QuoteStatus.APPROVED, QuoteStatus.INVOICED]),
                                    ),
                                    func.extract(
                                        "epoch",
                                        Quote.approved_at - func.cast(Quote.transaction_date, type_=Quote.approved_at.type),
                                    ) / 86400,
                                )
                            )
                        ),
                        0,
                    ).label("avg_days_to_approval"),
                    func.count(
                        case(
                            (
                                and_(
                                    Quote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT]),
                                    Quote.due_date < today,
                                ),
                                Quote.id,
                            )
                        )
                    ).label("expired_count"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    and_(
                                        Quote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT]),
                                        Quote.due_date < today,
                                    ),
                                    Quote.total_due,
                                ),
                                else_=0,
                            )
                        ),
                        Decimal("0"),
                    ).label("expired_amount"),
                )
                .filter(
                    Quote.transaction_date >= date_from if date_from else True,
                    Quote.transaction_date <= date_to   if date_to   else True,
                )
                .one()
            )

            total = agg.total_quotes or 0
            conversion_rate = (
                round(agg.converted_count / total * 100, 1) if total > 0 else 0.0
            )
            avg_value = (
                (Decimal(str(agg.total_quoted)) / total) if total > 0 else Decimal("0.00")
            )

            logger.debug(
                "Calculated quote statistics",
                extra={"date_from": str(date_from), "date_to": str(date_to), "total": total},
            )

            return {
                "total_quotes":             total,
                "total_quoted":             Decimal(str(agg.total_quoted)),
                "total_approved":           Decimal(str(agg.total_approved)),
                "total_invoiced":           Decimal(str(agg.total_invoiced)),
                "conversion_rate":          conversion_rate,
                "average_quote_value":      avg_value,
                "average_days_to_approval": round(float(agg.avg_days_to_approval or 0), 1),
                "expired_count":            agg.expired_count or 0,
                "expired_amount":           Decimal(str(agg.expired_amount)),
                "date_from":                date_from,
                "date_to":                  date_to,
            }

        except SQLAlchemyError as e:
            logger.exception("Database error calculating quote statistics")
            raise DatabaseException("Failed to calculate statistics") from e

    # NIGHTLY SCHEDULER

    def bulk_transition_expired(self) -> int:
        """
        Bulk-update past-due DRAFT/SENT quotes to EXPIRED.

        Mirrors the expense/invoice overdue jobs. Matches the model
        is_expired predicate: a quote that is neither APPROVED nor INVOICED
        and whose due_date has passed. Stamps expired_at and bumps version
        once. Uses synchronize_session=False and expire_all so subsequent
        reads reflect the new status. Returns the number of rows transitioned.
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

    def _generate_email_subject(self, quote: Quote) -> str:
        """Generate email subject for a quote."""
        from app.lib.config import settings
        return f"Quote {quote.quote_reference} from {settings.APP_NAME}"

    def _generate_email_body(self, quote: Quote) -> str:
        """Generate plain-text email body for a quote."""
        from app.lib.config import settings
        return f"""\
Dear {quote.customer.display_name},

Please find attached quote {quote.quote_reference} for {quote.currency} {quote.total_due}.

Valid until: {quote.due_date.strftime('%d %B %Y')}

Thank you for considering our proposal.

Best regards,
{settings.APP_NAME}
"""

    # PDF GENERATION

    def generate_pdf(self, quote_id: uuid.UUID) -> bytes:
        """Generate PDF for quote using ReportLab."""
        from app.common.pdf import DocumentPDFGenerator

        quote = self.get_by_id(quote_id)
        generator = DocumentPDFGenerator()
        return generator.generate_quote_pdf(quote)
