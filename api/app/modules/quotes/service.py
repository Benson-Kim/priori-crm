"""Quote business logic with financial calculations and state machine."""
import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    DatabaseException,
    NotFoundException,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import DiscountType, QuoteStatus, TaxType
from app.modules.quotes.models import Quote, QuoteLineItem
from app.modules.quotes.schemas import (
    QuoteCalculationResponse,
    QuoteCreate,
    QuoteFilterParams,
    QuoteLineItemCreate,
    QuoteResponse,
    QuoteStatisticsResponse,
    QuoteStatusCounts,
    QuoteSummary,
    QuoteUpdate,
)

logger = logging.getLogger(__name__)


class QuoteService:
    """
    Service layer for quote operations with financial calculations.
    """

    # Tax rate mappings (should ideally come from database config)
    TAX_RATES = {
        TaxType.VAT_16: Decimal("0.16"),
        TaxType.VAT_0: Decimal("0.00"),
        TaxType.NO_TAX: Decimal("0.00"),
    }

    # Max retries for quote number collision
    MAX_QUOTE_NUMBER_RETRIES = 3

    # Status transition rules (state machine)
    ALLOWED_TRANSITIONS = {
        QuoteStatus.DRAFT: [QuoteStatus.SENT, QuoteStatus.EXPIRED],
        QuoteStatus.SENT: [QuoteStatus.APPROVED, QuoteStatus.INVOICED, QuoteStatus.EXPIRED],
        QuoteStatus.APPROVED: [QuoteStatus.INVOICED, QuoteStatus.EXPIRED],
        QuoteStatus.INVOICED: [],  # Terminal state - already converted
        QuoteStatus.EXPIRED: [QuoteStatus.SENT],  # Can re-send expired quotes
    }

    @staticmethod
    def _calculate_discount(
        subtotal: Decimal,
        discount_type: DiscountType | None,
        discount_amount: Decimal | None,
        discount_percentage: Decimal | None,
    ) -> Decimal:
        """Calculate discount value. Caps fixed discount at subtotal to prevent negative totals."""
        if discount_type == DiscountType.AMOUNT and discount_amount:
            return min(discount_amount, subtotal)
        elif discount_type == DiscountType.PERCENTAGE and discount_percentage:
            return subtotal * (discount_percentage / Decimal("100"))
        return Decimal("0.00")

    def __init__(self, db: Session) -> None:
        """
        Initialize service with database session.
        """
        self._db = db


    def create(self, data: QuoteCreate, user_id: uuid.UUID | None = None) -> Quote:
        """
        Create a new quote with line items and calculations.
        """
        # Validate customer exists and is active
        from app.modules.customers.models import Customer
        from app.constants.enums import CustomerStatus

        customer = self._db.query(Customer).filter(Customer.id == data.customer_id).first()

        if not customer:
            raise NotFoundException(
                detail=f"Customer with ID '{data.customer_id}' not found",
                resource="customer"
            )

        if customer.status != CustomerStatus.ACTIVE:
            raise BadRequestException(
                detail=f"Cannot create quote for inactive customer: {customer.display_name}",
                field="customer_id"
            )

        # Calculate line item totals
        line_items_data = []
        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")

        for idx, item_data in enumerate(data.line_items, start=1):
            line_total = item_data.quantity * item_data.unit_price
            tax_rate = self.TAX_RATES.get(item_data.tax_type, Decimal("0.00"))
            tax_amount = line_total * tax_rate

            line_items_data.append({
                "line_number": idx,
                "item_name": item_data.item_name,
                "description": item_data.description,
                "quantity": item_data.quantity,
                "unit_price": item_data.unit_price,
                "line_total": line_total,
                "tax_type": item_data.tax_type,
                "tax_amount": tax_amount,
            })

            subtotal += line_total
            tax_total += tax_amount

        # Calculate discount and total
        discount_value = self._calculate_discount(
            subtotal, data.discount_type, data.discount_amount, data.discount_percentage
        )
        total_due = subtotal - discount_value + tax_total

        # Bounded retry loop for quote number collisions
        last_error: Exception | None = None
        for attempt in range(self.MAX_QUOTE_NUMBER_RETRIES + 1):
            try:
                quote_number = self._generate_quote_number()
                quote_reference = self._generate_quote_reference()

                quote = Quote(
                    quote_number=quote_number,
                    quote_reference=quote_reference,
                    customer_id=data.customer_id,
                    transaction_date=data.transaction_date,
                    due_date=data.due_date,
                    currency=data.currency,
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

                # Create line items
                for item_data in line_items_data:
                    line_item = QuoteLineItem(
                        quote_id=quote.id,
                        **item_data
                    )
                    self._db.add(line_item)

                self._db.flush()

                logger.info(
                    f"Created quote: {quote.quote_number}",
                    extra={
                        "quote_id": str(quote.id),
                        "quote_number": quote.quote_number,
                        "customer_id": str(data.customer_id),
                        "total_due": float(total_due),
                        "created_by": str(user_id) if user_id else None,
                    }
                )

                return quote

            except IntegrityError as e:
                last_error = e
                if "quote_number" in str(e.orig) and attempt < self.MAX_QUOTE_NUMBER_RETRIES:
                    logger.warning(f"Quote number collision, retry {attempt + 1}")
                    self._db.rollback()
                    continue
                raise ConflictException("Quote data violates database constraints") from e

        raise ConflictException("Failed to generate unique quote number after retries") from last_error


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
        Get counts of quotes grouped by status.
        Also calculates expired count based on due_date.
        """
        try:
            # Get counts by status
            results = (
                self._db.query(
                    Quote.status,
                    func.count(Quote.id).label("count"),
                )
                .group_by(Quote.status)
                .all()
            )

            counts_dict = {status: count for status, count in results}
            total = sum(counts_dict.values())

            # Calculate expired count (quotes past due date, not invoiced/approved)
            expired_count = (
                self._db.query(Quote)
                .filter(
                    Quote.status.in_([QuoteStatus.DRAFT, QuoteStatus.SENT]),
                    Quote.due_date < date.today(),
                )
                .count()
            )

            counts = QuoteStatusCounts(
                all=total,
                draft=counts_dict.get(QuoteStatus.DRAFT, 0),
                sent=counts_dict.get(QuoteStatus.SENT, 0),
                approved=counts_dict.get(QuoteStatus.APPROVED, 0),
                invoiced=counts_dict.get(QuoteStatus.INVOICED, 0),
                expired=expired_count,
            )

            logger.debug(f"Quote status counts: {counts}")

            return counts

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

        # Handle line items update (replace all)
        if "line_items" in update_data:
            self._db.query(QuoteLineItem).filter(
                QuoteLineItem.quote_id == quote_id
            ).delete()

            line_items_raw: list[dict] = update_data.pop("line_items")
            subtotal = Decimal("0.00")
            tax_total = Decimal("0.00")

            for idx, item in enumerate(line_items_raw, start=1):
                # Validate required fields
                if not all(k in item for k in ("item_name", "quantity", "unit_price", "description", "tax_type")):
                    raise BadRequestException(
                        detail="Line item missing required fields",
                        field="line_items"
                    )

                quantity = Decimal(str(item["quantity"]))
                unit_price = Decimal(str(item["unit_price"]))
                line_total = quantity * unit_price
                tax_rate = self.TAX_RATES.get(item["tax_type"], Decimal("0.00"))
                tax_amount = line_total * tax_rate

                line_item = QuoteLineItem(
                    quote_id=quote.id,
                    line_number=idx,
                    item_name=item["item_name"],
                    description=item["description"],
                    quantity=quantity,
                    unit_price=unit_price,
                    line_total=line_total,
                    tax_type=item["tax_type"],
                    tax_amount=tax_amount,
                )
                self._db.add(line_item)

                subtotal += line_total
                tax_total += tax_amount

            update_data["subtotal"] = subtotal
            update_data["tax_total"] = tax_total

        # Recalculate totals if financial fields changed
        if any(k in update_data for k in ["subtotal", "discount_type", "discount_amount", "discount_percentage"]):
            subtotal = update_data.get("subtotal", quote.subtotal)
            tax_total = update_data.get("tax_total", quote.tax_total)

            discount_value = self._calculate_discount(
                subtotal,
                update_data.get("discount_type", quote.discount_type),
                update_data.get("discount_amount", quote.discount_amount),
                update_data.get("discount_percentage", quote.discount_percentage),
            )

            total_due = subtotal - discount_value + tax_total
            update_data["total_due"] = total_due

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
        """
        Mark quote as sent (without actually sending email).
        Transitions: DRAFT → SENT
        """
        quote = self.get_by_id(quote_id)

        if quote.status != QuoteStatus.DRAFT:
            raise BadRequestException(
                detail=f"Can only mark DRAFT quotes as sent. Current status: {quote.status}",
                field="status"
            )

        quote.status = QuoteStatus.SENT
        quote.sent_at = sent_at or datetime.now(UTC)
        quote.version += 1

        self._db.flush()

        logger.info(
            f"Marked quote as sent: {quote.quote_number}",
            extra={"quote_id": str(quote.id), "sent_at": quote.sent_at}
        )

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
        Send quote via email (integrates with email service).

        Also marks quote as SENT if currently DRAFT.
        """
        quote = self.get_by_id(quote_id)

        if quote.status == QuoteStatus.INVOICED:
            raise BadRequestException(
                detail="Cannot send quote that has been converted to invoice",
                field="status"
            )

        # Determine recipient
        recipient = to_email or quote.customer.email
        if not recipient:
            raise BadRequestException(
                detail="No email address available for customer",
                field="to_email"
            )

        # Generate email content
        email_subject = subject or self._generate_email_subject(quote)
        email_body = body or self._generate_email_body(quote)

        # TODO: Generate PDF if attach_pdf is True
        # TODO: Send email via email service
            # from app.lib.email import email_service
            # email_service.send_quote_email(
            #     recipient=recipient,
            #     subject=email_subject,
            #     body=email_body,
            #     pdf_attachment=pdf_data,
            # )

        # Mark as sent if currently draft
        if quote.status == QuoteStatus.DRAFT:
            quote.status = QuoteStatus.SENT
            quote.sent_at = datetime.now(UTC)
            quote.version += 1
            self._db.flush()

        logger.info(
            f"Sent quote: {quote.quote_number}",
            extra={
                "quote_id": str(quote.id),
                "recipient": recipient,
                "attached_pdf": attach_pdf,
            }
        )

        return {
            "quote_id": quote.id,
            "sent_to": recipient,
            "sent_at": datetime.now(UTC),
            "message": "Quote sent successfully",
        }

    def approve_quote(
        self,
        quote_id: uuid.UUID,
        approved_at: datetime | None = None,
        approved_by: uuid.UUID | None = None,
    ) -> Quote:
        """
        Approve a quote.
        Transitions: SENT → APPROVED / DRAFT → APPROVED
        """
        quote = self.get_by_id(quote_id)

        if quote.status not in [QuoteStatus.SENT, QuoteStatus.DRAFT]:
            raise BadRequestException(
                detail=f"Can only approve SENT or DRAFT quotes. Current status: {quote.status}",
                field="status"
            )

        if quote.is_expired:
            raise BadRequestException(
                detail="Cannot approve expired quote. Please update the due date first.",
                field="due_date"
            )

        quote.status = QuoteStatus.APPROVED
        quote.approved_at = approved_at or datetime.now(UTC)
        quote.approved_by = approved_by
        quote.version += 1

        self._db.flush()

        logger.info(
            f"Approved quote: {quote.quote_number}",
            extra={
                "quote_id": str(quote.id),
                "approved_at": quote.approved_at,
                "approved_by": str(approved_by) if approved_by else None,
            }
        )

        return quote

    def convert_to_invoice(
        self,
        quote_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Convert an approved quote to an invoice.
        
        Creates a new invoice with all quote data and marks quote as INVOICED.
        """
        from app.modules.invoices.models import Invoice, InvoiceLineItem
        from app.constants.enums import InvoiceStatus

        quote = self.get_by_id(quote_id)

        if not quote.can_convert_to_invoice:
            raise BadRequestException(
                detail=(
                    f"Quote cannot be converted. Status: {quote.status}, "
                    f"Expired: {quote.is_expired}, Already converted: {quote.related_invoice_id is not None}"
                ),
                field="status"
            )

        try:
            # Generate invoice numbers
            invoice_number = self._generate_invoice_number()
            invoice_reference = self._generate_invoice_reference()

            # Create invoice
            invoice = Invoice(
                invoice_number=invoice_number,
                invoice_reference=invoice_reference,
                customer_id=quote.customer_id,
                transaction_date=date.today(),  # Invoice date is today
                due_date=quote.due_date,  # Keep same due date
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
                rfq_number=quote.rfq_rfp_number,  # Map RFQ/RFP to invoice RFQ
                notes=quote.notes,
                created_by=user_id,
            )

            self._db.add(invoice)
            self._db.flush()

            # Copy line items
            for quote_item in quote.line_items:
                invoice_item = InvoiceLineItem(
                    invoice_id=invoice.id,
                    line_number=quote_item.line_number,
                    description=f"{quote_item.item_name}\n{quote_item.description}",
                    quantity=quote_item.quantity,
                    unit_price=quote_item.unit_price,
                    line_total=quote_item.line_total,
                    tax_type=quote_item.tax_type,
                    tax_amount=quote_item.tax_amount,
                )
                self._db.add(invoice_item)

            self._db.flush()

            # Update quote status
            quote.status = QuoteStatus.INVOICED
            quote.invoiced_at = datetime.now(UTC)
            quote.related_invoice_id = invoice.id
            quote.version += 1

            self._db.flush()

            logger.info(
                f"Converted quote {quote.quote_number} to invoice {invoice.invoice_number}",
                extra={
                    "quote_id": str(quote.id),
                    "invoice_id": str(invoice.id),
                    "invoice_number": invoice.invoice_number,
                }
            )

            return {
                "quote_id": quote.id,
                "invoice_id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "message": "Quote converted to invoice successfully",
            }

        except IntegrityError as e:
            logger.exception(f"Error converting quote {quote_id} to invoice")
            raise ConflictException("Failed to create invoice from quote") from e
        except SQLAlchemyError as e:
            logger.exception(f"Database error converting quote {quote_id}")
            raise DatabaseException("Failed to convert quote to invoice") from e

    def duplicate_quote(
        self,
        quote_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> Quote:
        """
        Duplicate an existing quote as a new DRAFT.
        """
        try:
            original = self.get_by_id(quote_id)

            from datetime import timedelta
            new_transaction_date = date.today()
            new_due_date = new_transaction_date + timedelta(days=30)

            last_error: Exception | None = None

            for attempt in range(self.MAX_QUOTE_NUMBER_RETRIES + 1):
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

                    # Duplicate line items
                    for original_item in original.line_items:
                        duplicate_item = QuoteLineItem(
                            quote_id=duplicate.id,
                            line_number=original_item.line_number,
                            item_name=original_item.item_name,
                            description=original_item.description,
                            quantity=original_item.quantity,
                            unit_price=original_item.unit_price,
                            line_total=original_item.line_total,
                            tax_type=original_item.tax_type,
                            tax_amount=original_item.tax_amount,
                        )
                        self._db.add(duplicate_item)

                    self._db.flush()

                    logger.info(
                        f"Duplicated quote {original.quote_number} → {duplicate.quote_number}",
                        extra={
                            "original_id": str(original.id),
                            "duplicate_id": str(duplicate.id),
                        }
                    )

                    return duplicate

                except IntegrityError as exc:
                    last_error = exc
                    if (
                        "quote_number" in str(exc.orig)
                        and attempt < self.MAX_QUOTE_NUMBER_RETRIES
                    ):
                        logger.warning(
                            "Quote number collision during duplicate, retry %d", attempt + 1
                        )
                        self._db.rollback()
                        continue
                    raise ConflictException(
                        "Quote data violates database constraints"
                    ) from exc

            raise ConflictException(
                "Failed to generate unique quote number after retries"
            ) from last_error

        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception(f"Error duplicating quote {quote_id}")
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
        """
        Calculate quote totals without saving (preview).
        """
        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")
        calculated_items = []

        for item in line_items:
            line_total = item.quantity * item.unit_price
            tax_rate = cls.TAX_RATES.get(item.tax_type, Decimal("0.00"))
            tax_amount = line_total * tax_rate

            subtotal += line_total
            tax_total += tax_amount

            calculated_items.append({
                "item_name": item.item_name,
                "description": item.description,
                "quantity": float(item.quantity),
                "unit_price": float(item.unit_price),
                "line_total": float(line_total),
                "tax_type": item.tax_type,
                "tax_amount": float(tax_amount),
            })

        # Calculate discount using shared utility
        discount_value = cls._calculate_discount(
            subtotal, discount_type, discount_amount, discount_percentage
        )

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
        Get comprehensive quote statistics for dashboard.
        
        Defaults to current month if no date range provided.
        """
        try:
            from datetime import timedelta

            # Default to current month if no dates provided
            if not date_from and not date_to:
                today = date.today()
                date_from = date(today.year, today.month, 1)

                if today.month == 12:
                    date_to = date(today.year, 12, 31)
                else:
                    date_to = date(today.year, today.month + 1, 1) - timedelta(days=1)

            query = self._db.query(Quote)
            
            if date_from:
                query = query.filter(Quote.transaction_date >= date_from)
            if date_to:
                query = query.filter(Quote.transaction_date <= date_to)

            # Get all quotes in range
            quotes = query.all()

            # Calculate basic metrics
            total_quotes = len(quotes)
            
            if total_quotes == 0:
                return {
                    "total_quotes": 0,
                    "total_quoted": Decimal("0.00"),
                    "total_approved": Decimal("0.00"),
                    "total_invoiced": Decimal("0.00"),
                    "conversion_rate": 0.0,
                    "average_quote_value": Decimal("0.00"),
                    "average_days_to_approval": 0.0,
                    "expired_count": 0,
                    "expired_amount": Decimal("0.00"),
                    "date_from": date_from,
                    "date_to": date_to,
                }

            total_quoted = sum(q.total_due for q in quotes)
            
            approved_quotes = [q for q in quotes if q.status == QuoteStatus.APPROVED]
            total_approved = sum(q.total_due for q in approved_quotes)
            
            invoiced_quotes = [q for q in quotes if q.status == QuoteStatus.INVOICED]
            total_invoiced = sum(q.total_due for q in invoiced_quotes)

            # Conversion rate: (approved + invoiced) / total quotes
            conversion_rate = (len(approved_quotes) + len(invoiced_quotes)) / total_quotes * 100 if total_quotes > 0 else 0.0

            average_quote_value = total_quoted / total_quotes if total_quotes > 0 else Decimal("0.00")

            # Calculate average days to approval
            approved_with_dates = [
                q for q in approved_quotes + invoiced_quotes
                if q.approved_at
            ]
            
            if approved_with_dates:
                total_days = sum(
                    (q.approved_at.date() - q.transaction_date).days
                    for q in approved_with_dates
                )
                average_days_to_approval = total_days / len(approved_with_dates)
            else:
                average_days_to_approval = 0.0

            # Calculate expired metrics
            today = date.today()
            expired_quotes = [
                q for q in quotes
                if q.status in [QuoteStatus.DRAFT, QuoteStatus.SENT]
                and q.due_date < today
            ]
            
            expired_count = len(expired_quotes)
            expired_amount = sum(q.total_due for q in expired_quotes)

            logger.debug(
                "Calculated quote statistics",
                extra={
                    "date_from": str(date_from),
                    "date_to": str(date_to),
                    "total_quotes": total_quotes,
                    "total_quoted": float(total_quoted),
                }
            )

            return {
                "total_quotes": total_quotes,
                "total_quoted": total_quoted,
                "total_approved": total_approved,
                "total_invoiced": total_invoiced,
                "conversion_rate": round(conversion_rate, 1),
                "average_quote_value": average_quote_value,
                "average_days_to_approval": round(average_days_to_approval, 1),
                "expired_count": expired_count,
                "expired_amount": expired_amount,
                "date_from": date_from,
                "date_to": date_to,
            }

        except SQLAlchemyError as e:
            logger.exception("Database error calculating quote statistics")
            raise DatabaseException("Failed to calculate statistics") from e

    def _generate_quote_number(self) -> str:
        """
        Generate unique quote number.
        Format: QTE-YYYYMMDD-NNN
        Example: QTE-20260315-001
        """
        today = date.today()
        prefix = f"QTE-{today.strftime('%Y%m%d')}"

        count = (
            self._db.query(func.count(Quote.id))
            .filter(Quote.quote_number.like(f"{prefix}%"))
            .scalar()
        )

        return f"{prefix}-{count + 1:03d}"

    def _generate_quote_reference(self) -> str:
        """
        Generate user-facing quote reference.
        Format: QT-NNNN
        Example: QT-0101
        """
        count = (
            self._db.query(func.count(Quote.id))
            .scalar()
        )
        return f"QT-{count + 1:04d}"

    def _generate_invoice_number(self) -> str:
        """
        Generate invoice number (for conversion).
        Format: INV-YYYYMMDD-NNN
        """
        from app.modules.invoices.models import Invoice
        
        today = date.today()
        prefix = f"INV-{today.strftime('%Y%m%d')}"

        count = (
            self._db.query(func.count(Invoice.id))
            .filter(Invoice.invoice_number.like(f"{prefix}%"))
            .scalar()
        )

        return f"{prefix}-{count + 1:03d}"

    def _generate_invoice_reference(self) -> str:
        """
        Generate invoice reference (for conversion).
        Format: IN-NNNN
        """
        from app.modules.invoices.models import Invoice
        
        count = (
            self._db.query(func.count(Invoice.id))
            .scalar()
        )
        return f"IN-{count + 1:04d}"

    def _generate_email_subject(self, quote: Quote) -> str:
        """Generate email subject for quote."""
        from app.lib.config import settings
        return f"Quote {quote.quote_reference} from {settings.APP_NAME}"

    def _generate_email_body(self, quote: Quote) -> str:
        """Generate email body template for quote."""
        from app.lib.config import settings
        return f"""
        Dear {quote.customer.display_name},

        Please find attached quote {quote.quote_reference} for {quote.currency} {quote.total_due}.

        Valid until: {quote.due_date.strftime('%d %B %Y')}

        Thank you for considering our proposal.

        Best regards,
        {settings.APP_NAME}
        """

    # PDF GENERATION (Placeholder)

    def generate_pdf(self, quote_id: uuid.UUID) -> bytes:
        """
        Generate PDF for quote.
        
        TODO: Implement actual PDF generation with template.
        """
        quote = self.get_by_id(quote_id)
        
        raise NotImplementedError(
            "PDF generation not yet implemented. "
            "Will use library like WeasyPrint or ReportLab."
        )