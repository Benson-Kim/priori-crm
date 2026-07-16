"""Read-side SQL for the financial statements module.

Accounting basis — single source of truth

Every statements endpoint (overview, income statement, cashflow) derives
from the same two recognition predicates defined here, so the three
views can never disagree:

- ``RECOGNIZED_INVOICE_STATUSES`` — an invoice contributes revenue once
  issued (sent / partial / paid / overdue), dated by ``transaction_date``.
  DRAFT and CANCELED invoices are excluded everywhere.
- ``RECOGNIZED_EXPENSE_STATUSES`` — an expense contributes once recorded
  (pending / paid / overdue), dated by ``expense_date``. CANCELED
  expenses are excluded everywhere.

Amounts:

- Overview totals and income-statement lines aggregate **pre-tax line
  totals** (``SUM(line_total)``): VAT is a liability collected on behalf
  of the tax authority, not revenue. Both endpoints run the same
  aggregate, so the overview total always equals the sum of the income
  statement's lines. Document-level invoice discounts are not allocated
  to lines in v1 (revenue is reported gross of discounts).
- The cashflow ledger lists the same recognized documents, one row per
  document, at their tax-inclusive ``total_due`` (income positive,
  expense negative).

Scalability: all aggregation is pushed into SQL; only aggregated or
paginated rows are materialised. The ledger is ordered by a stable
``(entry_date, source_id)`` key and over-fetched by one sentinel row so
no COUNT(*) is needed for pagination.

Single currency: every query filters on exactly one ISO-4217 currency —
summing mixed currencies would be meaningless.

Read-only: this module never flushes or commits.
"""

import logging
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Final

from sqlalchemy import Select, and_, case, func, literal, select, union_all
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.common.exceptions import DatabaseException
from app.common.search import build_search_clause
from app.constants.enums import CustomerType, ExpenseStatus, InvoiceStatus
from app.modules.customers.models import Customer
from app.modules.expenses.models import Expense, ExpenseLineItem
from app.modules.invoices.models import Invoice, InvoiceLineItem
from app.modules.statements.schemas import CashflowCounts
from app.modules.vendors.models import Vendor

logger = logging.getLogger(__name__)

#: Invoice statuses that contribute revenue. Excludes DRAFT and CANCELED.
RECOGNIZED_INVOICE_STATUSES: Final[tuple[InvoiceStatus, ...]] = (
    InvoiceStatus.SENT,
    InvoiceStatus.PARTIAL,
    InvoiceStatus.PAID,
    InvoiceStatus.OVERDUE,
)

#: Expense statuses that contribute expenses. Excludes CANCELED.
RECOGNIZED_EXPENSE_STATUSES: Final[tuple[ExpenseStatus, ...]] = (
    ExpenseStatus.PENDING,
    ExpenseStatus.PAID,
    ExpenseStatus.OVERDUE,
)

SOURCE_TYPE_INVOICE: Final = "quote_invoice"
SOURCE_TYPE_EXPENSE: Final = "expense"

CATEGORY_INCOME: Final = "income"
CATEGORY_EXPENSE: Final = "expense"


@dataclass(frozen=True)
class PeriodTotals:
    """Aggregated pre-tax totals for one reporting window."""

    revenue: Decimal
    expenses: Decimal


@dataclass(frozen=True)
class CashflowFilters:
    """Filter set shared by the cashflow window, total and counts queries."""

    date_from: date
    date_to: date
    currency: str
    category: str = "all"  # all | income | expense
    search: str | None = None
    account_category: str | None = None


def customer_display_name() -> ColumnElement[str]:
    """SQL expression for a customer's display name.

    Business customers display their company name; individuals display
    \"first last\". Defined once so search and display can never drift.
    """
    return case(
        (
            and_(
                Customer.customer_type == CustomerType.BUSINESS,
                Customer.company_name.isnot(None),
            ),
            Customer.company_name,
        ),
        else_=Customer.first_name + " " + Customer.last_name,
    )


class StatementsRepository:
    """Read-side aggregates and ledger queries for financial statements."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # Shared predicates

    @staticmethod
    def _invoice_conds(date_from: date, date_to: date, currency: str) -> list:
        return [
            Invoice.status.in_(RECOGNIZED_INVOICE_STATUSES),
            Invoice.transaction_date >= date_from,
            Invoice.transaction_date <= date_to,
            Invoice.currency == currency,
        ]

    @staticmethod
    def _expense_conds(date_from: date, date_to: date, currency: str) -> list:
        return [
            Expense.status.in_(RECOGNIZED_EXPENSE_STATUSES),
            Expense.expense_date >= date_from,
            Expense.expense_date <= date_to,
            Expense.currency == currency,
        ]

    # Overview totals

    def period_totals(
        self, date_from: date, date_to: date, currency: str
    ) -> PeriodTotals:
        """Pre-tax revenue and expense totals for one window.

        Runs the same SUM(line_total) aggregates as the per-category
        breakdowns, so overview and income statement always agree.
        """
        try:
            revenue = (
                self._db.query(
                    func.coalesce(func.sum(InvoiceLineItem.line_total), Decimal("0"))
                )
                .join(Invoice, InvoiceLineItem.invoice_id == Invoice.id)
                .filter(*self._invoice_conds(date_from, date_to, currency))
                .scalar()
            )
            expenses = (
                self._db.query(
                    func.coalesce(func.sum(ExpenseLineItem.line_total), Decimal("0"))
                )
                .join(Expense, ExpenseLineItem.expense_id == Expense.id)
                .filter(*self._expense_conds(date_from, date_to, currency))
                .scalar()
            )
            return PeriodTotals(
                revenue=Decimal(str(revenue)),
                expenses=Decimal(str(expenses)),
            )
        except SQLAlchemyError as exc:
            logger.exception("Database error calculating statement totals")
            raise DatabaseException("Failed to calculate statement totals") from exc

    # Income statement breakdowns

    def revenue_by_category(
        self, date_from: date, date_to: date, currency: str
    ) -> list:
        """Revenue lines grouped by account category (line-item name)."""
        amount = func.coalesce(
            func.sum(InvoiceLineItem.line_total), Decimal("0")
        ).label("amount")
        try:
            return (
                self._db.query(
                    InvoiceLineItem.item_name.label("account_category"),
                    func.min(InvoiceLineItem.description).label("description"),
                    func.count(func.distinct(InvoiceLineItem.invoice_id)).label(
                        "document_count"
                    ),
                    amount,
                )
                .join(Invoice, InvoiceLineItem.invoice_id == Invoice.id)
                .filter(*self._invoice_conds(date_from, date_to, currency))
                .group_by(InvoiceLineItem.item_name)
                .order_by(amount.desc(), InvoiceLineItem.item_name.asc())
                .all()
            )
        except SQLAlchemyError as exc:
            logger.exception("Database error building revenue breakdown")
            raise DatabaseException("Failed to build revenue breakdown") from exc

    def expenses_by_category(
        self, date_from: date, date_to: date, currency: str
    ) -> list:
        """Expense lines grouped by account category (line-item name)."""
        amount = func.coalesce(
            func.sum(ExpenseLineItem.line_total), Decimal("0")
        ).label("amount")
        try:
            return (
                self._db.query(
                    ExpenseLineItem.item_name.label("account_category"),
                    func.min(ExpenseLineItem.description).label("description"),
                    func.count(func.distinct(ExpenseLineItem.expense_id)).label(
                        "document_count"
                    ),
                    amount,
                )
                .join(Expense, ExpenseLineItem.expense_id == Expense.id)
                .filter(*self._expense_conds(date_from, date_to, currency))
                .group_by(ExpenseLineItem.item_name)
                .order_by(amount.desc(), ExpenseLineItem.item_name.asc())
                .all()
            )
        except SQLAlchemyError as exc:
            logger.exception("Database error building expense breakdown")
            raise DatabaseException("Failed to build expense breakdown") from exc

    # Cashflow ledger

    def _income_select(self, f: CashflowFilters) -> Select:
        entity = customer_display_name()
        conds = self._invoice_conds(f.date_from, f.date_to, f.currency)
        if f.search:
            clause = build_search_clause(
                f.search,
                entity,
                Invoice.invoice_reference,
                Invoice.invoice_number,
            )
            if clause is not None:
                conds.append(clause)
        if f.account_category:
            conds.append(
                select(InvoiceLineItem.id)
                .where(
                    InvoiceLineItem.invoice_id == Invoice.id,
                    InvoiceLineItem.item_name == f.account_category,
                )
                .exists()
            )
        return (
            select(
                Invoice.id.label("source_id"),
                literal(SOURCE_TYPE_INVOICE).label("source_type"),
                entity.label("entity_name"),
                Invoice.invoice_reference.label("ref_no"),
                literal(CATEGORY_INCOME).label("category"),
                Invoice.transaction_date.label("entry_date"),
                Invoice.total_due.label("amount"),
            )
            .select_from(Invoice)
            .join(Customer, Invoice.customer_id == Customer.id)
            .where(*conds)
        )

    def _expense_select(self, f: CashflowFilters) -> Select:
        conds = self._expense_conds(f.date_from, f.date_to, f.currency)
        if f.search:
            clause = build_search_clause(
                f.search,
                Vendor.vendor_name,
                Expense.expense_reference,
                Expense.expense_number,
            )
            if clause is not None:
                conds.append(clause)
        if f.account_category:
            conds.append(
                select(ExpenseLineItem.id)
                .where(
                    ExpenseLineItem.expense_id == Expense.id,
                    ExpenseLineItem.item_name == f.account_category,
                )
                .exists()
            )
        return (
            select(
                Expense.id.label("source_id"),
                literal(SOURCE_TYPE_EXPENSE).label("source_type"),
                Vendor.vendor_name.label("entity_name"),
                Expense.expense_reference.label("ref_no"),
                literal(CATEGORY_EXPENSE).label("category"),
                Expense.expense_date.label("entry_date"),
                (-Expense.total_due).label("amount"),
            )
            .select_from(Expense)
            .join(Vendor, Expense.vendor_id == Vendor.id)
            .where(*conds)
        )

    def _cashflow_subquery(self, f: CashflowFilters):
        branches: list[Select] = []
        if f.category in ("all", CATEGORY_INCOME):
            branches.append(self._income_select(f))
        if f.category in ("all", CATEGORY_EXPENSE):
            branches.append(self._expense_select(f))
        sel = branches[0] if len(branches) == 1 else union_all(*branches)
        return sel.subquery("cash_entries")

    def cashflow_window(self, f: CashflowFilters, *, offset: int, limit: int) -> list:
        """One over-fetched page of ledger rows.

        Caller passes ``PaginationParams.fetch_limit`` so the sentinel-row
        pattern can derive ``has_next`` without a COUNT(*). Ordering is a
        stable (entry_date DESC, source_id DESC) key so pages never shift
        between requests on equal dates.
        """
        try:
            sub = self._cashflow_subquery(f)
            stmt = (
                select(sub)
                .order_by(sub.c.entry_date.desc(), sub.c.source_id.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error loading cashflow ledger")
            raise DatabaseException("Failed to load cashflow ledger") from exc

    def cashflow_total(self, f: CashflowFilters) -> int:
        """Opt-in exact row count for page-number UIs (with_total=true)."""
        try:
            sub = self._cashflow_subquery(f)
            total = self._db.execute(select(func.count()).select_from(sub)).scalar()
            return int(total or 0)
        except SQLAlchemyError as exc:
            logger.exception("Database error counting cashflow ledger")
            raise DatabaseException("Failed to count cashflow ledger") from exc

    def cashflow_counts(self, f: CashflowFilters) -> CashflowCounts:
        """Per-category counts for the filter tabs.

        Ignores any category filter (the tabs always show all buckets)
        but applies the period / currency / search / drill-down filters.
        """
        try:
            sub = self._cashflow_subquery(replace(f, category="all"))
            rows = self._db.execute(
                select(sub.c.category, func.count()).group_by(sub.c.category)
            ).all()
            counts = {category: count for category, count in rows}
            income = counts.get(CATEGORY_INCOME, 0)
            expense = counts.get(CATEGORY_EXPENSE, 0)
            return CashflowCounts(all=income + expense, income=income, expense=expense)
        except SQLAlchemyError as exc:
            logger.exception("Database error counting cashflow categories")
            raise DatabaseException("Failed to count cashflow categories") from exc
