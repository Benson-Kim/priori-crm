"""
Purchase Order business logic — service layer.
Purchase orders are vendor-facing.

Financial contract (no discount in v1):
    line_total = quantity x unit_price
    tax_amount = line_total x tax_rate   (tax_rate from tax_type via get_tax_rate)
    subtotal   = Σ line_total
    tax_total  = Σ tax_amount
    total      = subtotal + tax_total
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload

from app.common.analytics import (
    PurchaseOrderEvent,
    emit_event,
    sanitize_error_reason,
)
from app.common.audit import record_audit_event, status_value
from app.common.database import assert_version
from app.common.document_service import BaseDocumentService
from app.common.exceptions import (
    AppException,
    BadRequestException,
    DatabaseException,
    NotFoundException,
)
from app.common.financial import (
    build_line_items,
    calculate_subtotal_vat,
    sum_line_totals,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import (
    Currency,
    DocumentSource,
    PurchaseOrderStatus,
    TaxType,
)
from app.modules.purchase_orders.models import (
    PurchaseOrder,
    PurchaseOrderDocument,
    PurchaseOrderLineItem,
    PurchaseOrderPayment,
)
from app.modules.purchase_orders.queries import (
    PurchaseOrderExportQuery,
    PurchaseOrderStatisticsRepository,
    apply_purchase_order_filters,
)
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCalculationResponse,
    PurchaseOrderCreate,
    PurchaseOrderFilterParams,
    PurchaseOrderLineItemCreate,
    PurchaseOrderPaymentCreate,
    PurchaseOrderPaymentUpdate,
    PurchaseOrderStatusCounts,
    PurchaseOrderSummary,
    PurchaseOrderUpdate,
)

logger = logging.getLogger(__name__)

# Eager-load options shared by the single-resource reads so a PO detail
# never triggers N+1 lazy loads for its vendor / line items / payments /
# documents. The vendor is many-to-one (safe to JOIN), but the three
# one-to-many collections are loaded with selectinload: joined-eager-loading
# more than one collection on a single query Cartesian-multiplies the rows
# (and a .first()/.all() then requires .unique()). selectinload issues one
# constant extra SELECT per collection, so the read stays N+1-free with no
# row explosion (gate C) and needs no .unique(). Mirrors PurchaseOrderPayment
# lazy="selectin" on the model.
PO_EAGER_LOAD_OPTIONS = (
    joinedload(PurchaseOrder.vendor),
    selectinload(PurchaseOrder.line_items),
    selectinload(PurchaseOrder.payments),
    selectinload(PurchaseOrder.documents),
)

# Statuses from which a PO may be deleted. Only DRAFT is deletable in v1:
# once a PO is SENT (and thereafter PAID) it is a durable record and cannot
# be deleted. Cancel is disabled, so there is no soft-void path either.
_DELETABLE_STATUSES = frozenset({PurchaseOrderStatus.DRAFT})


class PurchaseOrderService(BaseDocumentService):
    """Service layer for purchase order operations.

    Shared state-machine and reference-retry mechanics come from
    ``BaseDocumentService``; the transition table and reference-collision
    markers below stay PO-specific. Purchase orders are vendor-facing.

    Lifecycle:
        DRAFT → SENT → PAID
    PAID is reached by full settlement (see record_payment), not a manual
    edge. There are no billed or canceled states for purchase orders.
    """

    MAX_RETRIES = 3
    MAX_REFERENCE_RETRIES = MAX_RETRIES

    _document_noun = "purchase_order"
    _reference_collision_markers = ("po_number", "po_reference")

    # Vendor-facing email wording. The shared DocumentEmailMixin template
    # assumes customer-facing fields (customer.display_name / due_date /
    # total_due) a PO does not have, so the subject/body builders are
    # overridden below; this mapping only supplies the human noun.
    _email_terms: ClassVar[dict[str, str]] = {
        "noun": "purchase_order",
        "date_label": "Delivery date",
        "closing": "Thank you.",
    }

    # Locked-load wiring (DocumentSendMixin._get_locked). The send
    # draft/sent statuses are declared here so the inherited FOR UPDATE row
    # loader and the Draft→Sent transition resolve correctly.
    _send_model = PurchaseOrder
    _send_draft_status = PurchaseOrderStatus.DRAFT
    _send_sent_status = PurchaseOrderStatus.SENT

    ALLOWED_TRANSITIONS: ClassVar[
        dict[PurchaseOrderStatus, list[PurchaseOrderStatus]]
    ] = {
        PurchaseOrderStatus.DRAFT: [PurchaseOrderStatus.SENT],
        # PAID is reached only by full settlement (record_payment, PO-19).
        PurchaseOrderStatus.SENT: [PurchaseOrderStatus.PAID],
        PurchaseOrderStatus.PAID: [],  # terminal
    }

    # REFERENCE GENERATION

    def _generate_po_number(self) -> str:
        """Generate a unique, date-scoped PO number (e.g. PO-20260616-001).

        Mirrors ExpenseService._generate_expense_number: advisory-lock +
        high-water-mark via the shared ReferenceGenerator. The number is
        sortable and scoped per day.
        """
        return self._ref_gen().generate(
            model=PurchaseOrder,
            column=PurchaseOrder.po_number,
            prefix="PO",
            lock_key="po_number",
            width=3,
            use_date_scope=True,
        )

    def _generate_po_reference(self) -> str:
        """Generate a unique, monotonic PO reference (e.g. PO-000042).

        Uses the MAX strategy so the numeric suffix is NEVER reused even
        after the most recent PO is hard-deleted. Mirrors
        ExpenseService._generate_expense_reference.
        """
        return self._ref_gen().generate(
            model=PurchaseOrder,
            column=PurchaseOrder.po_reference,
            prefix="PO",
            lock_key="po_reference_gen",
            width=6,
            use_date_scope=False,
            use_max_strategy=True,
            strip_prefix_len=3,
        )

    def _owner_defaults(self):
        """Resolve the org-scoped PO Settings defaults (PO-11).

        Thin wrapper over OwnerService so create() reads the org default T&C /
        send message / jurisdiction from the single authoritative source.
        Imported lazily to avoid a module-level import cycle
        (owner.service already imports purchase_orders indirectly via the PDF
        renderer path).
        """
        from app.modules.owner.service import OwnerService

        return OwnerService(self._db).purchase_order_defaults()

    @staticmethod
    def _build_line_items(
        raw_items: list[PurchaseOrderLineItemCreate],
    ) -> list[dict]:
        """Delegate to the shared build_line_items helper (no local calc)."""
        return build_line_items(raw_items)

    @staticmethod
    def _sum_line_totals(line_items_data: list[dict]) -> tuple[Decimal, Decimal]:
        """Delegate to the shared sum_line_totals helper (no local calc)."""
        return sum_line_totals(line_items_data)

    @staticmethod
    def _strip_line_tax(line_items_data: list[dict]) -> list[dict]:
        """Force per-line tax to no_tax/0 for purchase orders (PO-27).

        PO VAT is charged once on the subtotal (PO-level), so a line item must
        never carry per-line tax. Mutating the built dicts here keeps the
        persisted rows consistent (tax_type='no_tax', tax_amount=0) and the
        subtotal derivation unaffected (line_total is untouched).
        """
        for item in line_items_data:
            item["tax_type"] = TaxType.NO_TAX.value
            item["tax_amount"] = Decimal("0.00")
        return line_items_data

    def _owner_vat_compliance_ref(self) -> str | None:
        """Default VAT compliance reference from the owner profile's tax_pin.

        Option A (PO-27): the PO's ``vat_compliance_ref`` defaults from the
        organisation's own tax PIN, editable per PO. Resolved via OwnerService
        (lazy import to avoid the module-level cycle) and tolerant of a
        missing profile / tax_pin (returns None).
        """
        from app.modules.owner.service import OwnerService

        owner = OwnerService(self._db).get_or_create()
        return getattr(owner, "tax_pin", None)

    # CREATE

    def create(
        self,
        data: PurchaseOrderCreate,
        user_id: uuid.UUID | None = None,
    ) -> PurchaseOrder:
        """Create a new DRAFT purchase order with line items.

        Validations: vendor must exist and be active; >=1 line item
        (schema-enforced); delivery_date >= order_date (schema + DB CHECK);
        qty > 0 and unit_price >= 0 (schema + DB CHECK).

        Vendor-derived fields (never client-supplied): the PO currency is
        pinned to the vendor's currency, and the compliance / eTIMS reference
        is taken from the vendor's tax_id_pin (KRA PIN). is_recurring is always
        False (the recurring field was removed from the flow). Reference
        collisions are retried via the shared SAVEPOINT loop.
        """
        from app.modules.vendors.models import Vendor

        vendor = self._db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
        if not vendor:
            raise NotFoundException(
                detail=f"Vendor with ID '{data.vendor_id}' not found",
                resource="vendor",
            )
        if not vendor.is_active:
            raise BadRequestException(
                detail=(
                    f"Cannot raise a purchase order for inactive vendor "
                    f"'{vendor.vendor_name}'."
                ),
                field="vendor_id",
            )

        # Vendor-derived, never client-supplied: pin currency to the vendor's
        # currency (KES fallback) and take the compliance / eTIMS reference
        # from the vendor's tax_id_pin (KRA PIN).
        po_currency = vendor.currency or Currency.KES
        compliance_ref = vendor.tax_id_pin

        # PO-level VAT (PO-27): tax is charged once on the subtotal, not per
        # line. Per-line tax fields are forced to no_tax/0 so they can never
        # contribute to PO totals. The VAT compliance ref defaults from the
        # owner profile's tax_pin (editable per PO) when the client omits it.
        vat_enabled = bool(data.vat_enabled)
        vat_rate = data.vat_rate if vat_enabled else None
        vat_compliance_ref = data.vat_compliance_ref
        if vat_compliance_ref is None:
            vat_compliance_ref = self._owner_vat_compliance_ref()

        # Deterministic, no DB writes — computed once outside the retry loop.
        line_items_data = self._strip_line_tax(self._build_line_items(data.line_items))
        subtotal, _line_tax = self._sum_line_totals(line_items_data)
        tax_total = calculate_subtotal_vat(subtotal, vat_enabled, vat_rate)
        total = subtotal + tax_total

        terms_and_conditions = data.terms_and_conditions
        if "terms_and_conditions" not in data.model_fields_set:
            terms_and_conditions = self._owner_defaults().terms_and_conditions

        def _build() -> PurchaseOrder:
            purchase_order = PurchaseOrder(
                po_number=self._generate_po_number(),
                po_reference=self._generate_po_reference(),
                vendor_id=data.vendor_id,
                order_date=data.order_date,
                delivery_date=data.delivery_date,
                currency=po_currency,
                status=PurchaseOrderStatus.DRAFT,
                is_recurring=False,
                subtotal=subtotal,
                tax_total=tax_total,
                total=total,
                amount_paid=Decimal("0.00"),
                balance_due=total,
                vat_enabled=vat_enabled,
                vat_rate=vat_rate,
                vat_compliance_ref=vat_compliance_ref,
                compliance_ref=compliance_ref,
                notes=data.notes,
                terms_and_conditions=terms_and_conditions,
                created_by=user_id,
            )
            self._db.add(purchase_order)
            self._db.flush()

            for item in line_items_data:
                self._db.add(PurchaseOrderLineItem(po_id=purchase_order.id, **item))
            self._db.flush()
            return purchase_order

        purchase_order = self._with_reference_retry(_build, "purchase_order")

        logger.info(
            "Created purchase order: %s",
            purchase_order.po_number,
            extra={
                "po_id": str(purchase_order.id),
                "vendor_id": str(data.vendor_id),
                "total": float(total),
                "created_by": str(user_id) if user_id else None,
            },
        )

        # Fire-and-forget analytics. Emitted last so a failing
        # emitter can never affect the created PO.
        emit_event(
            PurchaseOrderEvent.PO_CREATED,
            {
                "po_id": str(purchase_order.id),
                "vendor_id": str(data.vendor_id),
                "currency": str(po_currency),
                "item_count": len(line_items_data),
                "recurring": bool(purchase_order.is_recurring),
            },
        )
        return purchase_order

    # READ

    def get_by_id(self, po_id: uuid.UUID) -> PurchaseOrder:
        """Retrieve a purchase order by UUID with vendor + line items eager."""
        try:
            purchase_order = (
                self._db.query(PurchaseOrder)
                .options(*PO_EAGER_LOAD_OPTIONS)
                .filter(PurchaseOrder.id == po_id)
                .first()
            )
            if not purchase_order:
                raise NotFoundException(
                    detail=f"Purchase order with ID '{po_id}' not found",
                    resource="purchase_order",
                )
            return purchase_order

        except NotFoundException:
            raise
        except SQLAlchemyError as exc:
            logger.exception("Database error retrieving purchase order %s", po_id)
            raise DatabaseException("Failed to retrieve purchase order") from exc

    def get_by_number(self, po_number: str) -> PurchaseOrder:
        """Retrieve a purchase order by system-generated number."""
        try:
            purchase_order = (
                self._db.query(PurchaseOrder)
                .options(*PO_EAGER_LOAD_OPTIONS)
                .filter(PurchaseOrder.po_number == po_number)
                .first()
            )
            if not purchase_order:
                raise NotFoundException(
                    detail=f"Purchase order '{po_number}' not found",
                    resource="purchase_order",
                )
            return purchase_order

        except NotFoundException:
            raise
        except SQLAlchemyError as exc:
            logger.exception("Database error retrieving purchase order %s", po_number)
            raise DatabaseException("Failed to retrieve purchase order") from exc

    # LIST / AGGREGATES

    # Single source of truth for purchase-order filtering — module-level in
    # queries.py so the export query shares the identical function.
    _apply_filters = staticmethod(apply_purchase_order_filters)

    def list_purchase_orders(
        self,
        params: PaginationParams,
        filters: PurchaseOrderFilterParams | None = None,
    ) -> PaginatedResponse[PurchaseOrderSummary]:
        """Paginated purchase-order list with filtering and full-text search.

        Selects only the summary columns plus a single vendor join — line
        items are never loaded here, so the list cost is constant in the
        number of line items (no N+1). Ordered by created_at DESC (index
        backed). COUNT(*) runs only when ``with_total`` is requested;
        otherwise an over-fetched window of ``per_page + 1`` rows yields an
        exact has_next without a count.
        """
        try:
            from app.modules.vendors.models import Vendor

            query = self._db.query(
                PurchaseOrder.id,
                PurchaseOrder.po_number,
                PurchaseOrder.po_reference,
                PurchaseOrder.vendor_id,
                PurchaseOrder.order_date,
                PurchaseOrder.delivery_date,
                PurchaseOrder.status,
                PurchaseOrder.currency,
                PurchaseOrder.total,
                PurchaseOrder.balance_due,
                PurchaseOrder.is_recurring,
                PurchaseOrder.converted_bill_id,
                PurchaseOrder.created_at,
                Vendor.vendor_name.label("vendor_name"),
            ).join(Vendor, PurchaseOrder.vendor_id == Vendor.id)

            query = self._apply_filters(query, filters)

            total = query.count() if params.with_total else None

            rows = (
                query.order_by(PurchaseOrder.created_at.desc())
                .offset(params.offset)
                .limit(params.fetch_limit)
                .all()
            )

            items = [
                PurchaseOrderSummary(
                    id=row.id,
                    po_number=row.po_number,
                    po_reference=row.po_reference,
                    vendor_id=row.vendor_id,
                    vendor_name=row.vendor_name,
                    order_date=row.order_date,
                    delivery_date=row.delivery_date,
                    status=row.status,
                    currency=row.currency,
                    total=row.total,
                    balance_due=row.balance_due,
                    is_recurring=row.is_recurring,
                    converted_bill_id=row.converted_bill_id,
                    created_at=row.created_at,
                )
                for row in rows
            ]

            logger.debug(
                "Listed purchase orders (page %d, total %s)",
                params.page,
                total,
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

        except SQLAlchemyError as exc:
            logger.exception("Database error listing purchase orders")
            raise DatabaseException("Failed to list purchase orders") from exc

    def list_for_export(
        self,
        filters: PurchaseOrderFilterParams | None = None,
        include_line_items: bool = False,
        limit: int | None = None,
    ) -> list[PurchaseOrder]:
        """Return full PurchaseOrder ORM rows for Excel export, batch-loaded.

        Delegates to PurchaseOrderExportQuery so the export shares the
        identical filter function as the list view.
        """
        return PurchaseOrderExportQuery(self._db).list(
            filters, include_line_items=include_line_items, limit=limit
        )

    def list_payments_for_export(
        self, po_id: uuid.UUID
    ) -> tuple[PurchaseOrder, list[PurchaseOrderPayment]]:
        """Return a purchase order with its payments ordered for Excel export.

        Loads the PO once (raising NotFoundException if missing) and returns
        its payments ordered by payment_date ascending (then created_at) so
        the workbook reads as a chronological statement. Read-only: no flush.
        """
        purchase_order = self.get_by_id(po_id)
        payments = (
            self._db.query(PurchaseOrderPayment)
            .filter(PurchaseOrderPayment.po_id == po_id)
            .order_by(
                PurchaseOrderPayment.payment_date.asc(),
                PurchaseOrderPayment.created_at.asc(),
            )
            .all()
        )
        return purchase_order, payments

    def get_status_counts(self) -> PurchaseOrderStatusCounts:
        """Per-status counts for the filter-tab badges.

        Delegates to PurchaseOrderStatisticsRepository (single grouped query).
        """
        return PurchaseOrderStatisticsRepository(self._db).status_counts()

        # SEND (DRAFT -> SENT) via the shared transactional outbox

    def _capture_owner_snapshot(self, purchase_order: PurchaseOrder) -> None:
        """Stamp an immutable owner-header snapshot the first time the PO is
        sent, so editing the live owner profile cannot re-brand a sent PO.

        Mirrors QuoteService._capture_owner_snapshot.
        """
        if purchase_order.owner_snapshot_id is not None:
            return
        from app.modules.owner.service import OwnerService

        snapshot = OwnerService(self._db).snapshot_current()
        purchase_order.owner_snapshot_id = snapshot.id

    def _validate_sendable(self, purchase_order: PurchaseOrder) -> None:
        """Reject sends for non-Draft purchase orders (DocumentSendMixin hook).

        Send is a Draft-only action: a SENT / PAID PO can never be (re)sent
        (resend after Sent is intentionally unavailable). The locked row is
        checked under FOR UPDATE so the gate cannot act on a stale status.
        """
        if purchase_order.status != PurchaseOrderStatus.DRAFT:
            raise BadRequestException(
                detail=(
                    f"Cannot send a purchase order in "
                    f"'{purchase_order.status}' status. Only DRAFT purchase "
                    "orders can be sent."
                ),
                field="status",
            )

    def _resolve_recipient(
        self, purchase_order: PurchaseOrder, to_email: str | None
    ) -> str:
        """Resolve the send recipient from the VENDOR (POs are vendor-facing).

        Overrides the customer-based default in DocumentSendMixin. An
        explicit ``to_email`` (the editable modal field) wins; otherwise the
        vendor's email is used. A vendor with no email on record blocks Send
        with a typed 400 (defence in depth behind the disabled UI button).
        """
        recipient = to_email or getattr(purchase_order.vendor, "email", None)
        if not recipient:
            raise BadRequestException(
                detail=(
                    "This vendor has no email address on record, so this "
                    "purchase order cannot be sent."
                ),
                field="to_email",
            )
        return recipient

    def _generate_email_subject(self, purchase_order: PurchaseOrder) -> str:
        """Subject: 'Purchase Order {ref} from {Business Name}'."""
        from app.lib.config import settings

        return f"Purchase Order {purchase_order.po_reference} from {settings.APP_NAME}"

    def _generate_email_body(
        self, purchase_order: PurchaseOrder, attached: bool = False
    ) -> str:
        """Vendor-facing plain-text body.

        Overrides the customer-facing DocumentEmailMixin template, which
        references customer.display_name / due_date / total_due — none of
        which apply to a vendor-facing PO.
        """
        from app.lib.config import settings

        vendor_name = getattr(purchase_order.vendor, "vendor_name", "")
        if attached:
            intro = (
                f"Please find attached purchase order "
                f"{purchase_order.po_reference} for "
                f"{purchase_order.currency} {purchase_order.total}."
            )
        else:
            intro = (
                f"Please find the details of purchase order "
                f"{purchase_order.po_reference} for "
                f"{purchase_order.currency} {purchase_order.total} below."
            )
        delivery_line = ""
        if purchase_order.delivery_date is not None:
            delivery_line = (
                f"Delivery date: {purchase_order.delivery_date.strftime('%d %B %Y')}\n"
            )
        return f"""\
Dear {vendor_name},

{intro}
{delivery_line}
Thank you.

Best regards,
{settings.APP_NAME}
"""

    def mark_as_sent(self, po_id: uuid.UUID) -> PurchaseOrder:
        """Mark a DRAFT purchase order as SENT WITHOUT dispatching an email.

        For when the owner has sent the PO to the vendor offline (printed /
        handed over) and just needs the record to reflect SENT. Mirrors the
        locked phase of Send (validate Draft, DRAFT->SENT via the shared
        state machine, stamp sent_at, capture the owner snapshot) but creates
        NO outbox row and sends NO email.

        Locked load: re-reads the row FOR UPDATE via the inherited
        _get_locked so this serializes with Send / record_payment and the
        Draft gate cannot act on a stale status (race-condition contract).
        Only the in-memory transition + flush happen under the lock — no I/O.
        The marked_sent audit row is written atomically with the transition.
        """
        from datetime import UTC, datetime

        purchase_order = self._get_locked(po_id)

        # "Any state can be marked as sent": a PO already in SENT (or beyond,
        # e.g. PAID) is treated as an idempotent no-op rather than an error,
        # so the action is always available from the View screen. Only a
        # DRAFT PO actually transitions through the state machine.
        if purchase_order.status != PurchaseOrderStatus.DRAFT:
            logger.info(
                "Mark-as-sent is a no-op for purchase order %s in '%s' status",
                purchase_order.po_reference,
                purchase_order.status,
                extra={"po_id": str(purchase_order.id)},
            )
            return purchase_order

        previous_status = purchase_order.status
        self._transition(purchase_order, PurchaseOrderStatus.SENT)
        purchase_order.sent_at = datetime.now(UTC)
        # Freeze the owner header exactly as Send does (idempotent if already
        # captured), so a later printed/exported PO is never re-branded.
        self._capture_owner_snapshot(purchase_order)
        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=self._actor_id,
            entity_type="purchase_order",
            entity_id=purchase_order.id,
            action="marked_sent",
            before={"status": status_value(previous_status)},
            after={"status": status_value(PurchaseOrderStatus.SENT)},
        )

        logger.info(
            "Marked purchase order as sent (no email): %s",
            purchase_order.po_reference,
            extra={"po_id": str(purchase_order.id)},
        )

        emit_event(
            PurchaseOrderEvent.PO_MARKED_SENT,
            {"po_id": str(purchase_order.id)},
        )
        return purchase_order

    def send_purchase_order(
        self,
        po_id: uuid.UUID,
        to_email: str | None = None,
        subject: str | None = None,
        body: str | None = None,
        attach_pdf: bool = True,
    ) -> dict[str, Any]:
        """Send a purchase order to its vendor by email (two-phase).

        Phase 1 (shared DocumentSendMixin._prepare_and_mark_sent, locked):
        re-read FOR UPDATE, validate Draft, transition Draft->Sent, stamp
        sent_at, capture the owner snapshot, enqueue the outbox row and
        COMMIT — releasing the row lock and making the SENT transition + the
        durable email record atomic.

        Phase 2 (no lock held): best-effort immediate delivery via
        deliver_now. A failed first attempt is NOT raised: the queued row is
        durable and the outbox drainer retries it (dead-letter after 5),
        so the action never silently loses the email.
        """
        recipient, _subject, _body, sent_at, outbox_id = self._prepare_and_mark_sent(
            po_id, to_email, subject, body, attach_pdf=attach_pdf
        )

        from app.common.email_outbox import EmailOutbox, EmailOutboxService

        delivered = EmailOutboxService(self._db).deliver_now(outbox_id)

        logger.info(
            "Sent purchase order: %s",
            po_id,
            extra={
                "po_id": str(po_id),
                "recipient_email_present": bool(recipient),
                "attached_pdf": attach_pdf,
                "delivered": delivered,
            },
        )

        # Fire-and-forget analytics. The PO is already SENT and the
        # email durably queued regardless of the first-attempt outcome, so
        # these emits never affect delivery. recipient_email_present is a
        # boolean — the raw address is never sent.
        if delivered:
            # Single scalar read for the vendor id (no joins / full load).
            vendor_id = (
                self._db.query(PurchaseOrder.vendor_id)
                .filter(PurchaseOrder.id == po_id)
                .scalar()
            )
            emit_event(
                PurchaseOrderEvent.PO_SENT,
                {
                    "po_id": str(po_id),
                    "vendor_id": str(vendor_id) if vendor_id else None,
                    "recipient_email_present": bool(recipient),
                },
            )
        else:
            # First attempt failed (row stays queued for retry). Classify the
            # durable last_error into a stable, PII-free category.
            failed_row = (
                self._db.query(EmailOutbox).filter(EmailOutbox.id == outbox_id).first()
            )
            last_error = getattr(failed_row, "last_error", None)
            emit_event(
                PurchaseOrderEvent.PO_SEND_FAILED,
                {
                    "po_id": str(po_id),
                    "error_reason": sanitize_error_reason(last_error),
                },
            )

        return {
            "purchase_order_id": po_id,
            "sent_to": recipient,
            "sent_at": sent_at,
            "message": (
                "Purchase order sent successfully"
                if delivered
                else "Purchase order queued for delivery; the first attempt "
                "failed and will be retried automatically"
            ),
        }

    # UPDATE

    def update(
        self,
        po_id: uuid.UUID,
        data: PurchaseOrderUpdate,
        expected_version: int | None = None,
    ) -> PurchaseOrder:
        """Update a purchase order with optimistic locking.

        Editable only in DRAFT; any non-DRAFT status raises BadRequestException
        (the router maps this to the §13 inline-banner message). currency is
        locked — it is not present on PurchaseOrderUpdate, so it can never be
        applied here. Supplying line_items replaces the full set and recomputes
        totals via the shared financial helpers.
        """
        purchase_order = self.get_by_id(po_id)

        if not purchase_order.is_editable:
            raise BadRequestException(
                detail=(
                    f"Cannot edit a purchase order in '{purchase_order.status}' "
                    "status. Only DRAFT purchase orders are editable."
                ),
                field="status",
            )

        # Atomic optimistic-lock guard: locks the row and compares the version
        # under the lock (replaces a non-atomic load-then-compare).
        assert_version(self._db, PurchaseOrder, po_id, expected_version)

        update_data = data.model_dump(exclude_unset=True, mode="python")
        if not update_data:
            return purchase_order  # no-op — nothing to apply

        # Validate a vendor change: the new vendor must exist and be active.
        if "vendor_id" in update_data:
            from app.modules.vendors.models import Vendor

            vendor = (
                self._db.query(Vendor)
                .filter(Vendor.id == update_data["vendor_id"])
                .first()
            )
            if not vendor:
                raise NotFoundException(
                    detail=f"Vendor '{update_data['vendor_id']}' not found",
                    resource="vendor",
                )
            if not vendor.is_active:
                raise BadRequestException(
                    detail=(
                        f"Cannot reassign the purchase order to inactive vendor "
                        f"'{vendor.vendor_name}'."
                    ),
                    field="vendor_id",
                )
            # The compliance / eTIMS reference is vendor-derived, so a vendor
            # change must re-derive it from the new vendor's tax_id_pin (KRA
            # PIN). currency stays locked after first save and is not touched.
            update_data["compliance_ref"] = vendor.tax_id_pin

        # Cross-field date validation across the mixed case (one date in the
        # payload, the other from the DB). Pydantic only guards when both are
        # supplied; the DB CHECK is the final net.
        new_order_date = update_data.get("order_date", purchase_order.order_date)
        new_delivery_date = update_data.get(
            "delivery_date", purchase_order.delivery_date
        )
        if new_delivery_date is not None and new_delivery_date < new_order_date:
            raise BadRequestException(
                detail="delivery_date must be on or after order_date",
                field="delivery_date",
            )

        # Replace line items (full set) and recompute totals.
        if "line_items" in update_data:
            self._db.query(PurchaseOrderLineItem).filter(
                PurchaseOrderLineItem.po_id == po_id
            ).delete()

            raw_items: list[dict] = update_data.pop("line_items")
            typed_items = [PurchaseOrderLineItemCreate(**item) for item in raw_items]
            line_items_data = self._build_line_items(typed_items)

            subtotal, tax_total = self._sum_line_totals(line_items_data)
            total = subtotal + tax_total
            balance_due = total - purchase_order.amount_paid

            # Mirror Expenses: the new total can never drop below what has
            # already been paid against the PO.
            if balance_due < Decimal("0.00"):
                raise BadRequestException(
                    detail=(
                        f"Cannot reduce the total below the amount already "
                        f"paid ({purchase_order.currency} "
                        f"{purchase_order.amount_paid}). Remove or reduce "
                        "payments first."
                    ),
                    field="line_items",
                )

            for item in line_items_data:
                self._db.add(PurchaseOrderLineItem(po_id=purchase_order.id, **item))

            update_data["subtotal"] = subtotal
            update_data["tax_total"] = tax_total
            update_data["total"] = total
            update_data["balance_due"] = balance_due

        # Apply scalar updates (currency is never present — locked).
        for field, value in update_data.items():
            setattr(purchase_order, field, value)

        purchase_order.version += 1
        self._db.flush()

        logger.info(
            "Updated purchase order: %s",
            purchase_order.po_number,
            extra={
                "po_id": str(purchase_order.id),
                "updated_fields": list(update_data.keys()),
                "new_version": purchase_order.version,
            },
        )
        return purchase_order

    # DOCUMENT MANAGEMENT

    def attach_document(
        self,
        po_id: uuid.UUID,
        filename: str,
        file_size_bytes: int,
        mime_type: str,
        storage_key: str,
        source: DocumentSource = DocumentSource.FORM,
        user_id: uuid.UUID | None = None,
        payment_id: uuid.UUID | None = None,
        storage: Any | None = None,
    ) -> PurchaseOrderDocument:
        """Persist document metadata after the file has been written to storage.

        Mirrors ExpenseService.attach_document. The router is responsible for
        the storage write and must call this only after it succeeds, so a DB
        row never references an object that never landed.

        Attachments are addable from the View screen at ANY status (DRAFT /
        SENT / BILLED / CANCELED): a PO attachment is supporting evidence, not
        an edit, so there is no editable-status gate here — the PO existing is
        the only precondition (the router confirms it before the storage
        write). Persisting metadata does not touch the PO row or its version,
        so it never re-opens edit mode.

        The freshly written object is registered for delete-on-rollback (shared
        ``storage_tx`` helper): if this insert — or the outer request commit —
        fails, the object is removed from storage instead of being orphaned.
        ``storage`` is injectable for tests and defaults to the process
        storage facade.
        """
        from app.common.storage_tx import schedule_delete_on_rollback
        from app.lib.storage import storage_service

        schedule_delete_on_rollback(self._db, storage or storage_service, storage_key)

        # Confirm the PO exists — raises NotFoundException if not.
        self.get_by_id(po_id)

        document = PurchaseOrderDocument(
            po_id=po_id,
            payment_id=payment_id,
            filename=filename,
            file_size_bytes=file_size_bytes,
            mime_type=mime_type,
            storage_key=storage_key,
            source=source.value,
            uploaded_by=user_id,
        )
        self._db.add(document)
        self._db.flush()

        logger.info(
            "Attached document to purchase order %s: %s",
            po_id,
            filename,
            extra={
                "po_id": str(po_id),
                "document_id": str(document.id),
                "source": source.value,
                "size_bytes": file_size_bytes,
            },
        )

        # file_size_kb (rounded) only — no filename / storage_key in analytics.
        emit_event(
            PurchaseOrderEvent.PO_DOCUMENT_ATTACHED,
            {
                "po_id": str(po_id),
                "file_size_kb": round(file_size_bytes / 1024),
            },
        )
        return document

    def get_payment(
        self,
        po_id: uuid.UUID,
        payment_id: uuid.UUID,
    ) -> PurchaseOrderPayment:
        """Fetch a single payment record, scoped to its owning PO.

        Scoping the lookup to ``(po_id, payment_id)`` means a proof-of-payment
        upload can never group a document under a payment that belongs to a
        different PO (mirrors ``get_document`` scoping). Raises
        NotFoundException when the payment does not exist on this PO.
        """
        payment = (
            self._db.query(PurchaseOrderPayment)
            .filter(
                PurchaseOrderPayment.id == payment_id,
                PurchaseOrderPayment.po_id == po_id,
            )
            .first()
        )
        if not payment:
            raise NotFoundException(
                detail=(
                    f"Payment '{payment_id}' not found on purchase order '{po_id}'"
                ),
                resource="purchase_order_payment",
            )
        return payment

    def get_document(
        self,
        po_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> PurchaseOrderDocument:
        """Fetch a single document record, scoped to its owning PO.

        Scoping the lookup to ``(po_id, document_id)`` means a download/delete
        authorised for one PO can never reach another PO's attachment by
        UUID guessing (security gate F).
        """
        document = (
            self._db.query(PurchaseOrderDocument)
            .filter(
                PurchaseOrderDocument.id == document_id,
                PurchaseOrderDocument.po_id == po_id,
            )
            .first()
        )
        if not document:
            raise NotFoundException(
                detail=(
                    f"Document '{document_id}' not found on purchase order '{po_id}'"
                ),
                resource="purchase_order_document",
            )
        return document

    def delete_document(
        self,
        po_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> str:
        """Remove document metadata and return its storage_key.

        Two-step by design (mirrors ExpenseService.delete_document): the
        router deletes the object from storage using the returned key only
        after the DB row is gone, so the key is never lost before deletion.
        """
        document = self.get_document(po_id, document_id)

        storage_key = document.storage_key

        self._db.delete(document)
        self._db.flush()

        logger.info(
            "Deleted document %s from purchase order %s",
            document_id,
            po_id,
            extra={
                "po_id": str(po_id),
                "document_id": str(document_id),
            },
        )

        emit_event(
            PurchaseOrderEvent.PO_DOCUMENT_DELETED,
            {
                "po_id": str(po_id),
                "document_id": str(document_id),
            },
        )
        return storage_key  # caller deletes from object storage

    @staticmethod
    def _purge_storage_objects(storage_keys: list[str]) -> None:
        """Best-effort delete of stored objects after a hard delete.

        Mirrors ExpenseService._purge_storage_objects. Storage failures are
        logged, not raised: the DB record is already gone, so a missed file is
        a reconciliation concern, not a request failure.
        """
        from app.lib.storage import storage_service

        for key in storage_keys:
            if storage_service.delete_file(key):
                logger.info(
                    "Purged orphaned storage object",
                    extra={"storage_key": key},
                )
            else:
                logger.warning(
                    "Failed to purge storage object — orphan may remain",
                    extra={"storage_key": key},
                )

    # PAYMENT RECORDING

    def _apply_payment(
        self,
        purchase_order: PurchaseOrder,
        amount: Decimal,
        payment_date,
        *,
        reference: str | None = None,
        notes: str | None = None,
        document_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> PurchaseOrderPayment:
        """Apply a payment to an already-locked PO and settle if covered.

        Single source of truth for record_payment (mirrors
        ExpenseService._apply_payment): creates the audit-trail
        PurchaseOrderPayment, advances amount_paid / balance_due, transitions
        SENT->PAID via the shared state machine when the balance clears, and
        bumps ``version`` exactly once. The caller must have already loaded
        ``purchase_order`` FOR UPDATE and validated status/amount.
        """
        from datetime import UTC, datetime

        payment = PurchaseOrderPayment(
            po_id=purchase_order.id,
            amount=amount,
            payment_date=payment_date,
            reference=reference,
            notes=notes,
            document_id=document_id,
            recorded_by=user_id,
        )
        self._db.add(payment)

        purchase_order.amount_paid += amount
        purchase_order.balance_due = purchase_order.total - purchase_order.amount_paid

        if purchase_order.balance_due <= Decimal("0.00"):
            # Settlement (exact or overpaid). balance_due is NOT clamped: an
            # overpayment leaves it negative to represent the credit owed back.
            #
            # The transition is idempotent for the reconciliation case: a
            # further payment on an already-PAID PO must NOT call
            # _transition(PAID -> PAID), which the state machine rejects
            # (ALLOWED_TRANSITIONS[PAID] = []). Only a SENT PO transitions; an
            # already-PAID PO bumps the version directly and keeps its
            # original paid_at.
            if purchase_order.status == PurchaseOrderStatus.SENT:
                self._transition(purchase_order, PurchaseOrderStatus.PAID)
                purchase_order.paid_at = datetime.now(UTC)
            else:
                purchase_order.version += 1
        else:
            # Partial payment: no status edge, so bump the version here so
            # the optimistic-lock counter still advances exactly once.
            purchase_order.version += 1

        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=user_id,
            entity_type="purchase_order",
            entity_id=purchase_order.id,
            action="payment_recorded",
            after={
                "payment_id": str(payment.id),
                "amount": str(amount),
                "new_balance": str(purchase_order.balance_due),
                "new_status": status_value(purchase_order.status),
            },
        )
        return payment

    def record_payment(
        self,
        po_id: uuid.UUID,
        data: PurchaseOrderPaymentCreate,
        user_id: uuid.UUID | None = None,
    ) -> PurchaseOrderPayment:
        """Record an auditable payment against a purchase order.

        A payment is only meaningful once the PO has been sent: a DRAFT PO
        must be sent first. A SENT or already-PAID / overpaid PO both accept
        payment (this app records and reconciles payments rather than taking
        them, so further and over payments are legitimate). On the first full
        settlement the PO transitions SENT->PAID (see _apply_payment); later
        payments keep it PAID and drive balance_due further negative.

        If ``data.document_id`` is supplied, the referenced document must
        exist and belong to this purchase order (scoped lookup). A document
        that does not exist or belongs to a different PO is rejected with a
        404. Ideally the document should have source ``payment_modal``
        (proof-of-payment), but this is not enforced so that documents
        uploaded via other sources can still be linked if needed.

        Locked load: the bare row is read FOR UPDATE via the inherited
        _get_locked (lazyload('*') keeps the lock off the vendor outer join),
        so concurrent payments serialize on the row lock instead of racing
        (race-condition contract). No blocking I/O runs under the lock.
        """
        purchase_order = self._get_locked(po_id)

        # A payment is only meaningful once the PO has been sent: a DRAFT PO
        # must be sent first. A SENT or already-PAID / overpaid PO both accept
        # payment (reconciliation use case) — there is no PAID rejection.
        if purchase_order.status == PurchaseOrderStatus.DRAFT:
            raise BadRequestException(
                detail=(
                    "Payments can only be recorded against a sent purchase "
                    "order. Send the purchase order first."
                ),
                field="status",
            )

        # Validate the optional proof-of-payment document. The scoped lookup
        # (po_id + document_id) ensures a document from a different PO cannot
        # be linked here — raises NotFoundException if not found.
        document_id: uuid.UUID | None = data.document_id
        if document_id is not None:
            self.get_document(po_id, document_id)

        payment = self._apply_payment(
            purchase_order,
            amount=data.amount,
            payment_date=data.payment_date,
            reference=data.reference,
            notes=data.notes,
            document_id=document_id,
            user_id=user_id,
        )

        logger.info(
            "Recorded payment for purchase order %s",
            purchase_order.po_reference,
            extra={
                "po_id": str(purchase_order.id),
                "payment_id": str(payment.id),
                "amount": float(data.amount),
                "new_balance": float(purchase_order.balance_due),
                "new_status": purchase_order.status,
            },
        )

        emit_event(
            PurchaseOrderEvent.PO_PAYMENT_RECORDED,
            {
                "po_id": str(purchase_order.id),
                "settled": purchase_order.status == PurchaseOrderStatus.PAID,
            },
        )
        return payment

    def _resync_after_payment_change(self, purchase_order: PurchaseOrder) -> None:
        """Recompute amount_paid / balance_due / status from the payment rows.

        Single source of truth for the post-edit / post-delete financial
        state. Sums the PO's remaining payments, recomputes the balance and
        re-derives the lifecycle status:

        - balance cleared on a SENT PO  -> PAID  (stamp paid_at);
        - balance re-opened on a PAID PO -> SENT (clear paid_at).

        The caller must have loaded ``purchase_order`` FOR UPDATE. The
        version bump is owned here so each edit / delete advances the
        optimistic-lock counter exactly once.
        """
        from datetime import UTC, datetime

        total_paid = (
            self._db.query(PurchaseOrderPayment)
            .with_entities(PurchaseOrderPayment.amount)
            .filter(PurchaseOrderPayment.po_id == purchase_order.id)
            .all()
        )
        amount_paid = sum((row.amount for row in total_paid), Decimal("0.00"))

        purchase_order.amount_paid = amount_paid
        balance_due = purchase_order.total - amount_paid
        purchase_order.balance_due = balance_due

        if (
            purchase_order.status == PurchaseOrderStatus.PAID
            and purchase_order.balance_due > Decimal("0.00")
        ):
            # The settling payment was reduced / removed: re-open to SENT so
            # the remaining balance can be collected again.
            purchase_order.status = PurchaseOrderStatus.SENT
            purchase_order.paid_at = None
        elif (
            purchase_order.status == PurchaseOrderStatus.SENT
            and purchase_order.balance_due <= Decimal("0.00")
        ):
            # An edit pushed the balance to zero: settle to PAID.
            purchase_order.status = PurchaseOrderStatus.PAID
            purchase_order.paid_at = datetime.now(UTC)

        purchase_order.version += 1

    def update_payment(
        self,
        po_id: uuid.UUID,
        payment_id: uuid.UUID,
        data: PurchaseOrderPaymentUpdate,
        user_id: uuid.UUID | None = None,
    ) -> PurchaseOrderPayment:
        """Edit a recorded payment and re-derive the PO's financial state.

        Only SENT / PAID purchase orders carry payments, so an edit is valid
        in either status. Overpayments are allowed — the balance_due will go
        negative to reflect the excess.

        Locked load: the PO row is read FOR UPDATE so a concurrent
        record/edit/delete serializes on the row lock and the recomputed
        balance can never race.
        """
        purchase_order = self._get_locked(po_id)
        payment = self.get_payment(po_id, payment_id)

        update_data = data.model_dump(exclude_unset=True, mode="python")
        if not update_data:
            return payment  # no-op

        previous_amount = payment.amount

        for field, value in update_data.items():
            setattr(payment, field, value)
        self._db.flush()

        self._resync_after_payment_change(purchase_order)
        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=user_id,
            entity_type="purchase_order",
            entity_id=purchase_order.id,
            action="payment_updated",
            before={"payment_id": str(payment.id), "amount": str(previous_amount)},
            after={
                "payment_id": str(payment.id),
                "amount": str(payment.amount),
                "new_balance": str(purchase_order.balance_due),
                "new_status": status_value(purchase_order.status),
            },
        )

        logger.info(
            "Updated payment %s on purchase order %s",
            payment_id,
            purchase_order.po_reference,
            extra={
                "po_id": str(purchase_order.id),
                "payment_id": str(payment_id),
                "new_balance": float(purchase_order.balance_due),
                "new_status": purchase_order.status,
            },
        )

        emit_event(
            PurchaseOrderEvent.PO_PAYMENT_RECORDED,
            {
                "po_id": str(purchase_order.id),
                "settled": purchase_order.status == PurchaseOrderStatus.PAID,
            },
        )
        return payment

    def delete_payment(
        self,
        po_id: uuid.UUID,
        payment_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> None:
        """Delete a recorded payment and re-derive the PO's financial state.

        Removing a payment reduces amount_paid and re-opens the balance; if
        the PO had been settled to PAID by this payment it reverts to SENT
        and paid_at is cleared. Any proof-of-payment documents grouped under
        the payment have their payment_id set NULL by the FK (ON DELETE SET
        NULL) — the documents themselves are retained on the PO.

        Locked load: the PO row is read FOR UPDATE so the recompute cannot
        race a concurrent payment mutation.
        """
        purchase_order = self._get_locked(po_id)
        payment = self.get_payment(po_id, payment_id)

        removed_amount = payment.amount

        self._db.delete(payment)
        self._db.flush()

        self._resync_after_payment_change(purchase_order)
        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=user_id,
            entity_type="purchase_order",
            entity_id=purchase_order.id,
            action="payment_deleted",
            before={"payment_id": str(payment_id), "amount": str(removed_amount)},
            after={
                "new_balance": str(purchase_order.balance_due),
                "new_status": status_value(purchase_order.status),
            },
        )

        logger.info(
            "Deleted payment %s from purchase order %s",
            payment_id,
            purchase_order.po_reference,
            extra={
                "po_id": str(purchase_order.id),
                "payment_id": str(payment_id),
                "new_balance": float(purchase_order.balance_due),
                "new_status": purchase_order.status,
            },
        )

        emit_event(
            PurchaseOrderEvent.PO_PAYMENT_RECORDED,
            {
                "po_id": str(purchase_order.id),
                "settled": purchase_order.status == PurchaseOrderStatus.PAID,
            },
        )

    # CANCEL

    def cancel(self, po_id: uuid.UUID) -> PurchaseOrder:
        """Cancel a purchase order: DRAFT | SENT -> CANCELED.

        Cancel voids the PO while preserving the record: a
        CANCELED PO is terminal and can never be re-opened or edited
        (``is_editable`` already gates editing to DRAFT only). BILLED and
        already-CANCELED purchase orders cannot be cancelled — the shared
        ``_transition`` rejects those edges with a typed BadRequestException
        (no silent no-op).

        Locked load: serializes with send / convert / delete so the status
        gate and transition cannot interleave with a concurrent transition
        (race-condition contract — all status transitions load FOR UPDATE).
        The audit row is written in the same locked transaction.
        """
        purchase_order = self._get_locked(po_id)

        previous_status = purchase_order.status
        # Route through the state machine so ALLOWED_TRANSITIONS is enforced
        # and the version bump is owned in one place. BILLED/CANCELED have no
        # CANCELED edge, so this raises BadRequestException for them.
        self._transition(purchase_order, PurchaseOrderStatus.CANCELED)
        self._db.flush()

        # Durable audit trail, atomic with the transition.
        record_audit_event(
            self._db,
            actor_id=self._actor_id,
            entity_type="purchase_order",
            entity_id=purchase_order.id,
            action="canceled",
            before={"status": status_value(previous_status)},
            after={"status": status_value(PurchaseOrderStatus.CANCELED)},
        )

        logger.warning(
            "Canceled purchase order: %s",
            purchase_order.po_reference,
            extra={"po_id": str(purchase_order.id)},
        )

        emit_event(
            PurchaseOrderEvent.PO_CANCELLED,
            {"po_id": str(purchase_order.id)},
        )
        return purchase_order

    # DELETE

    def delete(self, po_id: uuid.UUID) -> bool:
        """Delete a purchase order — permitted only in DRAFT.

        SENT and PAID purchase orders are protected and raise
        BadRequestException: once sent, a PO is a durable record and cannot
        be deleted (cancel is disabled in v1, so there is no void path). A
        before-image audit row is written atomically with the delete
        (committed by get_db()); this is always a hard delete and line items
        cascade.

        Locked load: serializes with concurrent transitions (race-condition
        contract — all status-sensitive ops load FOR UPDATE) so the status
        gate cannot act on a stale row.

        Returns False (no soft-delete path in v1) so the router can keep the
        same X-Delete-Type contract as Expenses.
        """
        purchase_order = self._get_locked(po_id)

        if purchase_order.status not in _DELETABLE_STATUSES:
            raise BadRequestException(
                detail=(
                    f"Cannot delete a purchase order in "
                    f"'{purchase_order.status}' status. Only DRAFT purchase "
                    "orders can be deleted; a sent purchase order is a "
                    "permanent record."
                ),
                field="status",
            )

        try:
            # Capture storage keys before the cascading DB delete so the
            # underlying objects can be purged afterward (no orphaned blobs).
            storage_keys = [doc.storage_key for doc in purchase_order.documents]

            # Before-image audit row — flushed in the same transaction as the
            # delete so the trail can never disagree with the ledger.
            record_audit_event(
                self._db,
                actor_id=self._actor_id,
                entity_type="purchase_order",
                entity_id=purchase_order.id,
                action="hard_deleted",
                before={
                    "status": status_value(purchase_order.status),
                    "po_number": purchase_order.po_number,
                    "po_reference": purchase_order.po_reference,
                    "total": str(purchase_order.total),
                },
            )

            self._db.delete(purchase_order)
            self._db.flush()

            # Best-effort purge after the row is gone.
            self._purge_storage_objects(storage_keys)

            logger.info(
                "Hard-deleted purchase order: %s",
                purchase_order.po_reference,
                extra={
                    "po_id": str(po_id),
                    "purged_objects": len(storage_keys),
                },
            )

            emit_event(
                PurchaseOrderEvent.PO_DELETED,
                {"po_id": str(po_id)},
            )
            return False

        except SQLAlchemyError as exc:
            logger.exception("Error deleting purchase order %s", po_id)
            raise DatabaseException("Failed to delete purchase order") from exc

    # DUPLICATE

    def duplicate(
        self,
        po_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> PurchaseOrder:
        """Duplicate an existing purchase order as a new DRAFT.

        Re-raise an order quickly; also the documented path to "resend" a
        Sent PO. Available at any status.

        Reuse-first: fresh references come from the shared reference
        generators and the create runs inside the shared
        ``_with_reference_retry`` SAVEPOINT loop — no copied reference or
        retry logic.

        Copy rules:
        - copied: line items (incl. tax rows), currency, notes, Terms &
          Conditions, delivery_date, is_recurring;
        - order_date is reset to today;
        - compliance_ref is CLEARED (specific to the original);
        - attached documents are NOT copied.
        """
        from datetime import date

        try:
            original = self.get_by_id(po_id)

            def _build() -> PurchaseOrder:
                duplicate = PurchaseOrder(
                    po_number=self._generate_po_number(),
                    po_reference=self._generate_po_reference(),
                    vendor_id=original.vendor_id,
                    order_date=date.today(),
                    delivery_date=original.delivery_date,
                    currency=original.currency,
                    status=PurchaseOrderStatus.DRAFT,
                    is_recurring=original.is_recurring,
                    subtotal=original.subtotal,
                    tax_total=original.tax_total,
                    total=original.total,
                    compliance_ref=None,  # cleared — specific to the original
                    notes=original.notes,
                    terms_and_conditions=original.terms_and_conditions,
                    created_by=user_id,
                )
                self._db.add(duplicate)
                self._db.flush()

                for orig_item in original.line_items:
                    self._db.add(
                        PurchaseOrderLineItem(
                            po_id=duplicate.id,
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
                    "Duplicated purchase order %s → %s",
                    original.po_number,
                    duplicate.po_number,
                    extra={
                        "original_id": str(original.id),
                        "duplicate_id": str(duplicate.id),
                    },
                )

                emit_event(
                    PurchaseOrderEvent.PO_DUPLICATED,
                    {
                        "source_po_id": str(original.id),
                        "new_po_id": str(duplicate.id),
                    },
                )
                return duplicate

            return self._with_reference_retry(_build, "purchase_order")

        except NotFoundException:
            raise
        except SQLAlchemyError as exc:
            logger.exception("Error duplicating purchase order %s", po_id)
            raise DatabaseException("Failed to duplicate purchase order") from exc

    # CALCULATION PREVIEW

    @classmethod
    def calculate_totals(
        cls,
        line_items: list[PurchaseOrderLineItemCreate],
    ) -> PurchaseOrderCalculationResponse:
        """
        Calculate PO totals without persisting — live preview endpoint.

        No discount in v1: total = subtotal + tax_total. Every monetary
        value is produced by ``app.common.financial`` so the result is, by
        construction, identical to the Expenses/Quotes engine for the same
        inputs (parity is asserted in tests).
        """
        calculated_items = cls._build_line_items(line_items)
        subtotal, tax_total = cls._sum_line_totals(calculated_items)

        formatted_items = [
            {
                "item_name": item["item_name"],
                "description": item["description"],
                "quantity": float(item["quantity"]),
                "unit_price": float(item["unit_price"]),
                "line_total": float(item["line_total"]),
                "tax_type": item["tax_type"],
                "tax_amount": float(item["tax_amount"]),
            }
            for item in calculated_items
        ]

        return PurchaseOrderCalculationResponse(
            subtotal=subtotal,
            tax_total=tax_total,
            total=subtotal + tax_total,
            line_items=formatted_items,
        )

    # PDF GENERATION

    def _render_pdf(
        self, purchase_order: PurchaseOrder, include_balance: bool = False
    ) -> bytes:
        """Render a PDF for an already-loaded purchase order.

        Orchestration (owner branding + ReportLab generator) is shared with
        invoices/quotes via DocumentPdfRenderer — no PO-specific copy.

        ``include_balance`` selects the original-amount document (False) or
        the balance-aware current/statement document (True).

        A rendering failure is not swallowed: it surfaces as an explicit 500
        carrying the message so the UI can show "PDF could not be
        generated. Please try again." instead of leaking a stack trace.
        """
        from app.common.pdf_renderer import DocumentPdfRenderer

        try:
            return DocumentPdfRenderer(self._db).render_purchase_order(
                purchase_order, include_balance=include_balance
            )
        except Exception as exc:
            logger.exception(
                "Failed to render purchase-order PDF %s",
                purchase_order.po_reference,
                extra={"po_id": str(purchase_order.id)},
            )
            raise AppException(
                status_code=500,
                detail="PDF could not be generated. Please try again.",
                error_code="PDF_GENERATION_FAILED",
            ) from exc

    def generate_pdf(self, po_id: uuid.UUID, include_balance: bool = False) -> bytes:
        """Generate the PDF for a purchase order by id.

        ``include_balance`` selects the original-amount document (False) or
        the balance-aware current/statement document (True).
        """
        return self._render_pdf(self.get_by_id(po_id), include_balance=include_balance)

    def generate_pdf_for_download(
        self, po_id: uuid.UUID, include_balance: bool = False
    ) -> tuple[bytes, PurchaseOrder]:
        """Generate the PO PDF and return it with the loaded purchase order.

        Loads the PO once and renders from it, so the download endpoint does
        not re-query just for the attachment filename (mirrors
        QuoteService.generate_pdf_for_download). ``include_balance`` selects
        the original-amount vs balance-aware variant.
        """
        purchase_order = self.get_by_id(po_id)
        return (
            self._render_pdf(purchase_order, include_balance=include_balance),
            purchase_order,
        )
