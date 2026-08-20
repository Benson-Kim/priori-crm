"""
Vendor detail summary cards (Total POs / Total Payments / Total Bills).

Proves the three card builders return correct aggregates (total / paid /
pending / count), a paid-or-pending tag per row, honour the date filter, and
that the Payments card unions PO payments with expense payments. Uses the same
portable SQL as the transaction-list tests; like every schema-building test
they require a Postgres DATABASE_URL (skipped at collection time otherwise).
"""

from datetime import date, timedelta
from decimal import Decimal

from app.constants.enums import Currency, TaxType, VendorStatus
from app.modules.expenses.schemas import (
    ExpenseCreate,
    ExpenseLineItemCreate,
    ExpensePaymentCreate,
)
from app.modules.expenses.service import ExpenseService
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
    PurchaseOrderPaymentCreate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
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


def _make_po(db, vendor, **kw):
    svc = PurchaseOrderService(db)
    kw.setdefault("vat_enabled", False)
    payload = PurchaseOrderCreate(
        vendor_id=vendor.id,
        order_date=kw.pop("order_date", date.today()),
        line_items=[
            PurchaseOrderLineItemCreate(
                item_name="Steel bolts",
                description="Box",
                quantity=Decimal("2"),
                unit_price=Decimal("100.00"),
                tax_type=TaxType.NO_TAX,
            )
        ],
        **kw,
    )
    return svc.create(payload)


def _make_expense(db, vendor, **kw):
    svc = ExpenseService(db)
    today = date.today()
    payload = ExpenseCreate(
        vendor_id=vendor.id,
        expense_date=kw.pop("expense_date", today),
        due_date=kw.pop("due_date", today + timedelta(days=30)),
        line_items=[
            ExpenseLineItemCreate(
                item_name="Office rent",
                description="Monthly",
                quantity=Decimal("1"),
                unit_price=Decimal("500.00"),
                tax_type=TaxType.NO_TAX,
            )
        ],
    )
    return svc.create(payload)


def _wide_period() -> tuple[date, date]:
    return date.today() - timedelta(days=365), date.today() + timedelta(days=1)


# BILLS CARD


class TestBillsCard:
    def test_totals_and_paid_pending_split(self, db):
        vendor = _vendor(db)
        _make_expense(db, vendor)  # 500, unpaid -> pending
        start, end = _wide_period()

        card = VendorService(db).get_bills_card(vendor.id, start, end)

        assert card.count == 1
        assert card.total == Decimal("500.00")
        assert card.pending_total == Decimal("500.00")
        assert card.paid_total == Decimal("0.00")
        assert card.items[0].payment_state == "pending"
        assert card.items[0].source == "bill"

    def test_paid_expense_tagged_paid(self, db):
        vendor = _vendor(db)
        expense = _make_expense(db, vendor)
        ExpenseService(db).record_payment(
            expense.id,
            ExpensePaymentCreate(amount=Decimal("500.00"), payment_date=date.today()),
        )
        start, end = _wide_period()

        card = VendorService(db).get_bills_card(vendor.id, start, end)
        assert card.paid_total == Decimal("500.00")
        assert card.items[0].payment_state == "paid"


# PO CARD


class TestPurchaseOrdersCard:
    def test_draft_po_excluded(self, db):
        vendor = _vendor(db)
        _make_po(db, vendor)  # DRAFT
        start, end = _wide_period()

        card = VendorService(db).get_purchase_orders_card(vendor.id, start, end)
        # A draft is not yet a commitment — the card omits it.
        assert card.count == 0
        assert card.total == Decimal("0.00")

    def test_sent_po_counted_as_pending(self, db):
        vendor = _vendor(db)
        po = _make_po(db, vendor)
        PurchaseOrderService(db).mark_as_sent(po.id)
        start, end = _wide_period()

        card = VendorService(db).get_purchase_orders_card(vendor.id, start, end)
        assert card.count == 1
        assert card.total == Decimal("200.00")
        assert card.pending_total == Decimal("200.00")
        assert card.items[0].payment_state == "pending"
        assert card.items[0].source == "purchase_order"


# PAYMENTS CARD


class TestPaymentsCard:
    def test_unions_po_and_expense_payments_all_paid(self, db):
        vendor = _vendor(db)

        # Expense payment leg.
        expense = _make_expense(db, vendor)
        ExpenseService(db).record_payment(
            expense.id,
            ExpensePaymentCreate(amount=Decimal("200.00"), payment_date=date.today()),
        )

        # PO payment leg (PO must be SENT before it can take a payment).
        po = _make_po(db, vendor)
        po_svc = PurchaseOrderService(db)
        po_svc.mark_as_sent(po.id)
        po_svc.record_payment(
            po.id,
            PurchaseOrderPaymentCreate(
                amount=Decimal("120.00"),
                payment_date=date.today(),
                reference="CHQ-001",
                currency="KES",
                exchange_rate=Decimal("1.0"),
            ),
        )

        start, end = _wide_period()
        card = VendorService(db).get_payments_card(vendor.id, start, end)

        assert card.count == 2
        assert card.total == Decimal("320.00")
        assert card.paid_total == Decimal("320.00")
        assert card.pending_total == Decimal("0.00")
        assert {i.source for i in card.items} == {"po_payment", "expense_payment"}
        assert all(i.payment_state == "paid" for i in card.items)


# DATE FILTER


class TestDateFilter:
    def test_out_of_range_rows_excluded(self, db):
        vendor = _vendor(db)
        _make_expense(db, vendor, expense_date=date.today() - timedelta(days=400))

        # Window only covers the last year -> the old expense is excluded.
        start = date.today() - timedelta(days=30)
        end = date.today() + timedelta(days=1)
        card = VendorService(db).get_bills_card(vendor.id, start, end)
        assert card.count == 0
