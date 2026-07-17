"""
Vendor business logic.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import case, func, literal_column, null
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.common.audit import record_audit_event
from app.common.database import assert_version
from app.common.document_service import ServiceBase, StateMachineMixin
from app.common.exceptions import (
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
from app.constants.enums import (
    Currency,
    ExpenseStatus,
    PurchaseOrderStatus,
    VendorStatus,
)
from app.modules.vendors.models import Vendor
from app.modules.vendors.schemas import (
    ContactSearchResponse,
    ContactSearchResult,
    VendorCardItem,
    VendorCardSummary,
    VendorCreate,
    VendorFilterParams,
    VendorPayablesSummary,
    VendorStatement,
    VendorStatementSummary,
    VendorStatementTransaction,
    VendorStatusCounts,
    VendorSummary,
    VendorTransactionFilterParams,
    VendorTransactionSummary,
    VendorUpdate,
)

logger = logging.getLogger(__name__)

# Single source of truth for which expense/bill statuses count as an open
# payable. Derived from ExpenseStatus so it can never drift back to the
# fictional "partial"/"sent" values: a payable is open unless it
# has been PAID or CANCELED.
OPEN_PAYABLE_STATUSES: tuple[str, ...] = (
    ExpenseStatus.PENDING,
    ExpenseStatus.OVERDUE,
)
OVERDUE_PAYABLE_STATUS: str = ExpenseStatus.OVERDUE
CLOSED_PAYABLE_STATUSES: tuple[str, ...] = (
    ExpenseStatus.PAID,
    ExpenseStatus.CANCELED,
)

# Detail-card status filters. Each card collapses lifecycle statuses into a
# paid/pending split, so we exclude the statuses that are neither: a PO card
# omits DRAFT (not yet a commitment — "no draft/sent" surfaced separately),
# and a bill card omits CANCELED (voided).
CARD_PO_STATUSES: tuple[str, ...] = (
    PurchaseOrderStatus.SENT,
    PurchaseOrderStatus.PAID,
)
CARD_BILL_STATUSES: tuple[str, ...] = (
    ExpenseStatus.PENDING,
    ExpenseStatus.OVERDUE,
    ExpenseStatus.PAID,
)


class VendorService(StateMachineMixin, ServiceBase):
    """
    Service layer for all vendor operations.

    Status transitions go through the shared StateMachineMixin._transition
    instead of a hand-rolled copy; the constructor and the
    public actor surface come from ServiceBase.
    """

    _document_noun = "vendor"

    ALLOWED_TRANSITIONS: ClassVar[dict[str, list[str]]] = {
        VendorStatus.ACTIVE: [VendorStatus.INACTIVE],
        VendorStatus.INACTIVE: [VendorStatus.ACTIVE],
    }

    # Internal Helpers

    def _get_vendor_or_404(self, vendor_id: uuid.UUID) -> Vendor:
        """
        Fetch a vendor by primary key.
        """
        vendor = self._db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if not vendor:
            raise NotFoundException(
                detail=f"Vendor with ID '{vendor_id}' not found",
                resource="vendor",
            )
        return vendor

    def _compute_payables_for_vendor(self, vendor_id: uuid.UUID) -> dict[str, Decimal]:
        """Compute total_unpaid and overdue_total for a single vendor."""
        from app.modules.expenses.models import Expense

        try:
            expense_rows = self._db.query(
                Expense.balance_due.label("balance"),
                Expense.status.label("status"),
            ).filter(Expense.vendor_id == vendor_id)
            union_q = expense_rows.subquery()

            row = self._db.query(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                union_q.c.status.in_(OPEN_PAYABLE_STATUSES),
                                union_q.c.balance,
                            ),
                            else_=Decimal("0.00"),
                        )
                    ),
                    Decimal("0.00"),
                ).label("total_unpaid"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                union_q.c.status == OVERDUE_PAYABLE_STATUS,
                                union_q.c.balance,
                            ),
                            else_=Decimal("0.00"),
                        )
                    ),
                    Decimal("0.00"),
                ).label("overdue_total"),
            ).one()

            return {
                "total_unpaid": Decimal(str(row.total_unpaid)),
                "overdue_total": Decimal(str(row.overdue_total)),
            }

        except SQLAlchemyError as e:
            logger.exception("Error computing payables for vendor %s", vendor_id)
            raise DatabaseException("Failed to compute vendor payables") from e

    def _compute_payables_bulk(
        self, vendor_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Decimal]:
        """
        Compute total_unpaid (payables) for a list of vendors.
        """
        if not vendor_ids:
            return {}

        from app.modules.expenses.models import Expense

        try:
            expense_rows = self._db.query(
                Expense.vendor_id.label("vendor_id"),
                Expense.balance_due.label("balance"),
                Expense.status.label("status"),
            ).filter(
                Expense.vendor_id.in_(vendor_ids),
                Expense.status.in_(OPEN_PAYABLE_STATUSES),
            )
            union_q = expense_rows.subquery()

            rows = (
                self._db.query(
                    union_q.c.vendor_id,
                    func.coalesce(func.sum(union_q.c.balance), Decimal("0.00")).label(
                        "total_unpaid"
                    ),
                )
                .group_by(union_q.c.vendor_id)
                .all()
            )

            return {row.vendor_id: Decimal(str(row.total_unpaid)) for row in rows}

        except SQLAlchemyError as e:
            logger.exception("Error computing bulk payables")
            raise DatabaseException("Failed to compute vendor payables") from e

    def _check_duplicate_email(
        self,
        email: str | None,
        exclude_vendor_id: uuid.UUID | None = None,
    ) -> Vendor | None:
        """
        Return an existing vendor with the same email, or None.
        """
        if not email:
            return None
        query = self._db.query(Vendor).filter(Vendor.email == email.lower().strip())
        if exclude_vendor_id:
            query = query.filter(Vendor.id != exclude_vendor_id)
        return query.first()

    def _has_open_transactions(self, vendor_id: uuid.UUID) -> bool:
        """
        Return True if the vendor has any pending or overdue transactions.

        """
        from app.modules.expenses.models import Expense

        try:
            expense_count = (
                self._db.query(func.count(Expense.id))
                .filter(
                    Expense.vendor_id == vendor_id,
                    Expense.status.in_(OPEN_PAYABLE_STATUSES),
                )
                .scalar()
            ) or 0

            return expense_count > 0

        except SQLAlchemyError as e:
            logger.exception(
                "Error checking open transactions for vendor %s", vendor_id
            )
            raise DatabaseException("Failed to check vendor transactions") from e

    def _attach_payables(
        self,
        vendor: Vendor,
        payables: dict[str, Decimal] | None = None,
    ) -> Vendor:
        """
        Dynamically attach computed payables to the ORM model for serialization
        by the router's VendorResponse schema.
        """
        if payables is None:
            payables = self._compute_payables_for_vendor(vendor.id)

        vendor.payables = payables["total_unpaid"]
        vendor.total_unpaid = payables["total_unpaid"]
        vendor.overdue_total = payables["overdue_total"]
        return vendor

    # CREATE

    def create(
        self,
        data: VendorCreate,
        user_id: uuid.UUID | None = None,
    ) -> Vendor:
        """
        Create a new vendor record.
        """
        # Duplicate email guard
        if data.email:
            existing = self._check_duplicate_email(data.email)
            if existing:
                raise ConflictException(
                    detail=(
                        f"A vendor with this email already exists: "
                        f"'{existing.vendor_name}'."
                    ),
                )

        # If contact_id is provided, verify the contact exists
        if data.contact_id:
            self._verify_contact_exists(data.contact_id)

        try:
            vendor = Vendor(
                vendor_name=data.vendor_name.strip(),
                address=data.address,
                email=data.email if data.email else None,
                country=data.country,
                phone_primary=data.phone_primary,
                phone_secondary=data.phone_secondary,
                website=data.website,
                currency=data.currency.upper() if data.currency else Currency.KES,
                vat_number=data.vat_number,
                tax_id_pin=data.tax_id_pin,
                notes=data.notes,
                status=VendorStatus.ACTIVE,
                contact_id=data.contact_id,
                created_by=user_id,
                version=1,
            )
            self._db.add(vendor)
            self._db.flush()

            logger.info(
                "Created vendor: %s",
                vendor.vendor_name,
                extra={
                    "vendor_id": str(vendor.id),
                    "status": vendor.status,
                    "contact_id": str(data.contact_id) if data.contact_id else None,
                    "created_by": str(user_id) if user_id else None,
                    "creation_path": "from_existing_contact"
                    if data.contact_id
                    else "new",
                },
            )

            return self._attach_payables(vendor)

        except IntegrityError as e:
            self._db.rollback()
            if "uq_vendors_email_not_null" in str(e.orig):
                raise ConflictException(
                    "A vendor with this email already exists."
                ) from e
            raise ConflictException("Vendor data violates database constraints.") from e
        except SQLAlchemyError as e:
            logger.exception("Database error creating vendor")
            raise DatabaseException("Failed to create vendor") from e

    # READ

    def get_by_id(self, vendor_id: uuid.UUID) -> Vendor:
        """
        Retrieve a single vendor
        """
        try:
            vendor = self._get_vendor_or_404(vendor_id)
            return self._attach_payables(vendor)
        except NotFoundException:
            raise
        except SQLAlchemyError as e:
            logger.exception("Database error retrieving vendor %s", vendor_id)
            raise DatabaseException(
                "Vendor details could not be loaded. Please try again."
            ) from e

    def list_vendors(
        self,
        params: PaginationParams,
        filters: VendorFilterParams | None = None,
    ) -> PaginatedResponse[VendorSummary]:
        """
        Return a paginated, filtered list of vendors.
        """
        try:
            query = self._db.query(Vendor)

            #  Apply filters
            if filters:
                if filters.status:
                    query = query.filter(Vendor.status == filters.status)

                if filters.search:
                    search_clause = build_search_clause(
                        filters.search,
                        Vendor.vendor_name,
                        Vendor.email,
                        Vendor.phone_primary,
                        Vendor.phone_secondary,
                    )
                    if search_clause is not None:
                        query = query.filter(search_clause)

            # Count only when requested; over-fetch otherwise.
            total = query.count() if params.with_total else None

            vendors = (
                query.order_by(Vendor.vendor_name.asc())
                .offset(params.offset)
                .limit(params.fetch_limit)
                .all()
            )

            # Batch payables lookup
            vendor_ids = [v.id for v in vendors]
            payables_map = self._compute_payables_bulk(vendor_ids)

            items = [
                VendorSummary(
                    id=v.id,
                    vendor_name=v.vendor_name,
                    email=v.email,
                    phone_primary=v.phone_primary,
                    status=v.status,
                    currency=v.currency,
                    payables=payables_map.get(v.id, Decimal("0.00")),
                    created_at=v.created_at,
                )
                for v in vendors
            ]

            logger.debug(
                "Listed vendors (page %d, total %s)",
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

        except SQLAlchemyError as e:
            logger.exception("Database error listing vendors")
            raise DatabaseException("Failed to list vendors") from e

    def get_status_counts(self) -> VendorStatusCounts:
        """
        Get all vendor counts.
        """
        counts = status_counts(
            self._db,
            Vendor.status,
            Vendor.id,
            error_context="vendor",
        )
        # `all` sums every returned status row rather than active + inactive,
        # so it stays correct if the status set ever expands.
        return VendorStatusCounts(
            all=sum(counts.values()),
            active=counts.get(VendorStatus.ACTIVE, 0),
            inactive=counts.get(VendorStatus.INACTIVE, 0),
        )

    def get_vendor_transactions(
        self,
        vendor_id: uuid.UUID,
        params: PaginationParams,
        filters: VendorTransactionFilterParams | None = None,
    ) -> PaginatedResponse[VendorTransactionSummary]:
        """
        Return a paginated, filtered list of the vendor's transactions.

        Combines expenses and purchase orders into one source-tagged list
        (transaction_type = 'expense' | 'purchase_order'). Both legs are
        projected to the identical column shape and UNION-ed, so the whole
        list is a single paginated query — there is no per-source N+1 and no
        PO-specific list builder. The expense leg filters on vendor_id; the
        PO leg filters on vendor_id and is backed by the (vendor_id, status)
        index. (Bills join here once that module lands, same shape.)

        A purchase order is a non-payable commitment — only the Bill it is
        converted to drives financials — so its balance is reported
        as 0.00, mirroring how non-payable docs present a balance. POs are
        deliberately absent from generate_statement / the cashflow
        aggregates, which query expenses only.
        """
        # Confirm vendor exists first so we return 404, not an empty list
        self._get_vendor_or_404(vendor_id)

        from app.modules.expenses.models import Expense
        from app.modules.purchase_orders.models import PurchaseOrder

        try:
            expense_q = self._db.query(
                Expense.id.label("id"),
                literal_column("'expense'").label("transaction_type"),
                Expense.expense_number.label("ref_no"),
                Expense.expense_date.label("date"),
                Expense.total_due.label("amount"),
                Expense.balance_due.label("balance"),
                Expense.status.label("status"),
                Expense.due_date.label("due_date"),
            ).filter(Expense.vendor_id == vendor_id)

            # PO leg: same column shape. balance is a literal 0.00 (non-payable
            # commitment); delivery_date maps to the shared due_date column for
            # display parity with the payable rows.
            po_q = self._db.query(
                PurchaseOrder.id.label("id"),
                literal_column("'purchase_order'").label("transaction_type"),
                PurchaseOrder.po_reference.label("ref_no"),
                PurchaseOrder.order_date.label("date"),
                PurchaseOrder.total.label("amount"),
                literal_column("0.00").label("balance"),
                PurchaseOrder.status.label("status"),
                PurchaseOrder.delivery_date.label("due_date"),
            ).filter(PurchaseOrder.vendor_id == vendor_id)

            union_q = expense_q.union_all(po_q).subquery()

            count_q = self._db.query(func.count()).select_from(union_q)
            data_q = self._db.query(union_q)

            # Apply status filter
            if filters and filters.status:
                count_q = count_q.filter(union_q.c.status == filters.status)
                data_q = data_q.filter(union_q.c.status == filters.status)

            # Apply transaction-type filter (expense | purchase_order)
            if filters and filters.transaction_type:
                count_q = count_q.filter(
                    union_q.c.transaction_type == filters.transaction_type
                )
                data_q = data_q.filter(
                    union_q.c.transaction_type == filters.transaction_type
                )

            total = count_q.scalar() or 0

            rows = (
                data_q.order_by(union_q.c.date.desc())
                .offset(params.offset)
                .limit(params.per_page)
                .all()
            )

            items = [
                VendorTransactionSummary(
                    id=row.id,
                    transaction_type=row.transaction_type,
                    ref_no=row.ref_no,
                    date=row.date,
                    amount=Decimal(str(row.amount)),
                    balance=Decimal(str(row.balance)),
                    status=row.status,
                    due_date=row.due_date,
                )
                for row in rows
            ]

            return PaginatedResponse.create(items=items, total=total, params=params)

        except SQLAlchemyError as e:
            logger.exception(
                "Database error retrieving transactions for vendor %s", vendor_id
            )
            raise DatabaseException("Failed to retrieve vendor transactions") from e

    def get_payables_summary(self, vendor_id: uuid.UUID) -> VendorPayablesSummary:
        """
        Return the payables summary
        """
        vendor = self._get_vendor_or_404(vendor_id)
        payables = self._compute_payables_for_vendor(vendor_id)
        return VendorPayablesSummary(
            total_unpaid=payables["total_unpaid"],
            overdue_total=payables["overdue_total"],
            currency=vendor.currency,
        )

    # DETAIL CARDS (Total POs / Total Payments / Total Bills)

    @staticmethod
    def _paginate(page: int, per_page: int, count: int) -> tuple[int, int]:
        """Return (offset, total_pages) for a 1-indexed page."""
        total_pages = max(1, -(-count // per_page)) if per_page else 1
        return (page - 1) * per_page, total_pages

    def get_purchase_orders_card(
        self,
        vendor_id: uuid.UUID,
        period_start: date,
        period_end: date,
        page: int = 1,
        per_page: int = 10,
    ) -> VendorCardSummary:
        """Total POs card: POs for the vendor in the period, paid/pending split.

        DRAFT POs are excluded (a draft is not yet a commitment). Aggregates
        are computed in SQL over the whole filtered set; only the requested
        page of rows is materialised.
        """
        from app.modules.purchase_orders.models import PurchaseOrder

        vendor = self._get_vendor_or_404(vendor_id)
        try:
            base = self._db.query(PurchaseOrder).filter(
                PurchaseOrder.vendor_id == vendor_id,
                PurchaseOrder.status.in_(CARD_PO_STATUSES),
                PurchaseOrder.order_date >= period_start,
                PurchaseOrder.order_date <= period_end,
            )

            agg = base.with_entities(
                func.coalesce(func.sum(PurchaseOrder.total), Decimal("0.00")),
                func.coalesce(
                    func.sum(
                        func.least(PurchaseOrder.amount_paid, PurchaseOrder.total)
                    ),
                    Decimal("0.00"),
                ),
                func.coalesce(
                    func.sum(func.greatest(PurchaseOrder.balance_due, 0)),
                    Decimal("0.00"),
                ),
                func.count(),
            ).one()
            total, paid_total, pending_total, count = agg

            offset, total_pages = self._paginate(page, per_page, count)
            rows = (
                base.order_by(PurchaseOrder.order_date.desc())
                .offset(offset)
                .limit(per_page)
                .all()
            )
            items = [
                VendorCardItem(
                    id=po.id,
                    source="purchase_order",
                    ref_no=po.po_reference,
                    date=po.order_date,
                    amount=Decimal(str(po.total)),
                    payment_state="paid" if po.is_paid else "pending",
                )
                for po in rows
            ]
        except SQLAlchemyError as e:
            logger.exception("Error building PO card for vendor %s", vendor_id)
            raise DatabaseException("Failed to build purchase-orders card") from e

        return VendorCardSummary(
            total=Decimal(str(total)),
            paid_total=Decimal(str(paid_total)),
            pending_total=Decimal(str(pending_total)),
            count=count,
            currency=vendor.currency,
            period_start=period_start,
            period_end=period_end,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            items=items,
        )

    def get_bills_card(
        self,
        vendor_id: uuid.UUID,
        period_start: date,
        period_end: date,
        page: int = 1,
        per_page: int = 10,
    ) -> VendorCardSummary:
        """Total Bills/Invoices card: the vendor's expenses in the period.

        In this system a vendor "bill/invoice" is an Expense. CANCELED
        expenses are excluded (voided). Paid vs pending is derived from the
        expense status/balance.
        """
        from app.modules.expenses.models import Expense

        vendor = self._get_vendor_or_404(vendor_id)
        try:
            base = self._db.query(Expense).filter(
                Expense.vendor_id == vendor_id,
                Expense.status.in_(CARD_BILL_STATUSES),
                Expense.expense_date >= period_start,
                Expense.expense_date <= period_end,
            )

            agg = base.with_entities(
                func.coalesce(func.sum(Expense.total_due), Decimal("0.00")),
                func.coalesce(
                    func.sum(func.least(Expense.amount_paid, Expense.total_due)),
                    Decimal("0.00"),
                ),
                func.coalesce(
                    func.sum(func.greatest(Expense.balance_due, 0)),
                    Decimal("0.00"),
                ),
                func.count(),
            ).one()
            total, paid_total, pending_total, count = agg

            offset, total_pages = self._paginate(page, per_page, count)
            rows = (
                base.order_by(Expense.expense_date.desc())
                .offset(offset)
                .limit(per_page)
                .all()
            )
            items = [
                VendorCardItem(
                    id=exp.id,
                    source="bill",
                    ref_no=exp.expense_reference,
                    date=exp.expense_date,
                    amount=Decimal(str(exp.total_due)),
                    payment_state="paid" if exp.is_paid else "pending",
                )
                for exp in rows
            ]
        except SQLAlchemyError as e:
            logger.exception("Error building bills card for vendor %s", vendor_id)
            raise DatabaseException("Failed to build bills card") from e

        return VendorCardSummary(
            total=Decimal(str(total)),
            paid_total=Decimal(str(paid_total)),
            pending_total=Decimal(str(pending_total)),
            count=count,
            currency=vendor.currency,
            period_start=period_start,
            period_end=period_end,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            items=items,
        )

    def get_payments_card(
        self,
        vendor_id: uuid.UUID,
        period_start: date,
        period_end: date,
        page: int = 1,
        per_page: int = 10,
    ) -> VendorCardSummary:
        """Total Payments card: everything paid to the vendor in the period.

        Unions PO payments (via PO -> vendor) and expense payments (via
        Expense -> vendor) into one source-tagged list. A payment is a settled
        event, so every row is 'paid' and pending_total is always 0.
        """
        from app.modules.expenses.models import Expense, ExpensePayment
        from app.modules.purchase_orders.models import (
            PurchaseOrder,
            PurchaseOrderPayment,
        )

        vendor = self._get_vendor_or_404(vendor_id)
        try:
            # invoice_number only exists on PO payments; expense payments carry
            # NULL. parent_id is the source document (PO / Expense) so the row's
            # "View" action can route to it. payment_ref is the payment's own
            # reference (cheque / txn id).
            po_leg = (
                self._db.query(
                    PurchaseOrderPayment.id.label("id"),
                    literal_column("'po_payment'").label("source"),
                    PurchaseOrder.po_reference.label("ref_no"),
                    PurchaseOrderPayment.payment_date.label("date"),
                    (
                        PurchaseOrderPayment.amount * PurchaseOrderPayment.exchange_rate
                    ).label("amount"),
                    PurchaseOrderPayment.invoice_number.label("invoice_number"),
                    PurchaseOrderPayment.reference.label("payment_ref"),
                    PurchaseOrder.id.label("parent_id"),
                )
                .join(PurchaseOrder, PurchaseOrderPayment.po_id == PurchaseOrder.id)
                .filter(
                    PurchaseOrder.vendor_id == vendor_id,
                    PurchaseOrderPayment.payment_date >= period_start,
                    PurchaseOrderPayment.payment_date <= period_end,
                )
            )
            expense_leg = (
                self._db.query(
                    ExpensePayment.id.label("id"),
                    literal_column("'expense_payment'").label("source"),
                    Expense.expense_reference.label("ref_no"),
                    ExpensePayment.payment_date.label("date"),
                    ExpensePayment.amount.label("amount"),
                    null().label("invoice_number"),
                    ExpensePayment.reference.label("payment_ref"),
                    Expense.id.label("parent_id"),
                )
                .join(Expense, ExpensePayment.expense_id == Expense.id)
                .filter(
                    Expense.vendor_id == vendor_id,
                    ExpensePayment.payment_date >= period_start,
                    ExpensePayment.payment_date <= period_end,
                )
            )

            union_q = po_leg.union_all(expense_leg).subquery()

            total, count = (
                self._db.query(
                    func.coalesce(func.sum(union_q.c.amount), Decimal("0.00")),
                    func.count(),
                )
                .select_from(union_q)
                .one()
            )

            offset, total_pages = self._paginate(page, per_page, count)
            rows = (
                self._db.query(union_q)
                .order_by(union_q.c.date.desc())
                .offset(offset)
                .limit(per_page)
                .all()
            )
            items = [
                VendorCardItem(
                    id=row.id,
                    source=row.source,
                    ref_no=row.ref_no,
                    date=row.date,
                    amount=Decimal(str(row.amount)),
                    payment_state="paid",
                    invoice_number=row.invoice_number,
                    payment_ref=row.payment_ref,
                    parent_id=row.parent_id,
                )
                for row in rows
            ]
        except SQLAlchemyError as e:
            logger.exception("Error building payments card for vendor %s", vendor_id)
            raise DatabaseException("Failed to build payments card") from e

        return VendorCardSummary(
            total=Decimal(str(total)),
            paid_total=Decimal(str(total)),
            pending_total=Decimal("0.00"),
            count=count,
            currency=vendor.currency,
            period_start=period_start,
            period_end=period_end,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            items=items,
        )

    # CARD EXPORT LOADERS — full (uncapped-by-page) rows for Excel/PDF.

    def card_export_rows(
        self,
        vendor_id: uuid.UUID,
        card: str,
        period_start: date,
        period_end: date,
        limit: int,
    ) -> tuple[Vendor, list[VendorCardItem], VendorCardSummary, bool]:
        """Return (vendor, rows, summary) for a card export.

        `card` is one of 'purchase_orders' | 'payments' | 'bills'. Rows are the
        tagged card items (up to `limit`, ordered newest-first); the summary
        carries the aggregate totals for the export footer.
        """
        builder = {
            "purchase_orders": self.get_purchase_orders_card,
            "payments": self.get_payments_card,
            "bills": self.get_bills_card,
        }.get(card)
        if builder is None:
            raise BadRequestException(detail=f"Unknown card '{card}'", field="card")

        # Reuse the card builder with a single large page so aggregates and
        # rows come from the identical query the UI shows. Fetch one row
        # beyond the export cap so truncation is detectable (consistent with
        # other list export endpoints which request BATCH_SIZE + 1).
        summary = builder(
            vendor_id, period_start, period_end, page=1, per_page=(limit + 1)
        )
        vendor = self._get_vendor_or_404(vendor_id)
        truncated = len(summary.items) > limit
        items = summary.items[:limit] if truncated else summary.items
        return vendor, items, summary, truncated

    # Card titles for export headers / filenames.
    _CARD_TITLES: ClassVar[dict[str, str]] = {
        "purchase_orders": "Total Purchase Orders",
        "payments": "Total Payments",
        "bills": "Total Bills",
    }

    def build_card_excel(
        self,
        vendor_id: uuid.UUID,
        card: str,
        period_start: date,
        period_end: date,
        limit: int,
    ) -> tuple[bytes, str, bool]:
        """Build the .xlsx bytes for a card export and a safe filename stem."""
        from app.common.excel import ExcelExporter

        vendor, items, summary, truncated = self.card_export_rows(
            vendor_id, card, period_start, period_end, limit
        )
        title = self._CARD_TITLES.get(card, "Vendor Card")
        xlsx = ExcelExporter().export_vendor_card(
            title=title,
            vendor_name=vendor.vendor_name,
            currency=vendor.currency,
            items=items,
            totals={
                "total": summary.total,
                "paid_total": summary.paid_total,
                "pending_total": summary.pending_total,
                "count": summary.count,
            },
        )
        # Return truncated flag so callers (router) can set response headers.
        return xlsx, self._card_filename_stem(vendor.vendor_name, card), truncated

    def build_card_pdf(
        self,
        vendor_id: uuid.UUID,
        card: str,
        period_start: date,
        period_end: date,
        limit: int,
    ) -> tuple[bytes, str, bool]:
        """Build the PDF bytes for a card export and a safe filename stem."""
        from app.common.pdf import DocumentPDFGenerator
        from app.modules.owner.service import OwnerService

        vendor, items, summary, truncated = self.card_export_rows(
            vendor_id, card, period_start, period_end, limit
        )
        title = self._CARD_TITLES.get(card, "Vendor Card")

        # Live owner branding (a card is a fresh report, not an issued doc).
        owner_service = OwnerService(self._db)
        source = owner_service.get_or_create()
        owner_info = owner_service.to_owner_info(source)
        logo_bytes = owner_service.load_logo_bytes(source)

        pdf = DocumentPDFGenerator().generate_list_pdf(
            title=title,
            vendor=vendor,
            period_start=period_start,
            period_end=period_end,
            items=items,
            totals={
                "total": summary.total,
                "paid_total": summary.paid_total,
                "pending_total": summary.pending_total,
                "count": summary.count,
            },
            currency=vendor.currency,
            owner=owner_info,
            logo_bytes=logo_bytes,
        )
        # Return truncated flag so callers (router) can set response headers.
        return pdf, self._card_filename_stem(vendor.vendor_name, card), truncated

    @staticmethod
    def _card_filename_stem(vendor_name: str, card: str) -> str:
        """A filesystem-safe stem, e.g. 'AcmeLtd_Total_Payments'."""
        safe_vendor = re.sub(r"[^A-Za-z0-9]+", "", vendor_name or "Vendor") or "Vendor"
        safe_card = card.replace("_", "-")
        return f"{safe_vendor}_{safe_card}"

    # UPDATE

    def update(
        self,
        vendor_id: uuid.UUID,
        data: VendorUpdate,
        user_id: uuid.UUID | None = None,
        expected_version: int | None = None,
    ) -> Vendor:
        """
        Partial update of a vendor record.
        """
        try:
            vendor = self._get_vendor_or_404(vendor_id)

            # Atomic optimistic-lock guard: locks the row and compares the
            # version, replacing the previous non-atomic Python compare that
            # allowed silent last-write-wins.
            assert_version(self._db, Vendor, vendor_id, expected_version)

            update_data = data.model_dump(exclude_unset=True)
            if not update_data:
                return self._attach_payables(vendor)

            # Duplicate email check for the new email
            if update_data.get("email"):
                existing = self._check_duplicate_email(
                    update_data["email"],
                    exclude_vendor_id=vendor_id,
                )
                if existing:
                    raise ConflictException(
                        detail=(
                            f"A vendor with this email already exists: "
                            f"'{existing.vendor_name}'."
                        )
                    )

            # Apply field updates
            for field, value in update_data.items():
                setattr(vendor, field, value)

            vendor.updated_by = user_id
            vendor.version += 1

            self._db.flush()

            logger.info(
                "Updated vendor: %s",
                vendor.vendor_name,
                extra={
                    "vendor_id": str(vendor.id),
                    "updated_fields": list(update_data.keys()),
                    "new_version": vendor.version,
                    "updated_by": str(user_id) if user_id else None,
                },
            )

            return self._attach_payables(vendor)

        except (NotFoundException, ConflictException):
            raise
        except IntegrityError as e:
            self._db.rollback()
            if "uq_vendors_email_not_null" in str(e.orig):
                raise ConflictException(
                    "A vendor with this email already exists."
                ) from e
            raise ConflictException(
                "Vendor update violates database constraints."
            ) from e
        except SQLAlchemyError as e:
            logger.exception("Database error updating vendor %s", vendor_id)
            raise DatabaseException(
                "Vendor could not be saved. Please try again."
            ) from e

    # STATUS TRANSITIONS

    def activate(
        self,
        vendor_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> Vendor:
        """
        Set vendor status to active.
        """
        try:
            vendor = self._get_vendor_or_404(vendor_id)
            self._transition(vendor, VendorStatus.ACTIVE)
            vendor.updated_by = user_id
            self._db.flush()

            logger.info(
                "Activated vendor: %s",
                vendor.vendor_name,
                extra={
                    "vendor_id": str(vendor.id),
                    "updated_by": str(user_id) if user_id else None,
                },
            )

            return self._attach_payables(vendor)

        except (NotFoundException, BadRequestException):
            raise
        except SQLAlchemyError as e:
            logger.exception("Database error activating vendor %s", vendor_id)
            raise DatabaseException("Failed to activate vendor") from e

    def deactivate(
        self,
        vendor_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> Vendor:
        """
        Set vendor status to inactive.
        """
        try:
            vendor = self._get_vendor_or_404(vendor_id)
            self._transition(vendor, VendorStatus.INACTIVE)
            vendor.updated_by = user_id
            self._db.flush()

            logger.info(
                "Deactivated vendor: %s",
                vendor.vendor_name,
                extra={
                    "vendor_id": str(vendor.id),
                    "updated_by": str(user_id) if user_id else None,
                },
            )

            return self._attach_payables(vendor)

        except (NotFoundException, BadRequestException):
            raise
        except SQLAlchemyError as e:
            logger.exception("Database error deactivating vendor %s", vendor_id)
            raise DatabaseException("Failed to deactivate vendor") from e

    #  DELETE

    def delete(
        self,
        vendor_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Permanently delete a vendor record.
        """
        try:
            vendor = self._get_vendor_or_404(vendor_id)

            #  Open-transaction guard
            if self._has_open_transactions(vendor_id):
                raise BadRequestException(
                    detail=(
                        "This vendor has open transactions. "
                        "Please resolve all outstanding balances before deleting."
                    ),
                    field="vendor_id",
                )

            vendor_name = vendor.vendor_name
            # Durable audit trail — written before the DELETE so
            # the snapshot is taken while the row still exists.
            record_audit_event(
                self._db,
                actor_id=user_id or self._actor_id,
                entity_type="vendor",
                entity_id=vendor.id,
                action="hard_deleted",
                before={"vendor_name": vendor_name},
            )
            self._db.delete(vendor)
            self._db.flush()

            logger.warning(
                "Deleted vendor: %s",
                vendor_name,
                extra={
                    "vendor_id": str(vendor_id),
                    "deleted_by": str(user_id) if user_id else None,
                },
            )

            return {
                "vendor_id": vendor_id,
                "message": "Vendor deleted successfully",
            }

        except (NotFoundException, BadRequestException):
            raise
        except SQLAlchemyError as e:
            logger.exception("Database error deleting vendor %s", vendor_id)
            raise DatabaseException("Failed to delete vendor") from e

    # CONTACT SEARCH

    def search_contacts(
        self,
        query: str,
        limit: int = 20,
    ) -> ContactSearchResponse:
        """
        Search CRM contacts by name or phone for the modal Path B dropdown.

        Returns contacts that do NOT already have a vendor record linked to
        them (to prevent duplicate mappings). If the Contacts module is not
        yet importable, returns an empty result set gracefully.
        """
        try:
            from app.modules.contacts.models import Contact
        except ImportError:
            logger.debug("Contacts module not importable; search returns empty")
            return ContactSearchResponse(results=[], total=0)

        try:
            # Exclude contacts that are already mapped to a vendor
            existing_contact_ids = (
                self._db.query(Vendor.contact_id)
                .filter(Vendor.contact_id.isnot(None))
                .subquery()
            )

            search_clause = build_search_clause(
                query,
                Contact.full_name,
                Contact.phone_primary,
                Contact.email,
            )
            contact_query = self._db.query(Contact).filter(
                Contact.id.notin_(existing_contact_ids),
            )
            if search_clause is not None:
                contact_query = contact_query.filter(search_clause)
            contacts = (
                contact_query.order_by(Contact.full_name.asc()).limit(limit).all()
            )

            results = [
                ContactSearchResult(
                    id=c.id,
                    full_name=c.full_name,
                    email=c.email,
                    phone_primary=c.phone_primary,
                    address=c.address,
                    country=c.country,
                    website=getattr(c, "website", None),
                    vat_number=getattr(c, "vat_number", None),
                )
                for c in contacts
            ]

            return ContactSearchResponse(results=results, total=len(results))

        except SQLAlchemyError as e:
            logger.exception("Database error searching contacts")
            raise DatabaseException("Failed to search contacts") from e

    # DUPLICATE CHECK

    def check_email_duplicate(
        self,
        email: str,
        exclude_vendor_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Real-time duplicate email check for the frontend
        """
        existing = self._check_duplicate_email(email, exclude_vendor_id)
        if existing:
            return {
                "exists": True,
                "vendor": VendorSummary.model_validate(existing),
                "message": (
                    f"A vendor with this email already exists: '{existing.vendor_name}'. "
                    f"Would you like to view the existing record?"
                ),
            }
        return {
            "exists": False,
            "vendor": None,
            "message": None,
        }

    # PRIVATE UTILITIES

    def _verify_contact_exists(self, contact_id: uuid.UUID) -> None:
        """
        Verify that a CRM Contact with the given ID exists.
        """
        try:
            from app.modules.contacts.models import Contact
        except ImportError:
            return

        contact = self._db.query(Contact).filter(Contact.id == contact_id).first()
        if not contact:
            raise NotFoundException(
                detail=f"Contact with ID '{contact_id}' not found",
                resource="contact",
            )

    def generate_statement(
        self,
        vendor_id: uuid.UUID,
        period_start: date,
        period_end: date,
    ) -> VendorStatement:
        """Generate a statement of accounts for a vendor."""
        vendor = self.get_by_id(vendor_id)

        from app.modules.expenses.models import Expense, ExpensePayment

        opening_balance = self._calculate_balance_at_date(vendor_id, period_start)

        period_expenses = (
            self._db.query(Expense)
            .filter(
                Expense.vendor_id == vendor_id,
                Expense.status.notin_(EXCLUDED_STATEMENT_STATUSES),
                Expense.expense_date >= period_start,
                Expense.expense_date <= period_end,
            )
            .order_by(Expense.expense_date)
            .all()
        )

        period_payments = (
            self._db.query(ExpensePayment)
            .join(Expense)
            .filter(
                Expense.vendor_id == vendor_id,
                ExpensePayment.payment_date >= period_start,
                ExpensePayment.payment_date <= period_end,
                Expense.status.notin_(EXCLUDED_STATEMENT_STATUSES),
            )
            .order_by(ExpensePayment.payment_date)
            .all()
        )

        # Build entries for the shared generator
        debits = [
            DebitEntry(
                date=exp.expense_date,
                description=f"Expense {exp.expense_number}",
                amount=exp.total_due,
            )
            for exp in period_expenses
        ]
        credits = [
            CreditEntry(
                date=pmt.payment_date,
                description=(
                    f"Payment Made — {vendor.currency} {pmt.amount} "
                    f"for {pmt.expense.expense_number}"
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
            VendorStatementTransaction(
                date=t["date"],
                description=t["description"],
                amount=t["amount"],
                payment=t["payment"],
                balance=t["balance"],
            )
            for t in txns
        ]

        summary = VendorStatementSummary(
            opening_balance=summary_data["opening_balance"],
            invoiced_amount=summary_data["invoiced_amount"],
            amount_paid=summary_data["amount_paid"],
            balance_due=summary_data["balance_due"],
        )

        return VendorStatement(
            vendor=vendor,
            period_start=period_start,
            period_end=period_end,
            summary=summary,
            transactions=transactions,
        )

    def _calculate_balance_at_date(
        self,
        vendor_id: uuid.UUID,
        as_of_date: date,
    ) -> Decimal:
        """Calculate vendor balance as of a specific date (invoiced - paid)."""
        from app.modules.expenses.models import Expense, ExpensePayment

        # Same status predicate on both sides as the customer statement canceled
        # expenses and payments against them must not move the opening balance.
        invoiced = self._db.query(func.sum(Expense.total_due)).filter(
            Expense.vendor_id == vendor_id,
            Expense.expense_date < as_of_date,
            Expense.status.notin_(EXCLUDED_STATEMENT_STATUSES),
        ).scalar() or Decimal("0.00")

        paid = self._db.query(func.sum(ExpensePayment.amount)).join(Expense).filter(
            Expense.vendor_id == vendor_id,
            ExpensePayment.payment_date < as_of_date,
            Expense.status.notin_(EXCLUDED_STATEMENT_STATUSES),
        ).scalar() or Decimal("0.00")

        return Decimal(str(invoiced)) - Decimal(str(paid))
