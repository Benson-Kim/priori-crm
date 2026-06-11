"""
Expense business logic — service layer.
"""

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import and_, case, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload, lazyload, selectinload

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
    sum_line_totals,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.search import build_search_clause
from app.constants.enums import DocumentSource, ExpenseStatus
from app.modules.expenses.models import (
    Expense,
    ExpenseDocument,
    ExpenseLineItem,
    ExpensePayment,
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

    _document_noun = "expense"
    _reference_collision_markers = ("expense_number", "expense_reference")

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

        # Calculate outside retry loop — deterministic, no DB writes
        line_items_data = self._build_line_items(data.line_items)
        subtotal, tax_total = self._sum_line_totals(line_items_data)
        total_due = subtotal + tax_total

        last_error: Exception | None = None

        for attempt in range(self.MAX_RETRIES + 1):
            sp = self._db.begin_nested()
            try:
                expense = Expense(
                    expense_number=self._generate_expense_number(),
                    expense_reference=self._generate_expense_reference(),
                    vendor_id=data.vendor_id,
                    expense_date=data.expense_date,
                    due_date=data.due_date,
                    currency=data.currency,
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

                sp.commit()

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

            except IntegrityError as exc:
                sp.rollback()
                last_error = exc
                is_collision = self._is_reference_collision(exc)
                if is_collision and attempt < self.MAX_RETRIES:
                    logger.warning(
                        "Expense reference collision — retry %d", attempt + 1
                    )
                    continue
                raise ConflictException(
                    "Expense data violates database constraints"
                ) from exc

        raise ConflictException(
            "Failed to generate unique expense reference after retries"
        ) from last_error

    # READ

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

            # CANCELED is hidden unless explicitly requested via status filter,
            # so the default list shows only live expenses.
            if filters and filters.status:
                query = query.filter(Expense.status == filters.status)
            else:
                query = query.filter(Expense.status != ExpenseStatus.CANCELED)

            if filters:
                if filters.vendor_id:
                    query = query.filter(Expense.vendor_id == filters.vendor_id)

                if filters.date_from:
                    query = query.filter(Expense.expense_date >= filters.date_from)

                if filters.date_to:
                    query = query.filter(Expense.expense_date <= filters.date_to)

                if filters.due_date_from:
                    query = query.filter(Expense.due_date >= filters.due_date_from)

                if filters.due_date_to:
                    query = query.filter(Expense.due_date <= filters.due_date_to)

                if filters.is_recurring is not None:
                    query = query.filter(Expense.is_recurring == filters.is_recurring)

                if filters.search:
                    search_clause = build_search_clause(
                        filters.search,
                        Expense.expense_number,
                        Expense.expense_reference,
                        Vendor.vendor_name,
                    )
                    if search_clause is not None:
                        query = query.filter(search_clause)

            total = query.count()

            rows = (
                query.order_by(Expense.created_at.desc())
                .offset(params.offset)
                .limit(params.per_page)
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
                "Listed %d expenses (page %d, total %d)",
                len(items),
                params.page,
                total,
                extra={
                    "page": params.page,
                    "per_page": params.per_page,
                    "total": total,
                    "filters": filters.model_dump() if filters else None,
                },
            )
            return PaginatedResponse.create(items=items, total=total, params=params)

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

        Replaces the per-row ``get_by_id`` N+1 in the export endpoint:
        vendor eager-joined for the display name; line items
        via ``selectinload`` only when requested. ``limit`` caps rows
        materialised in memory.
        """
        try:
            from app.modules.vendors.models import Vendor

            query = (
                self._db.query(Expense)
                .join(Vendor, Expense.vendor_id == Vendor.id)
                .options(joinedload(Expense.vendor))
            )

            if include_line_items:
                query = query.options(selectinload(Expense.line_items))

            if filters:
                if filters.status:
                    query = query.filter(Expense.status == filters.status)
                else:
                    query = query.filter(Expense.status != ExpenseStatus.CANCELED)
                if filters.vendor_id:
                    query = query.filter(Expense.vendor_id == filters.vendor_id)
                if filters.date_from:
                    query = query.filter(Expense.expense_date >= filters.date_from)
                if filters.date_to:
                    query = query.filter(Expense.expense_date <= filters.date_to)
                if filters.due_date_from:
                    query = query.filter(Expense.due_date >= filters.due_date_from)
                if filters.due_date_to:
                    query = query.filter(Expense.due_date <= filters.due_date_to)
                if filters.is_recurring is not None:
                    query = query.filter(Expense.is_recurring == filters.is_recurring)
                if filters.search:
                    from app.common.search import build_search_clause

                    search_clause = build_search_clause(
                        filters.search,
                        Expense.expense_number,
                        Expense.expense_reference,
                        Vendor.vendor_name,
                    )
                    if search_clause is not None:
                        query = query.filter(search_clause)
            else:
                query = query.filter(Expense.status != ExpenseStatus.CANCELED)

            query = query.order_by(Expense.created_at.desc())
            if limit is not None:
                query = query.limit(limit)

            return query.all()

        except SQLAlchemyError as exc:
            logger.exception("Database error loading expenses for export")
            raise DatabaseException("Failed to load expenses for export") from exc

    def get_status_counts(self) -> ExpenseStatusCounts:
        """
        Single-query status counts for the filter-tab bar.

        The overdue bucket captures both:
        - Rows already stored as OVERDUE
        - PENDING rows whose due_date has passed but the nightly job
          has not yet run (computed via SQL CASE — no second round-trip).
        """
        try:
            today = date.today()

            rows = (
                self._db.query(
                    Expense.status,
                    func.count(Expense.id).label("cnt"),
                    func.sum(
                        case(
                            (
                                and_(
                                    Expense.status == ExpenseStatus.PENDING,
                                    Expense.due_date < today,
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("pending_past_due_cnt"),
                )
                .group_by(Expense.status)
                .all()
            )

            counts: dict[str, int] = {}
            pending_past_due = 0
            # `all` and the active-status tabs cover live expenses only;
            # CANCELED is surfaced via its own count, not folded into `all`.
            total = 0

            for status_val, cnt, ppd_cnt in rows:
                counts[status_val] = cnt
                if status_val != ExpenseStatus.CANCELED:
                    total += cnt
                pending_past_due += ppd_cnt or 0

            stored_overdue = counts.get(ExpenseStatus.OVERDUE, 0)
            displayed_overdue = stored_overdue + pending_past_due

            return ExpenseStatusCounts(
                all=total,
                pending=counts.get(ExpenseStatus.PENDING, 0),
                paid=counts.get(ExpenseStatus.PAID, 0),
                overdue=displayed_overdue,
                canceled=counts.get(ExpenseStatus.CANCELED, 0),
            )

        except SQLAlchemyError as exc:
            logger.exception("Database error getting expense status counts")
            raise DatabaseException("Failed to get status counts") from exc

    def get_statistics(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Any]:
        """
        Aggregated expense statistics — all computation pushed to the DB.
        Defaults to the current calendar month when no range is supplied.
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
                    func.count(Expense.id).label("total_expenses"),
                    func.coalesce(func.sum(Expense.total_due), Decimal("0")).label(
                        "total_amount"
                    ),
                    func.coalesce(func.sum(Expense.amount_paid), Decimal("0")).label(
                        "total_paid"
                    ),
                    func.coalesce(func.sum(Expense.balance_due), Decimal("0")).label(
                        "total_outstanding"
                    ),
                    func.count(
                        case(
                            (
                                and_(
                                    Expense.status.in_(
                                        [ExpenseStatus.PENDING, ExpenseStatus.OVERDUE]
                                    ),
                                    Expense.due_date < today,
                                    Expense.balance_due > 0,
                                ),
                                Expense.id,
                            )
                        )
                    ).label("overdue_count"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    and_(
                                        Expense.status.in_(
                                            [
                                                ExpenseStatus.PENDING,
                                                ExpenseStatus.OVERDUE,
                                            ]
                                        ),
                                        Expense.due_date < today,
                                        Expense.balance_due > 0,
                                    ),
                                    Expense.balance_due,
                                ),
                                else_=0,
                            )
                        ),
                        Decimal("0"),
                    ).label("overdue_amount"),
                    func.coalesce(
                        func.avg(
                            case(
                                (
                                    and_(
                                        Expense.status == ExpenseStatus.PAID,
                                        Expense.paid_at.isnot(None),
                                    ),
                                    func.extract(
                                        "epoch",
                                        Expense.paid_at
                                        - func.cast(
                                            Expense.expense_date,
                                            type_=Expense.paid_at.type,
                                        ),
                                    )
                                    / 86400,
                                )
                            )
                        ),
                        0,
                    ).label("avg_days_to_payment"),
                )
                .filter(
                    Expense.status != ExpenseStatus.CANCELED,
                    Expense.expense_date >= date_from if date_from else True,
                    Expense.expense_date <= date_to if date_to else True,
                )
                .one()
            )

            total = agg.total_expenses or 0
            avg_value = (
                Decimal(str(agg.total_amount)) / total if total > 0 else Decimal("0.00")
            )

            logger.debug(
                "Calculated expense statistics",
                extra={
                    "date_from": str(date_from),
                    "date_to": str(date_to),
                    "total": total,
                },
            )

            return {
                "total_expenses": total,
                "total_amount": Decimal(str(agg.total_amount)),
                "total_paid": Decimal(str(agg.total_paid)),
                "total_outstanding": Decimal(str(agg.total_outstanding)),
                "overdue_count": agg.overdue_count or 0,
                "overdue_amount": Decimal(str(agg.overdue_amount)),
                "average_expense_value": avg_value,
                "average_days_to_payment": round(
                    float(agg.avg_days_to_payment or 0), 1
                ),
                "date_from": date_from,
                "date_to": date_to,
            }

        except SQLAlchemyError as exc:
            logger.exception("Database error calculating expense statistics")
            raise DatabaseException("Failed to calculate statistics") from exc

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
    ) -> ExpenseDocument:
        """
        Persist document metadata after the file has been written to storage.

        The router is responsible for the storage write and must call this
        method only after the storage write succeeds, preventing orphaned
        DB records for files that never landed in storage.
        """
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
        """
        expense = self.get_by_id(expense_id)

        if expense.status == ExpenseStatus.CANCELED:
            raise BadRequestException(
                detail="Expense is already canceled", field="status"
            )

        # _transition validates PENDING/OVERDUE/PAID -> CANCELED and bumps
        # version exactly once.
        self._transition(expense, ExpenseStatus.CANCELED)
        self._db.flush()

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
        """
        expense = self.get_by_id(expense_id)
        had_payments = bool(expense.payments)

        try:
            if had_payments:
                # Soft-delete via the state machine (CANCELED is first-class),
                # not a bare status assignment, so the transition is validated
                # and the version bump happens exactly once.
                self._transition(expense, ExpenseStatus.CANCELED)
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
            last_error: Exception | None = None

            for attempt in range(self.MAX_RETRIES + 1):
                sp = self._db.begin_nested()
                try:
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
                    sp.commit()

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

                except IntegrityError as exc:
                    sp.rollback()
                    last_error = exc
                    is_collision = self._is_reference_collision(exc)
                    if is_collision and attempt < self.MAX_RETRIES:
                        logger.warning(
                            "Collision during duplicate — retry %d", attempt + 1
                        )
                        continue
                    raise ConflictException(
                        "Expense data violates database constraints"
                    ) from exc

            raise ConflictException(
                "Failed to generate unique expense reference after retries"
            ) from last_error

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
