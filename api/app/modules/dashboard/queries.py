"""Read-side SQL for the dashboard module.

Single accounting basis: ``DashboardRepository`` *extends*
``StatementsRepository`` so every dashboard number derives from the same
recognition predicates and window conditions as the statements pages
(``RECOGNIZED_INVOICE_STATUSES`` / ``RECOGNIZED_EXPENSE_STATUSES``;
DRAFT and CANCELED documents are excluded everywhere).

Measurement bases (documented per query):

- the summary income/expense cards reuse the inherited pre-tax
  ``period_totals`` aggregate;
- the cashflow series and recent transactions use tax-inclusive
  ``total_due`` (cash view), matching the statements cashflow ledger;
- top sales aggregates pre-tax ``line_total`` per item, matching the
  income statement's revenue lines.

Scalability: aggregation is pushed into SQL. The chart series groups by
day in SQL (portable across PostgreSQL and SQLite — no ``date_trunc``)
and folds at most ``MAX_CUSTOM_RANGE_DAYS`` day-rows into buckets in
Python. Widget queries are LIMIT-bounded by server-side caps.

Read-only: this module never flushes or commits.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.exc import SQLAlchemyError

from app.common.exceptions import DatabaseException
from app.constants.enums import CustomerStatus
from app.modules.customers.models import Customer
from app.modules.expenses.models import Expense, ExpenseLineItem
from app.modules.invoices.models import Invoice, InvoiceLineItem
from app.modules.statements.queries import (
    CATEGORY_EXPENSE,
    CATEGORY_INCOME,
    SOURCE_TYPE_EXPENSE,
    SOURCE_TYPE_INVOICE,
    StatementsRepository,
    customer_display_name,
)
from app.modules.vendors.models import Vendor

logger = logging.getLogger(__name__)


def _utc_window(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Half-open UTC datetime window covering the closed date window.

    Used for timestamp columns (``created_at``) so the day boundaries are
    deterministic regardless of stored time components.
    """
    start = datetime.combine(date_from, time.min, tzinfo=UTC)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC)
    return start, end


class DashboardRepository(StatementsRepository):
    """Dashboard read queries on top of the statements accounting basis.

    Inherits ``period_totals`` (summary income/expense cards) and the
    shared invoice/expense window predicates, so the dashboard can never
    drift from the statements read model.
    """

    def receivables(self, date_from: date, date_to: date, currency: str) -> Decimal:
        """Outstanding receivables on recognized invoices in the window.

        SUM(balance_due), i.e. total_due - amount_paid, over invoices
        issued (``transaction_date``) within the window. Fully paid
        invoices contribute zero by construction.
        """
        try:
            total = (
                self._db.query(
                    func.coalesce(func.sum(Invoice.balance_due), Decimal("0"))
                )
                .filter(*self._invoice_conds(date_from, date_to, currency))
                .scalar()
            )
            return Decimal(str(total))
        except SQLAlchemyError as exc:
            logger.exception("Database error calculating outstanding receivables")
            raise DatabaseException(
                "Failed to calculate outstanding receivables"
            ) from exc

    def new_customers_count(self, date_from: date, date_to: date) -> int:
        """Customers created within the window (UTC), excluding soft-deleted.

        Deliberately currency-agnostic: a new customer counts regardless
        of their preferred billing currency.
        """
        start, end = _utc_window(date_from, date_to)
        try:
            count = (
                self._db.query(func.count(Customer.id))
                .filter(
                    Customer.created_at >= start,
                    Customer.created_at < end,
                    Customer.status != CustomerStatus.DELETED,
                )
                .scalar()
            )
            return int(count or 0)
        except SQLAlchemyError as exc:
            logger.exception("Database error counting new customers")
            raise DatabaseException("Failed to count new customers") from exc

    # Cashflow chart series

    def daily_income(
        self, date_from: date, date_to: date, currency: str
    ) -> list[tuple[date, Decimal]]:
        """Tax-inclusive income per day (cash view, matches the ledger)."""
        try:
            rows = (
                self._db.query(Invoice.transaction_date, func.sum(Invoice.total_due))
                .filter(*self._invoice_conds(date_from, date_to, currency))
                .group_by(Invoice.transaction_date)
                .all()
            )
            return [(day, Decimal(str(total))) for day, total in rows]
        except SQLAlchemyError as exc:
            logger.exception("Database error building daily income series")
            raise DatabaseException("Failed to build daily income series") from exc

    def daily_expense(
        self, date_from: date, date_to: date, currency: str
    ) -> list[tuple[date, Decimal]]:
        """Tax-inclusive expenses per day as positive magnitudes."""
        try:
            rows = (
                self._db.query(Expense.expense_date, func.sum(Expense.total_due))
                .filter(*self._expense_conds(date_from, date_to, currency))
                .group_by(Expense.expense_date)
                .all()
            )
            return [(day, Decimal(str(total))) for day, total in rows]
        except SQLAlchemyError as exc:
            logger.exception("Database error building daily expense series")
            raise DatabaseException("Failed to build daily expense series") from exc

    # Top sales

    def top_sales(
        self, date_from: date, date_to: date, currency: str, limit: int
    ) -> list:
        """Top revenue lines grouped by item name.

        Pre-tax ``SUM(line_total)`` — the same measurement as the income
        statement's revenue lines — with a deterministic tie-break on
        item name.
        """
        amount = func.coalesce(
            func.sum(InvoiceLineItem.line_total), Decimal("0")
        ).label("amount")
        try:
            return (
                self._db.query(
                    InvoiceLineItem.item_name.label("item_name"),
                    func.coalesce(
                        func.sum(InvoiceLineItem.quantity), Decimal("0")
                    ).label("units_sold"),
                    func.count(func.distinct(InvoiceLineItem.invoice_id)).label(
                        "document_count"
                    ),
                    amount,
                )
                .join(Invoice, InvoiceLineItem.invoice_id == Invoice.id)
                .filter(*self._invoice_conds(date_from, date_to, currency))
                .group_by(InvoiceLineItem.item_name)
                .order_by(amount.desc(), InvoiceLineItem.item_name.asc())
                .limit(limit)
                .all()
            )
        except SQLAlchemyError as exc:
            logger.exception("Database error building top sales")
            raise DatabaseException("Failed to build top sales") from exc

    # Recent transactions

    def recent_transactions(
        self, date_from: date, date_to: date, currency: str, limit: int
    ) -> list:
        """Most recent recognized documents, newest first (signed amounts).

        Ordered by the stable (entry_date, recorded_at, source_id) DESC
        key so the widget never shifts between requests on equal dates.
        The first line item is resolved via correlated scalar subqueries
        (no N+1).
        """
        first_invoice_item = (
            select(InvoiceLineItem.item_name)
            .where(InvoiceLineItem.invoice_id == Invoice.id)
            .order_by(InvoiceLineItem.line_number.asc())
            .limit(1)
            .correlate(Invoice)
            .scalar_subquery()
        )
        first_expense_item = (
            select(ExpenseLineItem.item_name)
            .where(ExpenseLineItem.expense_id == Expense.id)
            .order_by(ExpenseLineItem.line_number.asc())
            .limit(1)
            .correlate(Expense)
            .scalar_subquery()
        )
        income = (
            select(
                Invoice.id.label("source_id"),
                literal(SOURCE_TYPE_INVOICE).label("source_type"),
                customer_display_name().label("entity_name"),
                Customer.website.label("website"),
                first_invoice_item.label("item_name"),
                Invoice.invoice_reference.label("ref_no"),
                literal(CATEGORY_INCOME).label("category"),
                Invoice.transaction_date.label("entry_date"),
                Invoice.created_at.label("recorded_at"),
                Invoice.total_due.label("amount"),
            )
            .select_from(Invoice)
            .join(Customer, Invoice.customer_id == Customer.id)
            .where(*self._invoice_conds(date_from, date_to, currency))
        )
        expense = (
            select(
                Expense.id.label("source_id"),
                literal(SOURCE_TYPE_EXPENSE).label("source_type"),
                Vendor.vendor_name.label("entity_name"),
                Vendor.website.label("website"),
                first_expense_item.label("item_name"),
                Expense.expense_reference.label("ref_no"),
                literal(CATEGORY_EXPENSE).label("category"),
                Expense.expense_date.label("entry_date"),
                Expense.created_at.label("recorded_at"),
                (-Expense.total_due).label("amount"),
            )
            .select_from(Expense)
            .join(Vendor, Expense.vendor_id == Vendor.id)
            .where(*self._expense_conds(date_from, date_to, currency))
        )
        try:
            sub = union_all(income, expense).subquery("recent_transactions")
            stmt = (
                select(sub)
                .order_by(
                    sub.c.entry_date.desc(),
                    sub.c.recorded_at.desc(),
                    sub.c.source_id.desc(),
                )
                .limit(limit)
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error loading recent transactions")
            raise DatabaseException("Failed to load recent transactions") from exc
