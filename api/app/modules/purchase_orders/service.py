"""
Purchase Order business logic — service layer.
Purchase orders are vendor-facing.

Scope by issue:
- Later issues add list/export (PO-04), send (PO-06), convert (PO-07),
  cancel (PO-08) and duplicate (PO-09).

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
from sqlalchemy.orm import joinedload

from app.common.audit import record_audit_event, status_value
from app.common.database import assert_version
from app.common.document_service import BaseDocumentService
from app.common.exceptions import (
    AppException,
    BadRequestException,
    DatabaseException,
    NotFoundException,
)
from app.common.financial import build_line_items, sum_line_totals
from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import PurchaseOrderStatus
from app.modules.purchase_orders.models import PurchaseOrder, PurchaseOrderLineItem
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
    PurchaseOrderStatusCounts,
    PurchaseOrderSummary,
    PurchaseOrderUpdate,
)

logger = logging.getLogger(__name__)

# Eager-load options shared by the single-resource reads so a PO detail
# never triggers N+1 lazy loads for its vendor / line items.
PO_EAGER_LOAD_OPTIONS = (
    joinedload(PurchaseOrder.vendor),
    joinedload(PurchaseOrder.line_items),
)

# Statuses from which a PO may be deleted. SENT / BILLED are
# protected: a sent or billed PO must be Canceled (PO-08) or is immutable.
_DELETABLE_STATUSES = frozenset(
    {PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.CANCELED}
)


class PurchaseOrderService(BaseDocumentService):
    """Service layer for purchase order operations.

    Shared state-machine and reference-retry mechanics come from
    ``BaseDocumentService``; the transition table and reference-collision
    markers below stay PO-specific. Purchase orders are vendor-facing.

    Lifecycle:
        DRAFT → SENT → BILLED
        DRAFT | SENT → CANCELED   (terminal)
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
    # loader and the Draft→Sent transition used by PO-06 resolve correctly.
    _send_model = PurchaseOrder
    _send_draft_status = PurchaseOrderStatus.DRAFT
    _send_sent_status = PurchaseOrderStatus.SENT

    ALLOWED_TRANSITIONS: ClassVar[
        dict[PurchaseOrderStatus, list[PurchaseOrderStatus]]
    ] = {
        PurchaseOrderStatus.DRAFT: [
            PurchaseOrderStatus.SENT,
            PurchaseOrderStatus.CANCELED,
        ],
        PurchaseOrderStatus.SENT: [
            PurchaseOrderStatus.BILLED,
            PurchaseOrderStatus.CANCELED,
        ],
        PurchaseOrderStatus.BILLED: [],  # terminal
        PurchaseOrderStatus.CANCELED: [],  # terminal
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

    # CREATE

    def create(
        self,
        data: PurchaseOrderCreate,
        user_id: uuid.UUID | None = None,
    ) -> PurchaseOrder:
        """Create a new DRAFT purchase order with line items.

        Validations: vendor must exist and be active; >=1 line item
        (schema-enforced); delivery_date >= order_date (schema + DB CHECK);
        qty > 0 and unit_price >= 0 (schema + DB CHECK). Currency is pinned to
        the vendor's currency so a schema default can never silently drift it.
        Reference collisions are retried via the shared SAVEPOINT loop.
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

        # Single-currency-per-vendor (mirrors the Expenses rule): reject an
        # explicitly mismatched currency; otherwise pin to the vendor's
        # currency so the schema default can never silently drift it.
        if (
            "currency" in data.model_fields_set
            and vendor.currency
            and data.currency != vendor.currency
        ):
            raise BadRequestException(
                detail=(
                    f"Purchase order currency '{data.currency}' does not match "
                    f"the vendor's currency '{vendor.currency}'. A vendor can "
                    "only transact in a single currency."
                ),
                field="currency",
            )
        po_currency = vendor.currency or data.currency

        # Deterministic, no DB writes — computed once outside the retry loop.
        line_items_data = self._build_line_items(data.line_items)
        subtotal, tax_total = self._sum_line_totals(line_items_data)
        total = subtotal + tax_total

        def _build() -> PurchaseOrder:
            purchase_order = PurchaseOrder(
                po_number=self._generate_po_number(),
                po_reference=self._generate_po_reference(),
                vendor_id=data.vendor_id,
                order_date=data.order_date,
                delivery_date=data.delivery_date,
                currency=po_currency,
                status=PurchaseOrderStatus.DRAFT,
                is_recurring=data.is_recurring,
                subtotal=subtotal,
                tax_total=tax_total,
                total=total,
                compliance_ref=data.compliance_ref,
                notes=data.notes,
                terms_and_conditions=data.terms_and_conditions,
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

    # Single source of truth for purchase-order filtering, including the
    # CANCELED-visibility rule — module-level in queries.py so the export
    # query shares the identical function.
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

        Send is a Draft-only action: a SENT / BILLED / CANCELED PO can never
        be (re)sent (PRD §14 — resend after Sent is intentionally
        unavailable). The locked row is checked under FOR UPDATE so the gate
        cannot act on a stale status.
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
        """Subject: 'Purchase Order {ref} from {Business Name}' (PRD §6.6)."""
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

        from app.common.email_outbox import EmailOutboxService

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

        Editable only in DRAFT; SENT/BILLED/CANCELED raise BadRequestException
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

            for item in line_items_data:
                self._db.add(PurchaseOrderLineItem(po_id=purchase_order.id, **item))

            update_data["subtotal"] = subtotal
            update_data["tax_total"] = tax_total
            update_data["total"] = subtotal + tax_total

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

    # CANCEL

    def cancel(self, po_id: uuid.UUID) -> PurchaseOrder:
        """Cancel a purchase order: DRAFT | SENT -> CANCELED.

        Cancel voids the PO while preserving the record (PRD §12): a
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
        return purchase_order

    # DELETE

    def delete(self, po_id: uuid.UUID) -> bool:
        """Delete a purchase order — permitted only in DRAFT or CANCELED.

        SENT / BILLED purchase orders are protected and raise
        BadRequestException. A before-image audit row is written atomically
        with the delete (committed by get_db()). v1 POs carry no payments, so
        this is always a hard delete; line items cascade.

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
                    f"'{purchase_order.status}' status. Only DRAFT or CANCELED "
                    "purchase orders can be deleted; cancel a sent purchase "
                    "order instead."
                ),
                field="status",
            )

        try:
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

            logger.info(
                "Hard-deleted purchase order: %s",
                purchase_order.po_reference,
                extra={"po_id": str(po_id)},
            )
            return False

        except SQLAlchemyError as exc:
            logger.exception("Error deleting purchase order %s", po_id)
            raise DatabaseException("Failed to delete purchase order") from exc

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

    def _render_pdf(self, purchase_order: PurchaseOrder) -> bytes:
        """Render a PDF for an already-loaded purchase order.

        Orchestration (owner branding + ReportLab generator) is shared with
        invoices/quotes via DocumentPdfRenderer — no PO-specific copy.

        A rendering failure is not swallowed: it surfaces as an explicit 500
        carrying the message so the UI can show "PDF could not be
        generated. Please try again." instead of leaking a stack trace.
        """
        from app.common.pdf_renderer import DocumentPdfRenderer

        try:
            return DocumentPdfRenderer(self._db).render_purchase_order(purchase_order)
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

    def generate_pdf(self, po_id: uuid.UUID) -> bytes:
        """Generate the PDF for a purchase order by id."""
        return self._render_pdf(self.get_by_id(po_id))

    def generate_pdf_for_download(
        self, po_id: uuid.UUID
    ) -> tuple[bytes, PurchaseOrder]:
        """Generate the PO PDF and return it with the loaded purchase order.

        Loads the PO once and renders from it, so the download endpoint does
        not re-query just for the attachment filename (mirrors
        QuoteService.generate_pdf_for_download).
        """
        purchase_order = self.get_by_id(po_id)
        return self._render_pdf(purchase_order), purchase_order
