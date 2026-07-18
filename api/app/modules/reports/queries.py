"""Read-side SQL for the reports module.

Accounting basis
----------------
Shares the recognition predicates from statements.queries — the single
source of truth for what counts as recognized revenue and expense. Both
modules must agree; importing rather than redefining prevents drift.

  RECOGNIZED_INVOICE_STATUSES  — (SENT, PARTIAL, PAID, OVERDUE)
  RECOGNIZED_EXPENSE_STATUSES  — (PENDING, PAID, OVERDUE)
  RECOGNIZED_PO_STATUSES       — (SENT, PAID) — DRAFT is pre-financial

Revenue: net of discount, pre-tax — SUM(Invoice.total_due - Invoice.tax_total).
Formula: Invoice.total_due = subtotal - discount + tax_total, therefore
total_due - tax_total = subtotal - discount = net revenue after discount.
Invoice.discount_value is a @property, NOT a stored column; never reference
it in SQL. The discount is derived as: subtotal - (total_due - tax_total).

Tax: SUM(Invoice.tax_total) for sales; SUM(Expense.tax_total) + SUM(PO.tax_total)
for purchases. Document-level totals avoid an extra join to line items.
For per-type breakdown on sales: invoice line-item tax_amount is used.
For per-type breakdown on purchases:
  - Expenses: ExpenseLineItem.tax_amount grouped by tax_type
  - POs: PurchaseOrder.tax_total mapped to TaxType via vat_rate CASE expression.
    PO line items always have tax_type=no_tax/tax_amount=0 by design
    (neutralize_line_tax() in financial.py). Document-level tax_total is correct.

Purchases ledger: union_all of expenses and POs, mirroring the cashflow
module's pattern. All pagination (OFFSET/LIMIT) and ordering happen in SQL.

PurchaseOrder.total (not total_due) — the PO model uses `total` for its
tax-inclusive amount (confirmed purchase_orders/models.py:240).

PurchaseOrderLineItem.po_id — the FK column is `po_id` not `purchase_order_id`
(confirmed purchase_orders/models.py:434).

Vendor.tax_id_pin — the KRA PIN field on Vendor is `tax_id_pin` not `kra_pin`
(confirmed vendors/models.py:131).

customer_display_name() — imported from statements.queries so search and
display can never drift between reports and statements pages.

Aged AR/AP: point-in-time (no date range). PostgreSQL date subtraction
(CURRENT_DATE - due_date) returns INTEGER days. Never use func.datediff()
which is MySQL-only. POs have no due_date — always bucket as "current".

Read-only: this module never flushes or commits.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from sqlalchemy import Select, case, func, literal, select, union_all
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.common.exceptions import DatabaseException
from app.common.search import build_search_clause
from app.constants.enums import PurchaseOrderStatus
from app.modules.customers.models import Customer
from app.modules.expenses.models import Expense, ExpenseLineItem
from app.modules.invoices.models import Invoice, InvoiceLineItem
from app.modules.purchase_orders.models import PurchaseOrder

# Single source of truth — import, never redefine
from app.modules.statements.queries import (
    RECOGNIZED_EXPENSE_STATUSES,
    RECOGNIZED_INVOICE_STATUSES,
    customer_display_name,
)
from app.modules.vendors.models import Vendor

logger = logging.getLogger(__name__)

# POs recognized once issued (SENT) or settled (PAID). DRAFT is pre-financial.
RECOGNIZED_PO_STATUSES: Final[tuple[PurchaseOrderStatus, ...]] = (
    PurchaseOrderStatus.SENT,
    PurchaseOrderStatus.PAID,
)


# Shared period-filter condition lists (mirrors StatementsRepository pattern)


def _invoice_conds(date_from: date, date_to: date, currency: str) -> list:
    """Invoice recognition predicates — mirrors StatementsRepository._invoice_conds."""
    return [
        Invoice.status.in_(RECOGNIZED_INVOICE_STATUSES),
        Invoice.transaction_date >= date_from,
        Invoice.transaction_date <= date_to,
        Invoice.currency == currency,
    ]


def _expense_conds(date_from: date, date_to: date, currency: str) -> list:
    """Expense recognition predicates — mirrors StatementsRepository._expense_conds."""
    return [
        Expense.status.in_(RECOGNIZED_EXPENSE_STATUSES),
        Expense.expense_date >= date_from,
        Expense.expense_date <= date_to,
        Expense.currency == currency,
    ]


def _po_conds(date_from: date, date_to: date, currency: str) -> list:
    """PO recognition predicates. Only SENT and PAID; DRAFT is pre-financial."""
    return [
        PurchaseOrder.status.in_(RECOGNIZED_PO_STATUSES),
        PurchaseOrder.order_date >= date_from,
        PurchaseOrder.order_date <= date_to,
        PurchaseOrder.currency == currency,
    ]


# Data containers (frozen dataclasses — immutable, hashable)


@dataclass(frozen=True)
class SalesSummary:
    """Aggregated sales metrics for one reporting window."""

    subtotal: Decimal  # SUM(Invoice.subtotal) — gross of discount, pre-tax
    discount_total: Decimal  # subtotal - net_revenue (derived)
    net_revenue: Decimal  # SUM(total_due - tax_total) — post-discount, pre-tax
    tax_collected: Decimal  # SUM(Invoice.tax_total)
    total_invoiced: Decimal  # SUM(Invoice.total_due) — tax-inclusive
    invoice_count: int
    outstanding_balance: Decimal  # SUM(balance_due) for SENT/PARTIAL/OVERDUE
    overdue_balance: Decimal  # SUM(balance_due) for OVERDUE only


@dataclass(frozen=True)
class PurchasesSummary:
    """Aggregated purchases metrics for one reporting window."""

    expense_spend: Decimal  # SUM(ExpenseLineItem.line_total) — pre-tax
    po_spend: Decimal  # SUM(PurchaseOrder.subtotal) — pre-tax
    total_spend: Decimal  # expense_spend + po_spend
    expense_tax: Decimal  # SUM(Expense.tax_total)
    po_tax: Decimal  # SUM(PurchaseOrder.tax_total)
    total_tax: Decimal  # expense_tax + po_tax
    expense_count: int
    po_count: int
    outstanding_balance: Decimal  # SUM(balance_due) for unpaid expenses + POs


@dataclass(frozen=True)
class TaxSummary:
    """VAT position for one reporting window."""

    vat_collected: Decimal  # sales VAT
    vat_paid: Decimal  # purchase VAT (expenses + POs)
    net_vat: Decimal  # vat_collected - vat_paid (positive = owed to KRA)


# ReportsRepository


class ReportsRepository:
    """Read-side aggregate and ledger queries for the reports module.

    Read-only: never flushes or commits. All aggregation is pushed into SQL.
    Follows the same structure and conventions as StatementsRepository.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # Sales: Summary

    def sales_summary(
        self, date_from: date, date_to: date, currency: str
    ) -> SalesSummary:
        """
        Aggregated sales metrics.

        Single document-level query. Revenue = SUM(total_due - tax_total) which
        equals subtotal - discount (net of document-level discounts, pre-tax).
        Invoice.discount_value is a @property not a column — never used in SQL.
        """
        from app.constants.enums import InvoiceStatus

        inv_conds = _invoice_conds(date_from, date_to, currency)
        try:
            doc_stmt = (
                select(
                    func.coalesce(func.sum(Invoice.subtotal), Decimal("0")).label(
                        "subtotal"
                    ),
                    func.coalesce(
                        func.sum(Invoice.total_due - Invoice.tax_total), Decimal("0")
                    ).label("net_revenue"),
                    func.coalesce(func.sum(Invoice.tax_total), Decimal("0")).label(
                        "tax"
                    ),
                    func.coalesce(func.sum(Invoice.total_due), Decimal("0")).label(
                        "total"
                    ),
                    func.count(Invoice.id).label("count"),
                    func.coalesce(
                        func.sum(Invoice.balance_due).filter(
                            Invoice.status.in_(
                                [
                                    InvoiceStatus.SENT,
                                    InvoiceStatus.PARTIAL,
                                    InvoiceStatus.OVERDUE,
                                ]
                            )
                        ),
                        Decimal("0"),
                    ).label("outstanding"),
                    func.coalesce(
                        func.sum(Invoice.balance_due).filter(
                            Invoice.status == InvoiceStatus.OVERDUE
                        ),
                        Decimal("0"),
                    ).label("overdue"),
                )
                .select_from(Invoice)
                .where(*inv_conds)
            )
            row = self._db.execute(doc_stmt).one()

            subtotal_d = Decimal(str(row.subtotal))
            net_revenue_d = Decimal(str(row.net_revenue))

            return SalesSummary(
                subtotal=subtotal_d,
                discount_total=subtotal_d - net_revenue_d,
                net_revenue=net_revenue_d,
                tax_collected=Decimal(str(row.tax)),
                total_invoiced=Decimal(str(row.total)),
                invoice_count=int(row.count),
                outstanding_balance=Decimal(str(row.outstanding)),
                overdue_balance=Decimal(str(row.overdue)),
            )
        except SQLAlchemyError as exc:
            logger.exception("Database error in sales_summary")
            raise DatabaseException("Failed to calculate sales summary") from exc

    # Sales: Breakdowns

    def revenue_by_customer(
        self, date_from: date, date_to: date, currency: str, *, limit: int = 10
    ) -> list:
        """Top N customers by net revenue (total_due - tax_total), post-discount."""
        inv_conds = _invoice_conds(date_from, date_to, currency)
        entity = customer_display_name()
        try:
            # Net revenue per invoice = total_due - tax_total (stored columns, no @property)
            amount = func.coalesce(
                func.sum(Invoice.total_due - Invoice.tax_total), Decimal("0")
            ).label("amount")
            stmt = (
                select(
                    entity.label("customer_name"),
                    func.count(Invoice.id).label("invoice_count"),
                    amount,
                )
                .select_from(Invoice)
                .join(Customer, Invoice.customer_id == Customer.id)
                .where(*inv_conds)
                .group_by(entity)
                .order_by(amount.desc())
                .limit(limit)
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error in revenue_by_customer")
            raise DatabaseException("Failed to aggregate revenue by customer") from exc

    def revenue_by_category(
        self, date_from: date, date_to: date, currency: str
    ) -> list:
        """Revenue grouped by item_name (account/product category)."""
        inv_conds = _invoice_conds(date_from, date_to, currency)
        try:
            amount = func.coalesce(
                func.sum(InvoiceLineItem.line_total), Decimal("0")
            ).label("amount")
            stmt = (
                select(
                    InvoiceLineItem.item_name.label("category"),
                    func.count(func.distinct(Invoice.id)).label("document_count"),
                    amount,
                )
                .select_from(InvoiceLineItem)
                .join(Invoice, InvoiceLineItem.invoice_id == Invoice.id)
                .where(*inv_conds)
                .group_by(InvoiceLineItem.item_name)
                .order_by(amount.desc())
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error in revenue_by_category")
            raise DatabaseException("Failed to aggregate revenue by category") from exc

    # Sales: Ledger

    def sales_ledger(
        self,
        date_from: date,
        date_to: date,
        currency: str,
        *,
        search: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 11,
    ) -> list:
        """Paginated list of recognized invoices. Fetch `per_page + 1` for sentinel."""
        entity = customer_display_name()
        conds = _invoice_conds(date_from, date_to, currency)
        if status and status != "all":
            conds.append(Invoice.status == status)
        if search:
            clause = build_search_clause(
                search,
                entity,
                Invoice.invoice_reference,
                Invoice.invoice_number,
            )
            if clause is not None:
                conds.append(clause)
        try:
            stmt = (
                select(
                    Invoice.id.label("id"),
                    entity.label("customer_name"),
                    Invoice.invoice_reference.label("reference"),
                    Invoice.invoice_number.label("number"),
                    Invoice.transaction_date.label("date"),
                    Invoice.status.label("status"),
                    Invoice.currency.label("currency"),
                    Invoice.subtotal.label("subtotal"),
                    # discount = subtotal - (total_due - tax_total), all stored columns
                    (Invoice.subtotal - (Invoice.total_due - Invoice.tax_total)).label(
                        "discount"
                    ),
                    (Invoice.total_due - Invoice.tax_total).label("net_revenue"),
                    Invoice.total_due.label("amount"),
                    Invoice.tax_total.label("tax"),
                    Invoice.balance_due.label("balance_due"),
                )
                .select_from(Invoice)
                .join(Customer, Invoice.customer_id == Customer.id)
                .where(*conds)
                .order_by(Invoice.transaction_date.desc(), Invoice.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error in sales_ledger")
            raise DatabaseException("Failed to load sales ledger") from exc

    def sales_ledger_total(
        self,
        date_from: date,
        date_to: date,
        currency: str,
        *,
        search: str | None = None,
        status: str | None = None,
    ) -> int:
        """Exact row count for the sales ledger (used when withTotal=true)."""
        entity = customer_display_name()
        conds = _invoice_conds(date_from, date_to, currency)
        if status and status != "all":
            conds.append(Invoice.status == status)
        if search:
            clause = build_search_clause(
                search,
                entity,
                Invoice.invoice_reference,
                Invoice.invoice_number,
            )
            if clause is not None:
                conds.append(clause)
        try:
            stmt = (
                select(func.count())
                .select_from(Invoice)
                .join(Customer, Invoice.customer_id == Customer.id)
                .where(*conds)
            )
            return int(self._db.execute(stmt).scalar() or 0)
        except SQLAlchemyError as exc:
            logger.exception("Database error in sales_ledger_total")
            raise DatabaseException("Failed to count sales ledger") from exc

    def sales_status_counts(
        self, date_from: date, date_to: date, currency: str
    ) -> dict[str, int]:
        """Per-status counts for the filter tabs. Counts recognized statuses only."""
        conds = _invoice_conds(date_from, date_to, currency)
        try:
            stmt = (
                select(Invoice.status, func.count().label("cnt"))
                .select_from(Invoice)
                .where(*conds)
                .group_by(Invoice.status)
            )
            rows = self._db.execute(stmt).all()
            return {row.status: row.cnt for row in rows}
        except SQLAlchemyError as exc:
            logger.exception("Database error in sales_status_counts")
            raise DatabaseException("Failed to count sales by status") from exc

    # Purchases: Summary

    def purchases_summary(
        self, date_from: date, date_to: date, currency: str
    ) -> PurchasesSummary:
        """Combined expense + PO spend metrics."""
        exp_conds = _expense_conds(date_from, date_to, currency)
        po_conds = _po_conds(date_from, date_to, currency)
        try:
            # Expense line totals (pre-tax)
            exp_spend_stmt = (
                select(
                    func.coalesce(func.sum(ExpenseLineItem.line_total), Decimal("0"))
                )
                .select_from(ExpenseLineItem)
                .join(Expense, ExpenseLineItem.expense_id == Expense.id)
                .where(*exp_conds)
            )
            expense_spend = self._db.execute(exp_spend_stmt).scalar() or Decimal("0")

            # Expense doc-level: tax, count, outstanding
            exp_meta_stmt = (
                select(
                    func.coalesce(func.sum(Expense.tax_total), Decimal("0")).label(
                        "tax"
                    ),
                    func.count(Expense.id).label("cnt"),
                    func.coalesce(
                        func.sum(Expense.balance_due).filter(
                            Expense.status.in_(RECOGNIZED_EXPENSE_STATUSES)
                        ),
                        Decimal("0"),
                    ).label("outstanding"),
                )
                .select_from(Expense)
                .where(*exp_conds)
            )
            exp_meta = self._db.execute(exp_meta_stmt).one()

            # PO subtotals (pre-tax; PO.subtotal = stored Σ line_total)
            po_spend_stmt = (
                select(func.coalesce(func.sum(PurchaseOrder.subtotal), Decimal("0")))
                .select_from(PurchaseOrder)
                .where(*po_conds)
            )
            po_spend = self._db.execute(po_spend_stmt).scalar() or Decimal("0")

            # PO doc-level: tax, count, outstanding
            po_meta_stmt = (
                select(
                    func.coalesce(
                        func.sum(PurchaseOrder.tax_total), Decimal("0")
                    ).label("tax"),
                    func.count(PurchaseOrder.id).label("cnt"),
                    func.coalesce(
                        func.sum(PurchaseOrder.balance_due).filter(
                            PurchaseOrder.status == PurchaseOrderStatus.SENT
                        ),
                        Decimal("0"),
                    ).label("outstanding"),
                )
                .select_from(PurchaseOrder)
                .where(*po_conds)
            )
            po_meta = self._db.execute(po_meta_stmt).one()

            expense_spend_d = Decimal(str(expense_spend))
            po_spend_d = Decimal(str(po_spend))
            exp_tax_d = Decimal(str(exp_meta.tax))
            po_tax_d = Decimal(str(po_meta.tax))

            return PurchasesSummary(
                expense_spend=expense_spend_d,
                po_spend=po_spend_d,
                total_spend=expense_spend_d + po_spend_d,
                expense_tax=exp_tax_d,
                po_tax=po_tax_d,
                total_tax=exp_tax_d + po_tax_d,
                expense_count=int(exp_meta.cnt),
                po_count=int(po_meta.cnt),
                outstanding_balance=(
                    Decimal(str(exp_meta.outstanding))
                    + Decimal(str(po_meta.outstanding))
                ),
            )
        except SQLAlchemyError as exc:
            logger.exception("Database error in purchases_summary")
            raise DatabaseException("Failed to calculate purchases summary") from exc

    # Purchases: Breakdowns

    def spend_by_vendor(
        self, date_from: date, date_to: date, currency: str, *, limit: int = 10
    ) -> list:
        """Top N vendors by combined expense + PO spend (pre-tax)."""
        exp_conds = _expense_conds(date_from, date_to, currency)
        po_conds = _po_conds(date_from, date_to, currency)
        try:
            # Expense branch: vendor → pre-tax line total sum
            exp_branch = (
                select(
                    Vendor.vendor_name.label("vendor_name"),
                    func.coalesce(
                        func.sum(ExpenseLineItem.line_total), Decimal("0")
                    ).label("amount"),
                )
                .select_from(ExpenseLineItem)
                .join(Expense, ExpenseLineItem.expense_id == Expense.id)
                .join(Vendor, Expense.vendor_id == Vendor.id)
                .where(*exp_conds)
                .group_by(Vendor.vendor_name)
            )

            # PO branch: vendor → PO subtotal sum
            po_branch = (
                select(
                    Vendor.vendor_name.label("vendor_name"),
                    func.coalesce(func.sum(PurchaseOrder.subtotal), Decimal("0")).label(
                        "amount"
                    ),
                )
                .select_from(PurchaseOrder)
                .join(Vendor, PurchaseOrder.vendor_id == Vendor.id)
                .where(*po_conds)
                .group_by(Vendor.vendor_name)
            )

            # UNION ALL then re-aggregate
            combined = union_all(exp_branch, po_branch).subquery("vendor_spend")
            stmt = (
                select(
                    combined.c.vendor_name,
                    func.sum(combined.c.amount).label("amount"),
                )
                .group_by(combined.c.vendor_name)
                .order_by(func.sum(combined.c.amount).desc())
                .limit(limit)
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error in spend_by_vendor")
            raise DatabaseException("Failed to aggregate spend by vendor") from exc

    # Purchases: Ledger (union_all of expenses + POs)

    def _purchases_subquery(
        self,
        date_from: date,
        date_to: date,
        currency: str,
        *,
        source: str = "all",
        search: str | None = None,
    ):
        """
        Build the union_all subquery for the purchases ledger.

        PurchaseOrder uses `.total` (not `.total_due`) for tax-inclusive amount.
        PurchaseOrderLineItem FK is `po_id` (not `purchase_order_id`).
        """
        branches: list[Select] = []

        if source in ("all", "expense"):
            exp_conds = _expense_conds(date_from, date_to, currency)
            if search:
                clause = build_search_clause(
                    search,
                    Vendor.vendor_name,
                    Expense.expense_reference,
                    Expense.expense_number,
                )
                if clause is not None:
                    exp_conds.append(clause)
            branches.append(
                select(
                    Expense.id.label("source_id"),
                    literal("expense").label("source_type"),
                    Vendor.vendor_name.label("entity_name"),
                    Expense.expense_reference.label("reference"),
                    Expense.expense_number.label("number"),
                    literal("expense").label("category"),
                    Expense.expense_date.label("entry_date"),
                    Expense.status.label("status"),
                    Expense.currency.label("currency"),
                    Expense.total_due.label("amount"),  # Expense uses total_due ✓
                    Expense.tax_total.label("tax"),
                    Expense.balance_due.label("balance_due"),
                )
                .select_from(Expense)
                .join(Vendor, Expense.vendor_id == Vendor.id)
                .where(*exp_conds)
            )

        if source in ("all", "purchase_order"):
            po_conds = _po_conds(date_from, date_to, currency)
            if search:
                clause = build_search_clause(
                    search,
                    Vendor.vendor_name,
                    PurchaseOrder.po_reference,
                    PurchaseOrder.po_number,
                )
                if clause is not None:
                    po_conds.append(clause)
            branches.append(
                select(
                    PurchaseOrder.id.label("source_id"),
                    literal("purchase_order").label("source_type"),
                    Vendor.vendor_name.label("entity_name"),
                    PurchaseOrder.po_reference.label("reference"),
                    PurchaseOrder.po_number.label("number"),
                    literal("purchase_order").label("category"),
                    PurchaseOrder.order_date.label("entry_date"),
                    PurchaseOrder.status.label("status"),
                    PurchaseOrder.currency.label("currency"),
                    PurchaseOrder.total.label(
                        "amount"
                    ),  # PO uses .total NOT .total_due ✓
                    PurchaseOrder.tax_total.label("tax"),
                    PurchaseOrder.balance_due.label("balance_due"),
                )
                .select_from(PurchaseOrder)
                .join(Vendor, PurchaseOrder.vendor_id == Vendor.id)
                .where(*po_conds)
            )

        # Fallback guard: if branches is empty (bad `source` value), return
        # a subquery that always produces zero rows.
        if not branches:
            branches.append(
                select(
                    Expense.id.label("source_id"),
                    literal("expense").label("source_type"),
                    Vendor.vendor_name.label("entity_name"),
                    Expense.expense_reference.label("reference"),
                    Expense.expense_number.label("number"),
                    literal("expense").label("category"),
                    Expense.expense_date.label("entry_date"),
                    Expense.status.label("status"),
                    Expense.currency.label("currency"),
                    Expense.total_due.label("amount"),
                    Expense.tax_total.label("tax"),
                    Expense.balance_due.label("balance_due"),
                )
                .select_from(Expense)
                .where(Expense.id.is_(None))  # always-false predicate
            )

        sel = branches[0] if len(branches) == 1 else union_all(*branches)
        return sel.subquery("purchase_entries")

    def purchases_ledger(
        self,
        date_from: date,
        date_to: date,
        currency: str,
        *,
        source: str = "all",
        search: str | None = None,
        offset: int = 0,
        limit: int = 11,
    ) -> list:
        """One over-fetched page of the purchases ledger. Stable sort: date DESC, id DESC."""
        try:
            sub = self._purchases_subquery(
                date_from, date_to, currency, source=source, search=search
            )
            stmt = (
                select(sub)
                .order_by(sub.c.entry_date.desc(), sub.c.source_id.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error in purchases_ledger")
            raise DatabaseException("Failed to load purchases ledger") from exc

    def purchases_ledger_total(
        self,
        date_from: date,
        date_to: date,
        currency: str,
        *,
        source: str = "all",
        search: str | None = None,
    ) -> int:
        """Exact count for the purchases ledger (used when withTotal=true)."""
        try:
            sub = self._purchases_subquery(
                date_from, date_to, currency, source=source, search=search
            )
            return int(
                self._db.execute(select(func.count()).select_from(sub)).scalar() or 0
            )
        except SQLAlchemyError as exc:
            logger.exception("Database error in purchases_ledger_total")
            raise DatabaseException("Failed to count purchases ledger") from exc

    def purchases_source_counts(
        self,
        date_from: date,
        date_to: date,
        currency: str,
        *,
        search: str | None = None,
    ) -> dict[str, int]:
        """Per-source counts for the filter tabs (always uses source='all')."""
        try:
            sub = self._purchases_subquery(
                date_from, date_to, currency, source="all", search=search
            )
            rows = self._db.execute(
                select(sub.c.source_type, func.count()).group_by(sub.c.source_type)
            ).all()
            counts: dict[str, int] = {row[0]: row[1] for row in rows}
            expense = counts.get("expense", 0)
            po = counts.get("purchase_order", 0)
            return {"all": expense + po, "expense": expense, "purchase_order": po}
        except SQLAlchemyError as exc:
            logger.exception("Database error in purchases_source_counts")
            raise DatabaseException("Failed to count purchases by source") from exc

    # Tax Report

    def tax_summary(self, date_from: date, date_to: date, currency: str) -> TaxSummary:
        """VAT collected (sales), VAT paid (purchases), net VAT position."""
        inv_conds = _invoice_conds(date_from, date_to, currency)
        exp_conds = _expense_conds(date_from, date_to, currency)
        po_conds = _po_conds(date_from, date_to, currency)
        try:
            # Sales VAT: SUM(Invoice.tax_total) — no join needed, stored field
            sales_vat_stmt = (
                select(func.coalesce(func.sum(Invoice.tax_total), Decimal("0")))
                .select_from(Invoice)
                .where(*inv_conds)
            )
            vat_collected = self._db.execute(sales_vat_stmt).scalar() or Decimal("0")

            # Purchase VAT (expenses)
            exp_vat_stmt = (
                select(func.coalesce(func.sum(Expense.tax_total), Decimal("0")))
                .select_from(Expense)
                .where(*exp_conds)
            )
            exp_vat = self._db.execute(exp_vat_stmt).scalar() or Decimal("0")

            # Purchase VAT (POs)
            po_vat_stmt = (
                select(func.coalesce(func.sum(PurchaseOrder.tax_total), Decimal("0")))
                .select_from(PurchaseOrder)
                .where(*po_conds)
            )
            po_vat = self._db.execute(po_vat_stmt).scalar() or Decimal("0")

            vat_collected_d = Decimal(str(vat_collected))
            vat_paid_d = Decimal(str(exp_vat)) + Decimal(str(po_vat))

            return TaxSummary(
                vat_collected=vat_collected_d,
                vat_paid=vat_paid_d,
                net_vat=vat_collected_d - vat_paid_d,
            )
        except SQLAlchemyError as exc:
            logger.exception("Database error in tax_summary")
            raise DatabaseException("Failed to calculate tax summary") from exc

    def tax_by_type_sales(self, date_from: date, date_to: date, currency: str) -> list:
        """Sales VAT breakdown by tax_type (from invoice line items)."""
        inv_conds = _invoice_conds(date_from, date_to, currency)
        try:
            amount = func.coalesce(
                func.sum(InvoiceLineItem.tax_amount), Decimal("0")
            ).label("tax_amount")
            stmt = (
                select(
                    InvoiceLineItem.tax_type.label("tax_type"),
                    func.count(func.distinct(Invoice.id)).label("document_count"),
                    amount,
                )
                .select_from(InvoiceLineItem)
                .join(Invoice, InvoiceLineItem.invoice_id == Invoice.id)
                .where(*inv_conds)
                .group_by(InvoiceLineItem.tax_type)
                .order_by(amount.desc())
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error in tax_by_type_sales")
            raise DatabaseException("Failed to aggregate sales tax by type") from exc

    def tax_by_type_purchases(
        self, date_from: date, date_to: date, currency: str
    ) -> list:
        """Purchase VAT breakdown by tax_type.

        Expenses: ExpenseLineItem.tax_amount grouped by line-level tax_type.
        POs: PurchaseOrder.tax_total mapped to TaxType via vat_rate CASE expression.
        PO line items always have tax_type=no_tax/tax_amount=0 by design
        (neutralize_line_tax in financial.py). Use document-level tax_total instead.
        vat_rate is stored as a fraction (e.g. 0.1600 for 16%).
        """
        exp_conds = _expense_conds(date_from, date_to, currency)
        po_conds = _po_conds(date_from, date_to, currency)
        try:
            # Expense branch: per-line tax_type grouping (correct for expenses)
            exp_branch = (
                select(
                    ExpenseLineItem.tax_type.label("tax_type"),
                    func.coalesce(
                        func.sum(ExpenseLineItem.tax_amount), Decimal("0")
                    ).label("tax_amount"),
                )
                .select_from(ExpenseLineItem)
                .join(Expense, ExpenseLineItem.expense_id == Expense.id)
                .where(*exp_conds)
                .group_by(ExpenseLineItem.tax_type)
            )

            # PO branch: document-level tax, mapped to TaxType via vat_rate.
            # Only POs with vat_enabled=True and tax_total > 0 carry real VAT.
            # vat_rate = 0.1600 → vat_16; 0.0800 → vat_8; 0.0000 → vat_0; else → no_tax
            po_tax_type = case(
                (PurchaseOrder.vat_rate == Decimal("0.1600"), "vat_16"),
                (PurchaseOrder.vat_rate == Decimal("0.0800"), "vat_8"),
                (PurchaseOrder.vat_rate == Decimal("0.0000"), "vat_0"),
                else_="no_tax",
            )
            po_branch = (
                select(
                    po_tax_type.label("tax_type"),
                    func.coalesce(
                        func.sum(PurchaseOrder.tax_total), Decimal("0")
                    ).label("tax_amount"),
                )
                .select_from(PurchaseOrder)
                .where(
                    *po_conds,
                    PurchaseOrder.vat_enabled.is_(True),
                    PurchaseOrder.tax_total > 0,
                )
                .group_by(po_tax_type)
            )

            combined = union_all(exp_branch, po_branch).subquery("purchase_tax_by_type")
            stmt = (
                select(
                    combined.c.tax_type,
                    func.sum(combined.c.tax_amount).label("tax_amount"),
                )
                .group_by(combined.c.tax_type)
                .order_by(func.sum(combined.c.tax_amount).desc())
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error in tax_by_type_purchases")
            raise DatabaseException("Failed to aggregate purchase tax by type") from exc

    # Aged Receivables

    def aged_receivables_summary(self, currency: str) -> list:
        """Flat rows: (customer_name, bucket, amount) for aged AR pivot.

        Buckets: current (not yet due), 1_30, 31_60, 61_90, 90_plus.
        PostgreSQL date subtraction yields INTEGER days. Uses CURRENT_DATE so
        computation is done server-side at query time.
        Only outstanding invoices: SENT / PARTIAL / OVERDUE with balance_due > 0.
        """
        from app.constants.enums import InvoiceStatus

        entity = customer_display_name()
        days_late = func.current_date() - Invoice.due_date
        bucket = case(
            (func.current_date() <= Invoice.due_date, "current"),
            (days_late.between(1, 30), "1_30"),
            (days_late.between(31, 60), "31_60"),
            (days_late.between(61, 90), "61_90"),
            else_="90_plus",
        ).label("bucket")

        try:
            stmt = (
                select(
                    entity.label("customer_name"),
                    bucket,
                    func.coalesce(func.sum(Invoice.balance_due), Decimal("0")).label(
                        "amount"
                    ),
                )
                .select_from(Invoice)
                .join(Customer, Invoice.customer_id == Customer.id)
                .where(
                    Invoice.status.in_(
                        [
                            InvoiceStatus.SENT,
                            InvoiceStatus.PARTIAL,
                            InvoiceStatus.OVERDUE,
                        ]
                    ),
                    Invoice.balance_due > 0,
                    Invoice.currency == currency,
                )
                .group_by(entity, bucket)
                .order_by(entity.asc(), bucket.asc())
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error in aged_receivables_summary")
            raise DatabaseException("Failed to aggregate aged receivables") from exc

    def aged_receivables_totals(self, currency: str) -> dict:
        """Total balance_due bucketed across all outstanding invoices.

        Used for the MetricCards at the top of the Aged Receivables tab.
        Returns {bucket_key: Decimal}.
        """
        from app.constants.enums import InvoiceStatus

        days_late = func.current_date() - Invoice.due_date
        bucket = case(
            (func.current_date() <= Invoice.due_date, "current"),
            (days_late.between(1, 30), "1_30"),
            (days_late.between(31, 60), "31_60"),
            (days_late.between(61, 90), "61_90"),
            else_="90_plus",
        ).label("bucket")

        try:
            stmt = (
                select(
                    bucket,
                    func.coalesce(func.sum(Invoice.balance_due), Decimal("0")).label(
                        "amount"
                    ),
                )
                .select_from(Invoice)
                .where(
                    Invoice.status.in_(
                        [
                            InvoiceStatus.SENT,
                            InvoiceStatus.PARTIAL,
                            InvoiceStatus.OVERDUE,
                        ]
                    ),
                    Invoice.balance_due > 0,
                    Invoice.currency == currency,
                )
                .group_by(bucket)
            )
            rows = self._db.execute(stmt).all()
            return {row.bucket: Decimal(str(row.amount)) for row in rows}
        except SQLAlchemyError as exc:
            logger.exception("Database error in aged_receivables_totals")
            raise DatabaseException("Failed to total aged receivables") from exc

    def aged_receivables_detail(
        self,
        currency: str,
        *,
        offset: int = 0,
        limit: int = 11,
    ) -> list:
        """Individual outstanding invoice rows with bucket and days_overdue.

        Ordered oldest-due first (ascending due_date). Fetch limit+1 for
        sentinel pagination (no COUNT needed).
        days_overdue: negative = not yet due; 0 = due today; positive = overdue.
        """
        from app.constants.enums import InvoiceStatus

        entity = customer_display_name()
        days_late = func.current_date() - Invoice.due_date
        bucket = case(
            (func.current_date() <= Invoice.due_date, "current"),
            (days_late.between(1, 30), "1_30"),
            (days_late.between(31, 60), "31_60"),
            (days_late.between(61, 90), "61_90"),
            else_="90_plus",
        ).label("bucket")

        try:
            stmt = (
                select(
                    Invoice.id.label("id"),
                    entity.label("customer_name"),
                    Invoice.invoice_reference.label("reference"),
                    Invoice.invoice_number.label("number"),
                    Invoice.transaction_date.label("transaction_date"),
                    Invoice.due_date.label("due_date"),
                    Invoice.status.label("status"),
                    Invoice.total_due.label("total_due"),
                    Invoice.amount_paid.label("amount_paid"),
                    Invoice.balance_due.label("balance_due"),
                    bucket,
                    days_late.label("days_overdue"),
                )
                .select_from(Invoice)
                .join(Customer, Invoice.customer_id == Customer.id)
                .where(
                    Invoice.status.in_(
                        [
                            InvoiceStatus.SENT,
                            InvoiceStatus.PARTIAL,
                            InvoiceStatus.OVERDUE,
                        ]
                    ),
                    Invoice.balance_due > 0,
                    Invoice.currency == currency,
                )
                .order_by(Invoice.due_date.asc(), Invoice.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error in aged_receivables_detail")
            raise DatabaseException("Failed to load aged receivables detail") from exc

    # Aged Payables

    def aged_payables_summary(self, currency: str) -> list:
        """Flat rows: (vendor_name, bucket, amount) for aged AP pivot.

        Expenses: buckets by due_date.
        POs: always "current" — PurchaseOrder has no due_date field.
        Returns rows with columns: vendor_name, bucket, amount.
        """
        from app.constants.enums import ExpenseStatus, PurchaseOrderStatus

        # Expense branch: bucket by due_date
        exp_days_late = func.current_date() - Expense.due_date
        exp_bucket = case(
            (func.current_date() <= Expense.due_date, "current"),
            (exp_days_late.between(1, 30), "1_30"),
            (exp_days_late.between(31, 60), "31_60"),
            (exp_days_late.between(61, 90), "61_90"),
            else_="90_plus",
        )
        exp_branch = (
            select(
                Vendor.vendor_name.label("vendor_name"),
                exp_bucket.label("bucket"),
                func.coalesce(func.sum(Expense.balance_due), Decimal("0")).label(
                    "amount"
                ),
            )
            .select_from(Expense)
            .join(Vendor, Expense.vendor_id == Vendor.id)
            .where(
                Expense.status.in_([ExpenseStatus.PENDING, ExpenseStatus.OVERDUE]),
                Expense.balance_due > 0,
                Expense.currency == currency,
            )
            .group_by(Vendor.vendor_name, exp_bucket)
        )

        # PO branch: always "current" (no due_date on PurchaseOrder)
        po_branch = (
            select(
                Vendor.vendor_name.label("vendor_name"),
                literal("current").label("bucket"),
                func.coalesce(func.sum(PurchaseOrder.balance_due), Decimal("0")).label(
                    "amount"
                ),
            )
            .select_from(PurchaseOrder)
            .join(Vendor, PurchaseOrder.vendor_id == Vendor.id)
            .where(
                PurchaseOrder.status == PurchaseOrderStatus.SENT,
                PurchaseOrder.balance_due > 0,
                PurchaseOrder.currency == currency,
            )
            .group_by(Vendor.vendor_name)
        )

        try:
            combined = union_all(exp_branch, po_branch).subquery("payables_flat")
            stmt = (
                select(
                    combined.c.vendor_name,
                    combined.c.bucket,
                    func.sum(combined.c.amount).label("amount"),
                )
                .group_by(combined.c.vendor_name, combined.c.bucket)
                .order_by(combined.c.vendor_name.asc(), combined.c.bucket.asc())
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error in aged_payables_summary")
            raise DatabaseException("Failed to aggregate aged payables") from exc

    def aged_payables_totals(self, currency: str) -> dict:
        """Total outstanding balance by bucket across ALL unpaid expenses + SENT POs.

        Used for the 5 MetricCards on the Aged Payables tab.
        Returns {bucket_key: Decimal}.
        """
        from app.constants.enums import ExpenseStatus, PurchaseOrderStatus

        exp_days_late = func.current_date() - Expense.due_date
        exp_bucket = case(
            (func.current_date() <= Expense.due_date, "current"),
            (exp_days_late.between(1, 30), "1_30"),
            (exp_days_late.between(31, 60), "31_60"),
            (exp_days_late.between(61, 90), "61_90"),
            else_="90_plus",
        )
        exp_branch = (
            select(
                exp_bucket.label("bucket"),
                func.coalesce(func.sum(Expense.balance_due), Decimal("0")).label(
                    "amount"
                ),
            )
            .select_from(Expense)
            .where(
                Expense.status.in_([ExpenseStatus.PENDING, ExpenseStatus.OVERDUE]),
                Expense.balance_due > 0,
                Expense.currency == currency,
            )
            .group_by(exp_bucket)
        )
        po_branch = (
            select(
                literal("current").label("bucket"),
                func.coalesce(func.sum(PurchaseOrder.balance_due), Decimal("0")).label(
                    "amount"
                ),
            )
            .select_from(PurchaseOrder)
            .where(
                PurchaseOrder.status == PurchaseOrderStatus.SENT,
                PurchaseOrder.balance_due > 0,
                PurchaseOrder.currency == currency,
            )
        )

        try:
            combined = union_all(exp_branch, po_branch).subquery("payables_totals")
            stmt = select(
                combined.c.bucket,
                func.sum(combined.c.amount).label("amount"),
            ).group_by(combined.c.bucket)
            rows = self._db.execute(stmt).all()
            return {row.bucket: Decimal(str(row.amount)) for row in rows}
        except SQLAlchemyError as exc:
            logger.exception("Database error in aged_payables_totals")
            raise DatabaseException("Failed to total aged payables") from exc

    def aged_payables_detail(
        self,
        currency: str,
        *,
        offset: int = 0,
        limit: int = 11,
    ) -> list:
        """Individual expense + PO rows for the aged AP detail table.

        Expenses: bucket by due_date; days_overdue = CURRENT_DATE - due_date.
        POs: bucket always "current"; due_date = order_date (display anchor only);
             days_overdue = 0.
        Ordered oldest-due first.
        """
        from app.constants.enums import ExpenseStatus, PurchaseOrderStatus

        exp_days_late = func.current_date() - Expense.due_date
        exp_bucket = case(
            (func.current_date() <= Expense.due_date, "current"),
            (exp_days_late.between(1, 30), "1_30"),
            (exp_days_late.between(31, 60), "31_60"),
            (exp_days_late.between(61, 90), "61_90"),
            else_="90_plus",
        )

        exp_branch = (
            select(
                Expense.id.label("source_id"),
                literal("expense").label("source_type"),
                Vendor.vendor_name.label("vendor_name"),
                Expense.expense_reference.label("reference"),
                Expense.expense_number.label("number"),
                Expense.expense_date.label("entry_date"),
                Expense.due_date.label("due_date"),
                Expense.status.label("status"),
                Expense.total_due.label("total_due"),
                Expense.amount_paid.label("amount_paid"),
                Expense.balance_due.label("balance_due"),
                exp_bucket.label("bucket"),
                exp_days_late.label("days_overdue"),
            )
            .select_from(Expense)
            .join(Vendor, Expense.vendor_id == Vendor.id)
            .where(
                Expense.status.in_([ExpenseStatus.PENDING, ExpenseStatus.OVERDUE]),
                Expense.balance_due > 0,
                Expense.currency == currency,
            )
        )

        po_branch = (
            select(
                PurchaseOrder.id.label("source_id"),
                literal("purchase_order").label("source_type"),
                Vendor.vendor_name.label("vendor_name"),
                PurchaseOrder.po_reference.label("reference"),
                PurchaseOrder.po_number.label("number"),
                PurchaseOrder.order_date.label("entry_date"),
                PurchaseOrder.order_date.label("due_date"),  # display anchor only
                PurchaseOrder.status.label("status"),
                PurchaseOrder.total.label("total_due"),  # PO uses .total not .total_due
                PurchaseOrder.amount_paid.label("amount_paid"),
                PurchaseOrder.balance_due.label("balance_due"),
                literal("current").label("bucket"),
                literal(0).label("days_overdue"),
            )
            .select_from(PurchaseOrder)
            .join(Vendor, PurchaseOrder.vendor_id == Vendor.id)
            .where(
                PurchaseOrder.status == PurchaseOrderStatus.SENT,
                PurchaseOrder.balance_due > 0,
                PurchaseOrder.currency == currency,
            )
        )

        try:
            combined = union_all(exp_branch, po_branch).subquery("payables_detail")
            stmt = (
                select(combined)
                .order_by(combined.c.due_date.asc(), combined.c.source_id.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(self._db.execute(stmt).all())
        except SQLAlchemyError as exc:
            logger.exception("Database error in aged_payables_detail")
            raise DatabaseException("Failed to load aged payables detail") from exc
