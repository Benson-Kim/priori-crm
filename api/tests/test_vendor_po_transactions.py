"""
PO-13 — Purchase orders in the Vendor Detail transaction list.

Proves the shared vendor transaction-list builder now surfaces PO rows
alongside expenses (source_type/transaction_type = 'purchase_order'), in a
single paginated query, and that purchase orders stay OUT of the vendor
statement (PRD D9 — only the resulting Bill drives financials).

The union uses portable SQL, so these run on SQLite and Postgres alike; the
no-N+1 assertion counts emitted statements and is dialect-independent.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.common.pagination import PaginationParams
from app.constants.enums import (
    Currency,
    ExpenseStatus,
    PurchaseOrderStatus,
    TaxType,
    VendorStatus,
)
from app.modules.expenses.schemas import ExpenseCreate, ExpenseLineItemCreate
from app.modules.expenses.service import ExpenseService
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
from app.modules.vendors.schemas import VendorTransactionFilterParams
from app.modules.vendors.service import VendorService

# HELPERS


def _vendor(db, *, name="Acme Supplies", currency=Currency.KES) -> Vendor:
    vendor = Vendor(
        vendor_name=name,
        currency=currency,
        status=VendorStatus.ACTIVE,
        version=1,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _po_line(**kw) -> PurchaseOrderLineItemCreate:
    base = {
        "item_name": "Steel bolts M8",
        "description": "Box of 500",
        "quantity": Decimal("2"),
        "unit_price": Decimal("100.00"),
        "tax_type": TaxType.VAT_16,
    }
    base.update(kw)
    return PurchaseOrderLineItemCreate(**base)


def _make_po(db, vendor, **kw):
    svc = PurchaseOrderService(db)
    payload = PurchaseOrderCreate(
        vendor_id=vendor.id,
        order_date=kw.pop("order_date", date.today()),
        line_items=[_po_line()],
        **kw,
    )
    return svc.create(payload)


def _exp_line(**kw) -> ExpenseLineItemCreate:
    base = {
        "item_name": "Office rent",
        "description": "Monthly",
        "quantity": Decimal("1"),
        "unit_price": Decimal("500.00"),
        "tax_type": TaxType.VAT_16,
    }
    base.update(kw)
    return ExpenseLineItemCreate(**base)


def _make_expense(db, vendor, **kw):
    svc = ExpenseService(db)
    today = date.today()
    payload = ExpenseCreate(
        vendor_id=vendor.id,
        expense_date=kw.pop("expense_date", today),
        due_date=kw.pop("due_date", today + timedelta(days=30)),
        line_items=[_exp_line()],
    )
    return svc.create(payload)


def _txn_list(db, vendor_id, *, status=None, ttype=None, per_page=50):
    svc = VendorService(db)
    return svc.get_vendor_transactions(
        vendor_id,
        PaginationParams(page=1, per_page=per_page, with_total=True),
        VendorTransactionFilterParams(status=status, transaction_type=ttype),
    )


# PO ROWS APPEAR IN THE LIST


class TestPurchaseOrdersInList:
    def test_po_row_included_with_source_type(self, db):
        vendor = _vendor(db)
        po = _make_po(db, vendor)

        page = _txn_list(db, vendor.id)
        rows = {r.ref_no: r for r in page.items}

        assert po.po_reference in rows
        row = rows[po.po_reference]
        assert row.transaction_type == "purchase_order"
        assert row.id == po.id
        assert row.status == PurchaseOrderStatus.DRAFT.value

    def test_po_amount_is_total_balance_is_zero(self, db):
        vendor = _vendor(db)
        po = _make_po(db, vendor)

        page = _txn_list(db, vendor.id)
        row = next(r for r in page.items if r.transaction_type == "purchase_order")

        # 2 x 100 = 200 + 16% tax = 232 total; PO is non-payable -> balance 0.
        assert row.reference == po.reference
        assert row.amount == Decimal("232.00")
        assert row.balance == Decimal("0.00")

    def test_po_due_date_maps_delivery_date(self, db):
        vendor = _vendor(db)
        delivery = date.today() + timedelta(days=10)
        po = _make_po(db, vendor, delivery_date=delivery)

        page = _txn_list(db, vendor.id)
        row = next(r for r in page.items if r.id == po.id)
        assert row.due_date == delivery

    def test_expenses_and_pos_listed_together_newest_first(self, db):
        vendor = _vendor(db)
        # Older expense, newer PO.
        _make_expense(db, vendor, expense_date=date.today() - timedelta(days=5))
        _make_po(db, vendor, order_date=date.today())

        page = _txn_list(db, vendor.id)
        types = [r.transaction_type for r in page.items]
        assert "expense" in types
        assert "purchase_order" in types
        # Newest-first ordering: the PO (today) precedes the older expense.
        assert page.items[0].transaction_type == "purchase_order"


# FILTERS


class TestFilters:
    def test_type_filter_scopes_to_purchase_orders(self, db):
        vendor = _vendor(db)
        _make_expense(db, vendor)
        _make_po(db, vendor)

        page = _txn_list(db, vendor.id, ttype="purchase_order")
        assert page.items
        assert all(r.transaction_type == "purchase_order" for r in page.items)

    def test_type_filter_scopes_to_expenses(self, db):
        vendor = _vendor(db)
        _make_expense(db, vendor)
        _make_po(db, vendor)

        page = _txn_list(db, vendor.id, ttype="expense")
        assert page.items
        assert all(r.transaction_type == "expense" for r in page.items)

    def test_status_filter_matches_po_status(self, db):
        vendor = _vendor(db)
        _make_po(db, vendor)  # DRAFT

        draft = _txn_list(db, vendor.id, status="draft")
        assert draft.items
        assert all(r.status == "draft" for r in draft.items)

        # No PO is SENT, so the sent filter excludes it.
        sent = _txn_list(db, vendor.id, status="sent")
        assert all(r.transaction_type != "purchase_order" for r in sent.items)


# NO N+1: single combined query regardless of row count


class TestNoNPlusOne:
    def test_query_count_constant_as_rows_grow(self, db):
        vendor = _vendor(db)
        for _ in range(5):
            _make_po(db, vendor)
            _make_expense(db, vendor)
        db.flush()

        statements: list[str] = []

        def _before_cursor(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(db.bind, "before_cursor_execute", _before_cursor)
        try:
            page = _txn_list(db, vendor.id, per_page=50)
        finally:
            event.remove(db.bind, "before_cursor_execute", _before_cursor)

        # 10 transactions present, all on one page.
        assert len([r for r in page.items]) == 10
        # The list is a vendor-existence check + COUNT + one windowed SELECT
        # over the union. It must not scale with the number of rows: a small,
        # constant SELECT budget (no per-row/per-source follow-up query).
        assert len(statements) <= 4, statements


# PO EXCLUDED FROM STATEMENTS / CASHFLOW (PRD D9)


class TestStatementExclusion:
    def test_purchase_orders_do_not_affect_statement(self, db):
        vendor = _vendor(db)
        _make_po(db, vendor)  # must NOT appear in the statement

        svc = VendorService(db)
        start = date.today() - timedelta(days=30)
        end = date.today() + timedelta(days=1)
        statement = svc.generate_statement(vendor.id, start, end)

        # No PO line and a zero invoiced amount: only expenses/bills move the
        # vendor balance; the PO is invisible to financials until billed.
        assert statement.summary.invoiced_amount == Decimal("0.00")
        assert all(
            "PO-" not in t.description for t in statement.transactions
        )

    def test_statement_counts_expense_but_not_po(self, db):
        vendor = _vendor(db)
        _make_expense(db, vendor, expense_date=date.today())
        _make_po(db, vendor, order_date=date.today())

        svc = VendorService(db)
        start = date.today() - timedelta(days=1)
        end = date.today() + timedelta(days=1)
        statement = svc.generate_statement(vendor.id, start, end)

        # The expense (500 + 16% = 580) is invoiced; the PO adds nothing.
        assert statement.summary.invoiced_amount == Decimal("580.00")
