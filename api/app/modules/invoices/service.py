"""Invoice business logic with complex financial calculations and state machine."""
import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, or_, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    DatabaseException,
    NotFoundException,
)
from app.common.financial import calculate_discount, calculate_line_item
from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import DiscountType, InvoiceStatus, TaxType
from app.modules.invoices.models import Invoice, InvoiceLineItem, Payment
from app.modules.invoices.schemas import (
    InvoiceCalculationResponse,
    InvoiceCreate,
    InvoiceDetailResponse,
    InvoiceDuplicateResponse,
    InvoiceFilterParams,
    InvoiceLineItemCreate,
    InvoiceResponse,
    InvoiceStatusCounts,
    InvoiceSummary,
    InvoiceUpdate,
    PaymentCreate,
    PaymentResponse,
)

logger = logging.getLogger(__name__)


class InvoiceService:
    """
    Service layer for invoice operations.

    Follows the same atomicity and race-condition contracts as QuoteService.
    """

    MAX_INVOICE_NUMBER_RETRIES = 3

    ALLOWED_TRANSITIONS: dict[InvoiceStatus, list[InvoiceStatus]] = {
        InvoiceStatus.DRAFT:    [InvoiceStatus.SENT, InvoiceStatus.CANCELED],
        InvoiceStatus.SENT:     [InvoiceStatus.PARTIAL, InvoiceStatus.PAID, InvoiceStatus.OVERDUE, InvoiceStatus.CANCELED],
        InvoiceStatus.PARTIAL:  [InvoiceStatus.PAID, InvoiceStatus.OVERDUE, InvoiceStatus.CANCELED],
        InvoiceStatus.OVERDUE:  [InvoiceStatus.PARTIAL, InvoiceStatus.PAID, InvoiceStatus.CANCELED],
        InvoiceStatus.PAID:     [InvoiceStatus.CANCELED],
        InvoiceStatus.CANCELED: [],
    }

    def __init__(self, db: Session) -> None:
        self._db = db

    def _transition(self, invoice: Invoice, new_status: InvoiceStatus) -> None:
        """Enforce ALLOWED_TRANSITIONS and bump version atomically."""
        current = InvoiceStatus(invoice.status)
        allowed = self.ALLOWED_TRANSITIONS.get(current, [])
        if new_status not in allowed:
            raise BadRequestException(
                detail=(
                    f"Cannot transition invoice from '{current}' to '{new_status}'. "
                    f"Allowed: {[s.value for s in allowed] or 'none (terminal state)'}"
                ),
                field="status",
            )
        invoice.status = new_status
        invoice.version += 1

    def _advisory_lock(self, key: str) -> None:
        """Acquire a PostgreSQL transaction-scoped advisory lock."""
        self._db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": key},
        )

    # CREATE

    def create(self, data: InvoiceCreate, user_id: uuid.UUID | None = None) -> Invoice:
        """
        Create a new invoice with line items and calculations.
        Uses bounded retry for invoice number collisions.
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
                detail=f"Cannot create invoice for inactive customer: {customer.display_name}",
                field="customer_id"
            )

        line_items_data: list[dict] = []
        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")

        for idx, item_data in enumerate(data.line_items, start=1):
            line_total, tax_amount = calculate_line_item(
                item_data.quantity, item_data.unit_price, item_data.tax_type
            )
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

        discount_value = calculate_discount(
            subtotal, data.discount_type, data.discount_amount, data.discount_percentage
        )
        total_due = subtotal - discount_value + tax_total

        last_error: Exception | None = None
        for attempt in range(self.MAX_INVOICE_NUMBER_RETRIES + 1):
            sp = self._db.begin_nested()
            try:
                invoice_number = self._generate_invoice_number()
                invoice_reference = self._generate_invoice_reference()

                invoice = Invoice(
                    invoice_number=invoice_number,
                    invoice_reference=invoice_reference,
                    customer_id=data.customer_id,
                    transaction_date=data.transaction_date,
                    due_date=data.due_date,
                    currency=data.currency,
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
                sp.commit()

                logger.info(
                    "Created invoice: %s", invoice.invoice_number,
                    extra={
                        "invoice_id": str(invoice.id),
                        "customer_id": str(data.customer_id),
                        "total_due": float(total_due),
                        "created_by": str(user_id) if user_id else None,
                    },
                )
                return invoice

            except IntegrityError as e:
                sp.rollback()
                last_error = e
                is_collision = (
                    "invoice_number" in str(e.orig) or "invoice_reference" in str(e.orig)
                )
                if is_collision and attempt < self.MAX_INVOICE_NUMBER_RETRIES:
                    logger.warning("Invoice number collision, retry %d", attempt + 1)
                    continue
                raise ConflictException("Invoice data violates database constraints") from e

        raise ConflictException("Failed to generate unique invoice number after retries") from last_error

    # READ

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
                    resource="invoice"
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
                    detail=f"Invoice '{invoice_number}' not found",
                    resource="invoice"
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
            query = (
                self._db.query(
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
                )
                .join(Customer, Invoice.customer_id == Customer.id)
            )

            # Apply filters
            if filters:
                if filters.status:
                    query = query.filter(Invoice.status == filters.status)
                
                if filters.customer_id:
                    query = query.filter(Invoice.customer_id == filters.customer_id)
                
                if filters.date_from:
                    query = query.filter(Invoice.transaction_date >= filters.date_from)
                
                if filters.date_to:
                    query = query.filter(Invoice.transaction_date <= filters.date_to)
                
                if filters.due_date_from:
                    query = query.filter(Invoice.due_date >= filters.due_date_from)
                
                if filters.due_date_to:
                    query = query.filter(Invoice.due_date <= filters.due_date_to)
                
                if filters.search:
                    search_term = f"%{filters.search}%"
                    query = query.filter(
                        or_(
                            Invoice.invoice_number.ilike(search_term),
                            Invoice.invoice_reference.ilike(search_term),
                            Customer.company_name.ilike(search_term),
                            Customer.first_name.ilike(search_term),
                            Customer.last_name.ilike(search_term),
                        )
                    )

            total = query.count()

            results = (
                query
                .order_by(Invoice.created_at.desc())
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
                }
            )

            return PaginatedResponse.create(items=items, total=total, params=params)

        except SQLAlchemyError as e:
            logger.exception("Database error listing invoices")
            raise DatabaseException("Failed to list invoices") from e

    def get_status_counts(self, customer_id: uuid.UUID | None = None) -> InvoiceStatusCounts:
        """Get counts of invoices by status"""
        try:
            today = date.today()
            query = self._db.query(
                Invoice.status,
                func.count(Invoice.id).label("cnt"),
                func.sum(
                    case(
                        (
                            and_(
                                Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]),
                                Invoice.due_date < today,
                                Invoice.balance_due > 0,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("overdue_cnt"),
            )

            if customer_id:
                query = query.filter(Invoice.customer_id == customer_id)

            rows = query.group_by(Invoice.status).all()

            counts_dict: dict[str, int] = {}
            overdue_count = 0
            total = 0
            for status_val, cnt, od_cnt in rows:
                counts_dict[status_val] = cnt
                total += cnt
                overdue_count += od_cnt or 0

            return InvoiceStatusCounts(
                all=total,
                draft=counts_dict.get(InvoiceStatus.DRAFT, 0),
                sent=counts_dict.get(InvoiceStatus.SENT, 0),
                partial=counts_dict.get(InvoiceStatus.PARTIAL, 0),
                paid=counts_dict.get(InvoiceStatus.PAID, 0),
                overdue=overdue_count,
                canceled=counts_dict.get(InvoiceStatus.CANCELED, 0),
            )

        except SQLAlchemyError as e:
            logger.exception("Database error getting status counts")
            raise DatabaseException("Failed to get status counts") from e

    def get_invoice_statistics(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """
        Get invoice statistics for dashboard using SQL aggregates.

        All computation is pushed to the database — no full table scan.
        Excludes canceled invoices.  Defaults to current calendar month.
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

            agg = (
                self._db.query(
                    func.count(Invoice.id).label("total_invoices"),
                    func.coalesce(func.sum(Invoice.total_due), Decimal("0")).label("total_invoiced"),
                    func.coalesce(func.sum(Invoice.amount_paid), Decimal("0")).label("total_paid"),
                    func.coalesce(func.sum(Invoice.balance_due), Decimal("0")).label("total_outstanding"),
                    func.coalesce(
                        func.avg(
                            case(
                                (
                                    and_(
                                        Invoice.status == InvoiceStatus.PAID,
                                        Invoice.paid_at.isnot(None),
                                    ),
                                    func.extract(
                                        "epoch",
                                        Invoice.paid_at - func.cast(Invoice.transaction_date, type_=Invoice.paid_at.type),
                                    ) / 86400,
                                )
                            )
                        ),
                        0,
                    ).label("avg_days_to_payment"),
                    func.count(
                        case(
                            (
                                and_(
                                    Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]),
                                    Invoice.due_date < today,
                                    Invoice.balance_due > 0,
                                ),
                                Invoice.id,
                            )
                        )
                    ).label("overdue_count"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    and_(
                                        Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PARTIAL, InvoiceStatus.OVERDUE]),
                                        Invoice.due_date < today,
                                        Invoice.balance_due > 0,
                                    ),
                                    Invoice.balance_due,
                                ),
                                else_=0,
                            )
                        ),
                        Decimal("0"),
                    ).label("overdue_amount"),
                )
                .filter(
                    Invoice.status != InvoiceStatus.CANCELED,
                    Invoice.transaction_date >= date_from if date_from else True,
                    Invoice.transaction_date <= date_to if date_to else True,
                )
                .one()
            )

            total = agg.total_invoices or 0
            avg_value = (
                (Decimal(str(agg.total_invoiced)) / total) if total > 0 else Decimal("0.00")
            )

            logger.debug(
                "Calculated invoice statistics",
                extra={"date_from": str(date_from), "date_to": str(date_to), "total": total},
            )

            return {
                "total_invoices":          total,
                "total_invoiced":          Decimal(str(agg.total_invoiced)),
                "total_paid":              Decimal(str(agg.total_paid)),
                "total_outstanding":       Decimal(str(agg.total_outstanding)),
                "average_invoice_value":   avg_value,
                "average_days_to_payment": round(float(agg.avg_days_to_payment or 0), 1),
                "overdue_count":           agg.overdue_count or 0,
                "overdue_amount":          Decimal(str(agg.overdue_amount)),
                "date_from":               date_from,
                "date_to":                 date_to,
            }

        except SQLAlchemyError as e:
            logger.exception("Database error calculating invoice statistics")
            raise DatabaseException("Failed to calculate statistics") from e

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
                detail=f"Cannot edit invoice in {invoice.status} status",
                field="status"
            )

        if expected_version is not None and invoice.version != expected_version:
            raise ConflictException(
                detail=(
                    f"Invoice has been modified by another user. "
                    f"Expected version {expected_version}, current version {invoice.version}"
                )
            )

        update_data = data.model_dump(exclude_unset=True)

        if not update_data:
            return invoice  # No updates

        if invoice.status == InvoiceStatus.SENT:
            restricted_fields = ["customer_id", "transaction_date", "currency"]
            for field in restricted_fields:
                if field in update_data:
                    raise BadRequestException(
                        detail=f"Cannot change {field} after invoice has been sent",
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
            self._db.query(InvoiceLineItem).filter(
                InvoiceLineItem.invoice_id == invoice_id
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

                quantity   = Decimal(str(item["quantity"]))
                unit_price = Decimal(str(item["unit_price"]))
                line_total, tax_amount = calculate_line_item(quantity, unit_price, item["tax_type"])

                line_item = InvoiceLineItem(
                    invoice_id=invoice.id,
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

        # Recalculate totals if discount or subtotal changed
        if any(k in update_data for k in ["subtotal", "discount_type", "discount_amount", "discount_percentage"]):
            subtotal = update_data.get("subtotal", invoice.subtotal)
            tax_total = update_data.get("tax_total", invoice.tax_total)

            effective_type       = update_data.get("discount_type", invoice.discount_type)
            effective_amount     = update_data.get("discount_amount", invoice.discount_amount)
            effective_percentage = update_data.get("discount_percentage", invoice.discount_percentage)

            discount_value = calculate_discount(subtotal, effective_type, effective_amount, effective_percentage)

            total_due = subtotal - discount_value + tax_total
            balance_due = total_due - invoice.amount_paid

            update_data["total_due"] = total_due
            update_data["balance_due"] = balance_due

        # Apply updates
        for field, value in update_data.items():
            setattr(invoice, field, value)

        # Increment version for optimistic locking
        invoice.version += 1

        self._db.flush()

        logger.info(
            f"Updated invoice: {invoice.invoice_number}",
            extra={
                "invoice_id": str(invoice.id),
                "updated_fields": list(update_data.keys()),
                "new_version": invoice.version,
            }
        )

        return invoice

   
    # STATUS TRANSITIONS & ACTIONS

    def mark_as_sent(
        self,
        invoice_id: uuid.UUID,
        sent_at: datetime | None = None,
    ) -> Invoice:
        """Mark invoice as sent. Transitions: DRAFT → SENT."""
        invoice = self.get_by_id(invoice_id)
        self._transition(invoice, InvoiceStatus.SENT)
        invoice.sent_at = sent_at or datetime.now(UTC)
        self._db.flush()
        logger.info("Marked invoice as sent: %s", invoice.invoice_number,
                    extra={"invoice_id": str(invoice.id), "sent_at": invoice.sent_at})
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

        Uses SELECT FOR UPDATE to prevent TOCTOU race conditions.
        """
        invoice = (
            self._db.query(Invoice)
            .options(joinedload(Invoice.customer))
            .filter(Invoice.id == invoice_id)
            .with_for_update()
            .first()
        )
        if not invoice:
            raise NotFoundException(detail=f"Invoice '{invoice_id}' not found", resource="invoice")

        if invoice.status == InvoiceStatus.CANCELED:
            raise BadRequestException(
                detail="Cannot send a canceled invoice",
                field="status",
            )

        recipient = to_email or invoice.customer.email
        if not recipient:
            raise BadRequestException(
                detail="No email address available for this customer",
                field="to_email",
            )

        email_subject = subject or self._generate_email_subject(invoice)
        email_body = body or self._generate_email_body(invoice)

        # TODO: Generate PDF if attach_pdf is True
        # TODO: Send email via email service
            # from app.lib.email import email_service
            # email_service.send_invoice_email(
            #     recipient=recipient,
            #     subject=email_subject,
            #     body=email_body,
            #     pdf_attachment=pdf_data,
            # )

        # Mark as sent if currently draft
        sent_at = datetime.now(UTC)
        if invoice.status == InvoiceStatus.DRAFT:
            self._transition(invoice, InvoiceStatus.SENT)
            invoice.sent_at = sent_at
            self._db.flush()

        logger.info(
            "Sent invoice: %s", invoice.invoice_number,
            extra={"invoice_id": str(invoice.id), "recipient": recipient, "attached_pdf": attach_pdf},
        )
        return {
            "invoice_id": invoice.id,
            "sent_to": recipient,
            "sent_at": sent_at,
            "message": "Invoice sent successfully",
        }

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
        """
        invoice = self.get_by_id(invoice_id)

        # Validate invoice status
        if invoice.status == InvoiceStatus.DRAFT:
            raise BadRequestException(
                detail="Cannot record payment for draft invoice. Send it first.",
                field="status"
            )

        if invoice.status == InvoiceStatus.CANCELED:
            raise BadRequestException(
                detail="Cannot record payment for canceled invoice",
                field="status"
            )

        # Validate payment amount
        if data.amount > invoice.balance_due:
            raise BadRequestException(
                detail=(
                    f"Payment amount ({data.amount}) exceeds balance due ({invoice.balance_due}). "
                    f"Cannot overpay invoice."
                ),
                field="amount"
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

        # Update status based on new balance
        if invoice.balance_due <= 0:
            invoice.status = InvoiceStatus.PAID
            invoice.paid_at = datetime.now(UTC)
        elif invoice.amount_paid > 0:
            invoice.status = InvoiceStatus.PARTIAL

        invoice.version += 1

        self._db.flush()

        self._update_customer_balance(invoice.customer_id)

        logger.info(
            "Recorded payment for invoice %s", invoice.invoice_number,
            extra={
                "invoice_id": str(invoice.id),
                "payment_id": str(payment.id),
                "amount": float(data.amount),
                "new_balance": float(invoice.balance_due),
                "new_status": invoice.status,
            }
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
            last_error: Exception | None = None

            for attempt in range(self.MAX_INVOICE_NUMBER_RETRIES + 1):
                sp = self._db.begin_nested()
                try:
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
                        self._db.add(InvoiceLineItem(
                            invoice_id=duplicate.id,
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
                    sp.commit()
                    logger.info(
                        "Duplicated invoice %s → %s",
                        original.invoice_number, duplicate.invoice_number,
                    )
                    return duplicate

                except IntegrityError as exc:
                    sp.rollback()
                    last_error = exc
                    is_collision = (
                        "invoice_number" in str(exc.orig) or "invoice_reference" in str(exc.orig)
                    )
                    if is_collision and attempt < self.MAX_INVOICE_NUMBER_RETRIES:
                        logger.warning("Collision during duplicate, retry %d", attempt + 1)
                        continue
                    raise ConflictException("Invoice data violates database constraints") from exc

            raise ConflictException(
                "Failed to generate unique invoice number after retries"
            ) from last_error

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
                detail="Invoice is already canceled",
                field="status"
            )

        invoice.status = InvoiceStatus.CANCELED
        invoice.version += 1

        self._db.flush()

        logger.warning(
            f"Canceled invoice: {invoice.invoice_number}",
            extra={"invoice_id": str(invoice.id)}
        )

        return invoice


    # CALCULATIONS & UTILITIES

    @classmethod
    def calculate_totals(
        cls,
        line_items: list[InvoiceLineItemCreate],
        discount_type: DiscountType | None = None,
        discount_amount: Decimal | None = None,
        discount_percentage: Decimal | None = None,
    ) -> InvoiceCalculationResponse:
        """Calculate invoice totals without saving (preview)."""
        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")
        calculated_items = []

        for item in line_items:
            line_total, tax_amount = calculate_line_item(
                item.quantity, item.unit_price, item.tax_type
            )
            subtotal  += line_total
            tax_total += tax_amount
            calculated_items.append({
                "item_name":   item.item_name,
                "description": item.description,
                "quantity":    float(item.quantity),
                "unit_price":  float(item.unit_price),
                "line_total":  float(line_total),
                "tax_type":    item.tax_type,
                "tax_amount":  float(tax_amount),
            })

        discount_value = calculate_discount(subtotal, discount_type, discount_amount, discount_percentage)
        total_due = subtotal - discount_value + tax_total

        return InvoiceCalculationResponse(
            subtotal=subtotal,
            discount_value=discount_value,
            tax_total=tax_total,
            total_due=total_due,
            line_items=calculated_items,
        )

    # Customer balance synchronisation

    def _update_customer_balance(self, customer_id: uuid.UUID) -> None:
        """
        Recalculate and persist Customer.balance from outstanding invoices.

        Called after any payment recording or status transition that changes
        the balance_due on invoices belonging to this customer.
        """
        from app.modules.customers.models import Customer

        outstanding = (
            self._db.query(func.coalesce(func.sum(Invoice.balance_due), Decimal("0.00")))
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
                "Updated customer balance to %s", outstanding,
                extra={"customer_id": str(customer_id)},
            )

    def _generate_invoice_number(self) -> str:
        """Generate a unique invoice number with advisory lock."""
        today = date.today()
        prefix = f"INV-{today.strftime('%Y%m%d')}"
        self._advisory_lock(f"invoice_number_{prefix}")
        count = (
            self._db.query(func.count(Invoice.id))
            .filter(Invoice.invoice_number.like(f"{prefix}%"))
            .scalar()
        )
        return f"{prefix}-{count + 1:03d}"

    def _generate_invoice_reference(self) -> str:
        """Generate a unique invoice reference with advisory lock."""
        self._advisory_lock("invoice_reference_gen")
        count = self._db.query(func.count(Invoice.id)).scalar()
        return f"IN-{count + 1:04d}"

    # Public aliases for cross-module use (e.g., QuoteService.convert_to_invoice)
    def generate_invoice_number(self) -> str:
        """Public wrapper for invoice number generation."""
        return self._generate_invoice_number()

    def generate_invoice_reference(self) -> str:
        """Public wrapper for invoice reference generation."""
        return self._generate_invoice_reference()

    def _generate_email_subject(self, invoice: Invoice) -> str:
        """Generate email subject for invoice."""
        from app.lib.config import settings
        return f"Invoice {invoice.invoice_reference} from {settings.APP_NAME}"

    def _generate_email_body(self, invoice: Invoice) -> str:
        """Generate plain-text email body for invoice."""
        from app.lib.config import settings
        return f"""\
Dear {invoice.customer.display_name},

Please find attached invoice {invoice.invoice_reference} for {invoice.currency} {invoice.total_due}.

Due date: {invoice.due_date.strftime('%d %B %Y')}

Thank you for your business.

Best regards,
{settings.APP_NAME}
"""

    # PDF GENERATION (Placeholder)

    def generate_pdf(self, invoice_id: uuid.UUID) -> bytes:
        """Generate PDF for invoice. TODO: Implement with WeasyPrint or ReportLab."""
        self.get_by_id(invoice_id)
        raise NotImplementedError(
            "PDF generation not yet implemented. "
            "Will use WeasyPrint or ReportLab."
        )
