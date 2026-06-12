"""
Expense business logic — service layer.
"""

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar

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
    sum_line_totals,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.constants.enums import DocumentSource, ExpenseStatus
from app.modules.expenses.models import (
    Expense,
    ExpenseDocument,
    ExpenseLineItem,
    ExpensePayment,
)
from app.modules.expenses.queries import (
    ExpenseExportQuery,
    ExpenseStatisticsRepository,
    apply_expense_filters,
)
from app.modules.expenses.schemas import (
    ExpenseCalculationResponse,
    ExpenseCreate,
    ExpenseFilterParams,
    ExpenseLineItemCreate,
    ExpensePaymentCreate,
    ExpenseStatusCounts,
    ExpenseSummary,
    ExpenseUpdate,
)

EXPENSE_EAGER_LOAD_OPTIONS = (
    joinedload(Expense.vendor),
    joinedload(Expense.line_items),
    joinedload(Expense.payments),
    joinedload(Expense.documents),
)

logger = logging.getLogger(__name__)


class ExpenseService(BaseDocumentService):
    """Service layer for all expense operations.

    Shared state-machine and reference-retry mechanics come from
    BaseDocumentService; the transition table and reference formats below stay
    expense-specific. Expenses are vendor-facing and send no customer email,
    so the email mixin methods are unused here.
    """

    MAX_RETRIES = 3
    # Shared reference-retry wiring
    MAX_REFERENCE_RETRIES = MAX_RETRIES

    _document_noun = "expense"
    _reference_collision_markers = ("expense_number", "expense_reference")

    # Locked-load wiring (DocumentSendMixin._get_locked). Expenses send no
    # customer email, so the draft/sent statuses and send hooks stay unset;
    # only the shared FOR UPDATE row loader is used by the transition paths.
    _send_model = Expense

    ALLOWED_TRANSITIONS: ClassVar[dict[ExpenseStatus, list[ExpenseStatus]]] = {
        ExpenseStatus.PENDING: [
            ExpenseStatus.PAID,
            ExpenseStatus.OVERDUE,
            ExpenseStatus.CANCELED,
        ],
        ExpenseStatus.OVERDUE: [ExpenseStatus.PAID, ExpenseStatus.CANCELED],
        # PAID can still be voided (e.g. a mistaken settlement) -> CANCELED.
        ExpenseStatus.PAID: [ExpenseStatus.CANCELED],
        ExpenseStatus.CANCELED: [],  # terminal
    }

    def _generate_expense_number(self) -> str:
        """Generate a unique expense number via ReferenceGenerator."""
        return self._ref_gen().generate(
            model=Expense,
            column=Expense.expense_number,
            prefix="EXP",
            lock_key="expense_number",
            width=3,
            use_date_scope=True,
        )

    def _generate_expense_reference(self) -> str:
        """Generate a unique expense reference via ReferenceGenerator."""
        return self._ref_gen().generate(
            model=Expense,
            column=Expense.expense_reference,
            prefix="EXP",
            lock_key="expense_reference_gen",
            width=4,
            use_date_scope=False,
            use_max_strategy=True,
            strip_prefix_len=4,
        )

    @staticmethod
    def _build_line_items(
        raw_items: list[ExpenseLineItemCreate],
    ) -> list[dict]:
        """Delegate to the shared build_line_items helper in common/financial.py."""
        return build_line_items(raw_items)

    @staticmethod
    def _sum_line_totals(line_items_data: list[dict]) -> tuple[Decimal, Decimal]:
        """Delegate to the shared sum_line_totals helper in common/financial.py."""
        return sum_line_totals(line_items_data)

    # CREATE

    def create(
        self,
        data: ExpenseCreate,
        user_id: uuid.UUID | None = None,
    ) -> Expense:
        """
        Create a new expense record with line items.
        Initial status is always PENDING.
        Uses bounded retry for reference number collisions.
        """
        from app.modules.vendors.models import Vendor

        vendor = self._db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
        if not vendor:
            raise NotFoundException(
                detail=f"Vendor with ID '{data.vendor_id}' not found",
                resource="vendor",
            )

        # Single-currency-per-vendor (mirrors the customer rule on
        # invoices/quotes): vendor payables and statements sum balance_due
        # across this vendor's expenses, which is only meaningful in one
        # currency. Reject an *explicitly* mismatched currency; otherwise
        # pin to the vendor's currency so a schema default can never
        # silently drift it.
        if (
            "currency" in data.model_fields_set
            and vendor.currency
            and data.currency != vendor.currency
        ):
            raise BadRequestException(
                detail=(
                    f"Expense currency '{data.currency}' does not match the "
                    f"vendor's currency '{vendor.currency}'. A vendor can "
                    "only transact in a single currency."
                ),
                field="currency",
            )
        expense_currency = vendor.currency or data.currency

        # Calculate outside retry loop — deterministic, no DB writes
        line_items_data = self._build_line_items(data.line_items)
        subtotal, tax_total = self._sum_line_totals(line_items_data)
        total_due = subtotal + tax_total

        def _build() -> Expense:
            expense = Expense(
                expense_number=self._generate_expense_number(),
                expense_reference=self._generate_expense_reference(),
                vendor_id=data.vendor_id,
                expense_date=data.expense_date,
                due_date=data.due_date,
                currency=expense_currency,
                status=ExpenseStatus.PENDING,
                is_recurring=data.is_recurring,
                subtotal=subtotal,
                tax_total=tax_total,
                total_due=total_due,
                amount_paid=Decimal("0.00"),
                balance_due=total_due,
                notes=data.notes,
                created_by=user_id,
            )
            self._db.add(expense)
            self._db.flush()

            for item in line_items_data:
                self._db.add(ExpenseLineItem(expense_id=expense.id, **item))
            self._db.flush()
            return expense

        expense = self._with_reference_retry(_build, "expense")

        logger.info(
            "Created expense: %s",
            expense.expense_number,
            extra={
                "expense_id": str(expense.id),
                "vendor_id": str(data.vendor_id),
                "total_due": float(total_due),
                "created_by": str(user_id) if user_id else None,
            },
        )
        return expense

    # READ

    # Single source of truth for expense filtering, including
    # the CANCELED-visibility rule — module-level in queries.py so
    # ExpenseExportQuery shares the identical function.
    _apply_filters = staticmethod(apply_expense_filters)

    def get_by_id(self, expense_id: uuid.UUID) -> Expense:
        """Retrieve expense by UUID with all relationships eagerly loaded."""
        try:
            expense = (
                self._db.query(Expense)
                .options(*EXPENSE_EAGER_LOAD_OPTIONS)
                .filter(Expense.id == expense_id)
                .first()
            )
            if not expense:
                raise NotFoundException(
                    detail=f"Expense with ID '{expense_id}' not found",
                    resource="expense",
                )
            return expense

        except NotFoundException:
            raise
        except SQLAlchemyError as exc:
            logger.exception("Database error retrieving expense %s", expense_id)
            raise DatabaseException("Failed to retrieve expense") from exc

    def get_by_number(self, expense_number: str) -> Expense:
        """Retrieve expense by system-generated number."""
        try:
            expense = (
                self._db.query(Expense)
                .options(*EXPENSE_EAGER_LOAD_OPTIONS)
                .filter(Expense.expense_number == expense_number)
                .first()
            )
            if not expense:
                raise NotFoundException(
                    detail=f"Expense '{expense_number}' not found",
                    resource="expense",
                )
            return expense

        except NotFoundException:
            raise
        except SQLAlchemyError as exc:
            logger.exception("Database error retrieving expense %s", expense_number)
            raise DatabaseException("Failed to retrieve expense") from exc

    def list_expenses(
        self,
        params: PaginationParams,
        filters: ExpenseFilterParams | None = None,
    ) -> PaginatedResponse[ExpenseSummary]:
        """Paginated expense list with optional filtering and full-text search."""
        try:
            from app.modules.vendors.models import Vendor

            query = self._db.query(
                Expense.id,
                Expense.expense_number,
                Expense.expense_reference,
                Expense.vendor_id,
                Expense.expense_date,
                Expense.due_date,
                Expense.status,
                Expense.currency,
                Expense.total_due,
                Expense.balance_due,
                Expense.is_recurring,
                Expense.created_at,
                Vendor.vendor_name.label("vendor_name"),
            ).join(Vendor, Expense.vendor_id == Vendor.id)

            query = self._apply_filters(query, filters)

            # Count only when requested; over-fetch otherwise.
            total = query.count() if params.with_total else None

            rows = (
                query.order_by(Expense.created_at.desc())
                .offset(params.offset)
                .limit(params.fetch_limit)
                .all()
            )

            items = [
                ExpenseSummary(
                    id=row.id,
                    expense_number=row.expense_number,
                    expense_reference=row.expense_reference,
                    vendor_id=row.vendor_id,
                    vendor_name=row.vendor_name,
                    expense_date=row.expense_date,
                    due_date=row.due_date,
                    status=row.status,
                    currency=row.currency,
                    total_due=row.total_due,
                    balance_due=row.balance_due,
                    is_recurring=row.is_recurring,
                    created_at=row.created_at,
                )
                for row in rows
            ]

            logger.debug(
                "Listed expenses (page %d, total %s)",
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
            logger.exception("Database error listing expenses")
            raise DatabaseException("Failed to list expenses") from exc

    def list_for_export(
        self,
        filters: ExpenseFilterParams | None = None,
        include_line_items: bool = False,
        limit: int | None = None,
    ) -> list[Expense]:
        """Return full Expense ORM rows for Excel export, batch-loaded.

        Delegates to ExpenseExportQuery.
        """
        return ExpenseExportQuery(self._db).list(
            filters, include_line_items=include_line_items, limit=limit
        )

    def get_status_counts(self) -> ExpenseStatusCounts:
        """Status counts for the filter-tab bar.

        Delegates to ExpenseStatisticsRepository.
        """
        return ExpenseStatisticsRepository(self._db).status_counts()

    def get_statistics(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """Aggregated expense statistics for the dashboard.

        Delegates to ExpenseStatisticsRepository.
        """
        return ExpenseStatisticsRepository(self._db).statistics(date_from, date_to)

    # UPDATE

    def update(
        self,
        expense_id: uuid.UUID,
        data: ExpenseUpdate,
        expected_version: int | None = None,
    ) -> Expense:
        """
        Update an existing expense with optimistic locking.

        PENDING / OVERDUE: full editing allowed.
        PAID: read-only — raises BadRequestException.
        currency: always locked — not present in ExpenseUpdate schema.
        """
        expense = self.get_by_id(expense_id)

        if not expense.is_editable:
            raise BadRequestException(
                detail=(
                    f"Cannot edit expense in '{expense.status}' status. "
                    f"Only PENDING and OVERDUE expenses are editable."
                ),
                field="status",
            )

        # Atomic optimistic-lock guard: locks the row and compares the
        # version under the lock, replacing the non-atomic Python compare.
        assert_version(self._db, Expense, expense_id, expected_version)

        update_data = data.model_dump(exclude_unset=True, mode="python")

        if not update_data:
            return expense  # no-op — nothing to apply

        # Validate vendor change
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

        # Cross-field date validation
        # Pydantic validates only when both dates are in the payload.
        # Here we also guard the mixed case: one date in payload, one from DB.
        new_expense_date = update_data.get("expense_date", expense.expense_date)
        new_due_date = update_data.get("due_date", expense.due_date)
        if new_due_date < new_expense_date:
            raise BadRequestException(
                detail="due_date must be on or after expense_date",
                field="due_date",
            )

        # Replace line items
        if "line_items" in update_data:
            self._db.query(ExpenseLineItem).filter(
                ExpenseLineItem.expense_id == expense_id
            ).delete()

            # Convert raw dicts back to ExpenseLineItemCreate for type safety
            raw_items: list[dict] = update_data.pop("line_items")
            typed_items = [ExpenseLineItemCreate(**item) for item in raw_items]
            line_items_data = self._build_line_items(typed_items)

            subtotal, tax_total = self._sum_line_totals(line_items_data)
            total_due = subtotal + tax_total
            balance_due = total_due - expense.amount_paid

            if balance_due < Decimal("0.00"):
                raise BadRequestException(
                    detail=(
                        f"Cannot reduce total below the amount already paid "
                        f"({expense.currency} {expense.amount_paid}). "
                        f"Remove or reduce payments first."
                    ),
                    field="line_items",
                )

            for item in line_items_data:
                self._db.add(ExpenseLineItem(expense_id=expense.id, **item))

            update_data["subtotal"] = subtotal
            update_data["tax_total"] = tax_total
            update_data["total_due"] = total_due
            update_data["balance_due"] = balance_due

        # Apply scalar updates
        for field, value in update_data.items():
            setattr(expense, field, value)

        expense.version += 1
        self._db.flush()

        logger.info(
            "Updated expense: %s",
            expense.expense_number,
            extra={
                "expense_id": str(expense.id),
                "updated_fields": list(update_data.keys()),
                "new_version": expense.version,
            },
        )
        return expense

    # STATUS TRANSITIONS

    def _apply_payment(
        self,
        expense: Expense,
        amount: Decimal,
        payment_date,
        *,
        reference: str | None = None,
        notes: str | None = None,
        document_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        paid_at: datetime | None = None,
    ) -> ExpensePayment:
        """Apply a payment to an already-locked expense and settle if covered.

        Single source of truth for both record_payment and mark_as_paid:
        creates the audit-trail ExpensePayment, advances amount_paid /
        balance_due, transitions to PAID when the balance is cleared, and
        bumps ``version`` exactly once. The caller must have already loaded
        ``expense`` with ``with_for_update()`` and validated status/amount.
        """
        payment = ExpensePayment(
            expense_id=expense.id,
            amount=amount,
            payment_date=payment_date,
            reference=reference,
            notes=notes,
            document_id=document_id,
            recorded_by=user_id,
        )
        self._db.add(payment)

        expense.amount_paid += amount
        expense.balance_due = expense.total_due - expense.amount_paid

        if expense.balance_due <= Decimal("0.00"):
            # Full settlement. Set status directly (not via _transition) so the
            # single version bump below is the only increment for this op.
            expense.status = ExpenseStatus.PAID
            expense.balance_due = Decimal("0.00")
            expense.paid_at = paid_at or datetime.now(UTC)

        expense.version += 1
        self._db.flush()

        # Durable audit trail: commits atomically with the payment.
        record_audit_event(
            self._db,
            actor_id=user_id,
            entity_type="expense",
            entity_id=expense.id,
            action="payment_recorded",
            after={
                "payment_id": str(payment.id),
                "amount": str(amount),
                "new_balance": str(expense.balance_due),
                "new_status": status_value(expense.status),
            },
        )
        return payment

    def mark_as_paid(
        self,
        expense_id: uuid.UUID,
        paid_at: datetime | None = None,
    ) -> Expense:
        """
        Quick-action: set status to PAID with a full audit trail.

        Creates an ExpensePayment record for the remaining balance_due
        so that the audit trail is complete

        Uses SELECT FOR UPDATE to prevent TOCTOU race with concurrent
        record_payment() calls on the same expense row.
        """
        # Lock the bare expenses row. Expense.vendor is lazy="joined", which
        # would otherwise emit a LEFT OUTER JOIN and make Postgres reject
        # FOR UPDATE ("cannot be applied to the nullable side of an outer
        # join"). lazyload("*") suppresses the eager join; relationships load
        # lazily on access afterward.
        expense = (
            self._db.query(Expense)
            .options(lazyload("*"))
            .filter(Expense.id == expense_id)
            .with_for_update()
            .first()
        )
        if not expense:
            raise NotFoundException(
                detail=f"Expense '{expense_id}' not found",
                resource="expense",
            )

        if expense.status == ExpenseStatus.PAID:
            raise BadRequestException(
                detail="Expense is already paid",
                field="status",
            )

        now = paid_at or datetime.now(UTC)
        settlement_amount = expense.balance_due

        self._apply_payment(
            expense,
            amount=settlement_amount,
            payment_date=now.date() if isinstance(now, datetime) else now,
            reference=f"AUTO-SETTLE-{expense.expense_reference}",
            notes="Full settlement via mark_as_paid quick action",
            paid_at=now,
        )

        logger.info(
            "Marked expense as paid: %s",
            expense.expense_reference,
            extra={
                "expense_id": str(expense.id),
                "paid_at": str(expense.paid_at),
                "payment_amount": str(settlement_amount),
            },
        )
        return expense

    # PAYMENT RECORDING

    def record_payment(
        self,
        expense_id: uuid.UUID,
        data: ExpensePaymentCreate,
        user_id: uuid.UUID | None = None,
        document_id: uuid.UUID | None = None,
    ) -> ExpensePayment:
        """
        Record an auditable payment against an expense.

        Status after payment:
          amount_paid >= total_due → PAID (expense.paid_at set)
          amount_paid <  total_due → status unchanged (PENDING or OVERDUE)
        """
        # Lock the bare expenses row (see mark_as_paid): suppress the
        # lazy="joined" vendor outer join so Postgres allows FOR UPDATE.
        expense = (
            self._db.query(Expense)
            .options(lazyload("*"))
            .filter(Expense.id == expense_id)
            .with_for_update()
            .first()
        )
        if not expense:
            raise NotFoundException(
                detail=f"Expense '{expense_id}' not found",
                resource="expense",
            )

        if expense.status == ExpenseStatus.PAID:
            raise BadRequestException(
                detail="Cannot record payment for an already-paid expense",
                field="status",
            )

        if data.amount > expense.balance_due:
            raise BadRequestException(
                detail=(
                    f"Payment amount ({data.amount}) exceeds balance due "
                    f"({expense.balance_due}). Cannot overpay an expense."
                ),
                field="amount",
            )

        payment = self._apply_payment(
            expense,
            amount=data.amount,
            payment_date=data.payment_date,
            reference=data.reference,
            notes=data.notes,
            document_id=document_id,
            user_id=user_id,
        )

        logger.info(
            "Recorded payment for expense %s",
            expense.expense_reference,
            extra={
                "expense_id": str(expense.id),
                "payment_id": str(payment.id),
                "amount": float(data.amount),
                "new_balance": float(expense.balance_due),
                "new_status": expense.status,
            },
        )
        return payment

    # DOCUMENT MANAGEMENT

    def attach_document(
        self,
        expense_id: uuid.UUID,
        filename: str,
        file_size_bytes: int,
        mime_type: str,
        storage_key: str,
        source: DocumentSource = DocumentSource.FORM,
        user_id: uuid.UUID | None = None,
        storage: Any | None = None,
    ) -> ExpenseDocument:
        """
        Persist document metadata after the file has been written to storage.

        The router is responsible for the storage write and must call this
        method only after the storage write succeeds, preventing orphaned
        DB records for files that never landed in storage.

        IThe freshly written object is registered for
        delete-on-rollback (shared ``storage_tx`` utility), so if this
        insert — or the outer request commit — fails, the object is removed
        from storage instead of being orphaned. ``storage`` is injectable
        for tests and defaults to the process storage facade.
        """
        from app.common.storage_tx import schedule_delete_on_rollback
        from app.lib.storage import storage_service

        schedule_delete_on_rollback(self._db, storage or storage_service, storage_key)

        # Confirm expense exists — raises NotFoundException if not
        self.get_by_id(expense_id)

        document = ExpenseDocument(
            expense_id=expense_id,
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
            "Attached document to expense %s: %s",
            expense_id,
            filename,
            extra={
                "expense_id": str(expense_id),
                "document_id": str(document.id),
                "source": source.value,
                "size_bytes": file_size_bytes,
            },
        )
        return document

    def get_document(
        self,
        expense_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> ExpenseDocument:
        """Fetch a single document record for download URL generation."""
        document = (
            self._db.query(ExpenseDocument)
            .filter(
                ExpenseDocument.id == document_id,
                ExpenseDocument.expense_id == expense_id,
            )
            .first()
        )
        if not document:
            raise NotFoundException(
                detail=(
                    f"Document '{document_id}' not found on expense '{expense_id}'"
                ),
                resource="expense_document",
            )
        return document

    def delete_document(
        self,
        expense_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> str:
        """
        Remove document metadata and return the storage_key.

        Return type is str — the router uses the returned storage_key
        to delete the object from storage after the DB record is removed.
        The two-step design ensures we don't lose the key before deletion.
        """
        document = self.get_document(expense_id, document_id)

        storage_key = document.storage_key

        self._db.delete(document)
        self._db.flush()

        logger.info(
            "Deleted document %s from expense %s",
            document_id,
            expense_id,
            extra={
                "expense_id": str(expense_id),
                "document_id": str(document_id),
                "storage_key": storage_key,
            },
        )
        return storage_key  # caller deletes from object storage

    # DELETE

    def cancel(self, expense_id: uuid.UUID) -> Expense:
        """Cancel (void) an expense — terminal CANCELED state.

        Routes through the state machine so ALLOWED_TRANSITIONS is enforced
        and the version bump is owned in one place. Idempotency is rejected:
        an already-CANCELED expense raises rather than transitioning again.

        Locked load: serializes with record_payment / mark_as_paid so the
        status check cannot act on a stale row (race-condition contract:
        all status transitions load FOR UPDATE).
        """
        expense = self._get_locked(expense_id)

        if expense.status == ExpenseStatus.CANCELED:
            raise BadRequestException(
                detail="Expense is already canceled", field="status"
            )

        previous_status = expense.status
        # _transition validates PENDING/OVERDUE/PAID -> CANCELED and bumps
        # version exactly once.
        self._transition(expense, ExpenseStatus.CANCELED)
        self._db.flush()

        # Durable audit trail .
        record_audit_event(
            self._db,
            actor_id=self._actor_id,
            entity_type="expense",
            entity_id=expense.id,
            action="canceled",
            before={"status": status_value(previous_status)},
            after={"status": status_value(ExpenseStatus.CANCELED)},
        )

        logger.warning(
            "Canceled expense: %s",
            expense.expense_reference,
            extra={"expense_id": str(expense.id)},
        )
        return expense

    def delete(self, expense_id: uuid.UUID) -> bool:
        """
        Delete an expense.

        If the expense has payments, it is soft-deleted by transitioning to
        the terminal CANCELED state (preserving the payment audit trail).
        Otherwise, it is hard-deleted.
        Returns True if the expense was soft-deleted so the router can
        surface the augmented warning text.

        Locked load: serializes with record_payment / mark_as_paid so the
        had_payments check and the soft/hard-delete decision cannot act on
        a stale row (race-condition contract: all status transitions load
        FOR UPDATE). payments/documents load lazily off the locked row.
        """
        expense = self._get_locked(expense_id)
        had_payments = bool(expense.payments)
        previous_status = expense.status

        try:
            if had_payments:
                # Soft-delete via the state machine (CANCELED is first-class),
                # not a bare status assignment, so the transition is validated
                # and the version bump happens exactly once.
                self._transition(expense, ExpenseStatus.CANCELED)
                record_audit_event(
                    self._db,
                    actor_id=self._actor_id,
                    entity_type="expense",
                    entity_id=expense.id,
                    action="soft_deleted",
                    before={"status": status_value(previous_status)},
                    after={"status": status_value(ExpenseStatus.CANCELED)},
                )
                logger.warning(
                    "Soft-deleted expense (had payments): %s",
                    expense.expense_reference,
                    extra={
                        "expense_id": str(expense.id),
                        "had_payments": True,
                    },
                )
            else:
                # Capture storage keys before the cascading DB delete so we
                # can purge the underlying files and avoid orphans.
                storage_keys = [doc.storage_key for doc in expense.documents]

                record_audit_event(
                    self._db,
                    actor_id=self._actor_id,
                    entity_type="expense",
                    entity_id=expense.id,
                    action="hard_deleted",
                    before={
                        "status": status_value(previous_status),
                        "expense_number": expense.expense_number,
                        "total_due": str(expense.total_due),
                    },
                )
                self._db.delete(expense)
                self._db.flush()

                self._purge_storage_objects(storage_keys)

                logger.info(
                    "Hard-deleted expense: %s",
                    expense.expense_reference,
                    extra={
                        "expense_id": str(expense.id),
                        "had_payments": False,
                        "purged_objects": len(storage_keys),
                    },
                )
                return had_payments

            self._db.flush()
            return had_payments

        except SQLAlchemyError as exc:
            logger.exception("Error deleting expense %s", expense_id)
            raise DatabaseException("Failed to delete expense") from exc

    @staticmethod
    def _purge_storage_objects(storage_keys: list[str]) -> None:
        """Best-effort delete of stored objects after a hard delete.

        Storage failures are logged, not raised: the DB record is already
        gone, so a missed file is a reconciliation concern, not a request
        failure. A periodic sweep should reconcile any stragglers.
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

    # DUPLICATE

    def duplicate(
        self,
        expense_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> Expense:
        """
        Duplicate an expense as a new PENDING record.

        Copies: vendor, currency, line items, is_recurring, notes.
        Resets: expense_date = today, due_date = today + 30 days.
        Excludes: payments, documents (not carried over to duplicates).
        Uses bounded retry for reference collisions.
        """
        try:
            from datetime import timedelta

            original = self.get_by_id(expense_id)
            new_expense_date = date.today()
            new_due_date = new_expense_date + timedelta(days=30)

            def _build() -> Expense:
                duplicate = Expense(
                    expense_number=self._generate_expense_number(),
                    expense_reference=self._generate_expense_reference(),
                    vendor_id=original.vendor_id,
                    expense_date=new_expense_date,
                    due_date=new_due_date,
                    currency=original.currency,
                    status=ExpenseStatus.PENDING,
                    is_recurring=original.is_recurring,
                    subtotal=original.subtotal,
                    tax_total=original.tax_total,
                    total_due=original.total_due,
                    amount_paid=Decimal("0.00"),
                    balance_due=original.total_due,
                    notes=original.notes,
                    created_by=user_id,
                )
                self._db.add(duplicate)
                self._db.flush()

                for orig_item in original.line_items:
                    self._db.add(
                        ExpenseLineItem(
                            expense_id=duplicate.id,
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
                    "Duplicated expense %s → %s",
                    original.expense_number,
                    duplicate.expense_number,
                    extra={
                        "original_id": str(original.id),
                        "duplicate_id": str(duplicate.id),
                    },
                )
                return duplicate

            return self._with_reference_retry(_build, "expense")

        except NotFoundException:
            raise
        except SQLAlchemyError as exc:
            logger.exception("Error duplicating expense %s", expense_id)
            raise DatabaseException("Failed to duplicate expense") from exc

    # CALCULATION PREVIEW

    @classmethod
    def calculate_totals(
        cls,
        line_items: list[ExpenseLineItemCreate],
    ) -> ExpenseCalculationResponse:
        """
        Calculate totals without persisting — live preview endpoint.

        No discount in v1: total_due = subtotal + tax_total.
        """
        calculated_items = cls._build_line_items(line_items)
        subtotal, tax_total = cls._sum_line_totals(calculated_items)

        # Convert Decimals to floats for the response dict structure required
        formatted_items = []
        for item in calculated_items:
            formatted_items.append(
                {
                    "item_name": item["item_name"],
                    "description": item["description"],
                    "quantity": float(item["quantity"]),
                    "unit_price": float(item["unit_price"]),
                    "line_total": float(item["line_total"]),
                    "tax_type": item["tax_type"],
                    "tax_amount": float(item["tax_amount"]),
                }
            )

        return ExpenseCalculationResponse(
            subtotal=subtotal,
            tax_total=tax_total,
            total_due=subtotal + tax_total,
            line_items=formatted_items,
        )

    # NIGHTLY SCHEDULER

    def bulk_transition_overdue(self) -> int:
        """
        Bulk-update PENDING expenses past their due_date to OVERDUE.

        Called by the nightly scheduler.
        Uses synchronize_session=False for efficiency — avoids loading
        every matched row into Python memory.
        Explicitly expires all instances so subsequent reads reflect the
        updated status.

        Deliberate exception to the locked-load transition contract: this
        is a single atomic UPDATE whose WHERE clause re-checks eligibility
        row-by-row, so no FOR UPDATE pre-read is needed.

        Returns count of updated rows.
        """
        try:
            today = date.today()

            updated = (
                self._db.query(Expense)
                .filter(
                    Expense.status == ExpenseStatus.PENDING,
                    Expense.due_date < today,
                )
                .update(
                    {
                        Expense.status: ExpenseStatus.OVERDUE,
                        Expense.version: Expense.version + 1,
                    },
                    synchronize_session=False,
                )
            )
            self._db.flush()
            self._db.expire_all()

            logger.info(
                "Bulk transitioned %d expenses to OVERDUE",
                updated,
                extra={"count": updated, "as_of": str(today)},
            )
            return updated

        except SQLAlchemyError as exc:
            logger.exception("Error in bulk overdue transition")
            raise DatabaseException("Failed to transition overdue expenses") from exc
