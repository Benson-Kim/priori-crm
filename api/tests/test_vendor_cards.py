"""
Vendor "Supplier Statements" cards (PO-33).

Three read-only cards on the vendor Overview tab:
- Total POs: non-DRAFT POs + paid/pending/overpaid summary (no overdue bucket).
- Total Payments: the vendor's PO payments (joined to their PO) + doc presence.
- Total Invoices: the vendor's bills, sourced from the expenses ledger.

The bucketing (derive_po_status) is a pure function tested unconditionally.
The DB-backed cases create POs/payments/expenses, so they use the advisory-
lock reference-generation path and are PostgreSQL-only (matching the rest of
the PO/vendor suite). Every card query is vendor-scoped, read-only, and shares
the single derived-status/filter source in vendors/queries.py.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.exceptions import NotFoundException
from app.constants.enums import Currency, PurchaseOrderStatus, TaxType, VendorStatus
from app.modules.expenses.schemas import ExpenseCreate, ExpenseLineItemCreate
from app.modules.expenses.service import ExpenseService
from app.modules.purchase_orders.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderLineItemCreate,
    PurchaseOrderPaymentCreate,
)
from app.modules.purchase_orders.service import PurchaseOrderService
from app.modules.vendors.models import Vendor
from app.modules.vendors.queries import VendorCardsRepository, derive_po_status
from app.modules.vendors.service import VendorService
from tests.conftest import USING_POSTGRES

pg_only = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="PO/expense create uses advisory-lock reference generation (PostgreSQL only)",
)


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
        "tax_type": TaxType.NO_TAX,
    }
    base.update(kw)
    return PurchaseOrderLineItemCreate(**base)


def _make_po(db, vendor, *, order_date=None, **kw):
    svc = PurchaseOrderService(db)
    payload = PurchaseOrderCreate(
        vendor_id=vendor.id,
        order_date=order_date or date.today(),
        line_items=[_po_line()],
        **kw,
    )
    return svc.create(payload)


def _send(db, po) -> None:
    po.status = PurchaseOrderStatus.SENT
    db.flush()


def _pay(db, po, amount) -> None:
    PurchaseOrderService(db).record_payment(
        po.id,
        PurchaseOrderPaymentCreate(
            amount=Decimal(amount),
            paymentDate=date.today(),
            reference="TXN-CARD",
            currency="KES",
            exchangeRate=Decimal("1"),
        ),
    )
    db.flush()


def _make_expense(db, vendor, *, expense_date=None):
    today = date.today()
    payload = ExpenseCreate(
        vendor_id=vendor.id,
        expense_date=expense_date or today,
        due_date=today + timedelta(days=30),
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
    return ExpenseService(db).create(payload)


# PURE BUCKETING


class TestDerivePoStatus:
    def test_paid_exact(self):
        assert derive_po_status(PurchaseOrderStatus.PAID, Decimal("0.00")) == "paid"

    def test_overpaid_when_balance_negative(self):
        assert (
            derive_po_status(PurchaseOrderStatus.PAID, Decimal("-5.00")) == "overpaid"
        )

    def test_pending_when_sent(self):
        assert (
            derive_po_status(PurchaseOrderStatus.SENT, Decimal("100.00")) == "pending"
        )

    def test_sent_zero_balance_is_still_pending(self):
        # Only status PAID maps to paid/overpaid; a SENT PO is always pending.
        assert derive_po_status(PurchaseOrderStatus.SENT, Decimal("0.00")) == "pending"


# CARD 1 — TOTAL POs


@pg_only
class TestPurchaseOrdersCard:
    def test_draft_excluded_from_list_and_counts(self, db):
        vendor = _vendor(db)
        _make_po(db, vendor)  # DRAFT — must not appear
        sent = _make_po(db, vendor)
        _send(db, sent)

        card = VendorCardsRepository(db).purchase_orders(vendor.id)
        refs = {r.po_reference for r in card.rows}
        assert sent.po_reference in refs
        assert len(card.rows) == 1  # DRAFT excluded
        assert card.pending == 1
        assert card.paid == 0
        assert card.overpaid == 0

    def test_buckets_paid_pending_overpaid(self, db):
        vendor = _vendor(db)
        # pending: sent, unpaid
        p = _make_po(db, vendor)
        _send(db, p)
        # paid exactly
        paid = _make_po(db, vendor)
        _send(db, paid)
        _pay(db, paid, paid.total)
        # overpaid
        over = _make_po(db, vendor)
        _send(db, over)
        _pay(db, over, over.total + Decimal("10.00"))

        card = VendorCardsRepository(db).purchase_orders(vendor.id)
        assert card.pending == 1
        assert card.paid == 1
        assert card.overpaid == 1
        # every non-DRAFT PO is listed exactly once
        assert len(card.rows) == 3
        assert {r.derived_status for r in card.rows} == {"pending", "paid", "overpaid"}

    def test_order_date_range_filter(self, db):
        vendor = _vendor(db)
        old = _make_po(db, vendor, order_date=date.today() - timedelta(days=40))
        _send(db, old)
        recent = _make_po(db, vendor, order_date=date.today())
        _send(db, recent)

        card = VendorCardsRepository(db).purchase_orders(
            vendor.id, date_from=date.today() - timedelta(days=7)
        )
        refs = {r.po_reference for r in card.rows}
        assert recent.po_reference in refs
        assert old.po_reference not in refs
        assert card.pending == 1


# CARD 2 — TOTAL PAYMENTS


@pg_only
class TestPaymentsCard:
    def test_lists_vendor_po_payments_with_doc_presence(self, db):
        vendor = _vendor(db)
        po = _make_po(db, vendor)
        _send(db, po)
        _pay(db, po, Decimal("50.00"))

        card = VendorCardsRepository(db).po_payments(vendor.id)
        assert len(card.rows) == 1
        row = card.rows[0]
        assert row.po_reference == po.po_reference
        assert row.reference == "TXN-CARD"
        assert row.amount == Decimal("50.00")
        # No proof document attached in this test.
        assert row.has_document is False
        assert card.total_amount == Decimal("50.00")

    def test_payment_date_range_filter(self, db):
        vendor = _vendor(db)
        po = _make_po(db, vendor)
        _send(db, po)
        _pay(db, po, Decimal("25.00"))  # dated today

        # A window entirely in the past excludes today's payment.
        card = VendorCardsRepository(db).po_payments(
            vendor.id,
            date_from=date.today() - timedelta(days=10),
            date_to=date.today() - timedelta(days=1),
        )
        assert card.rows == []
        assert card.total_amount == Decimal("0.00")


# CARD 3 — TOTAL INVOICES


@pg_only
class TestInvoicesCard:
    def test_lists_vendor_expenses(self, db):
        vendor = _vendor(db)
        exp = _make_expense(db, vendor)

        card = VendorCardsRepository(db).invoices(vendor.id)
        assert len(card.rows) == 1
        row = card.rows[0]
        assert row.ref_no == exp.expense_number
        assert row.amount == exp.total_due
        assert card.total_amount == exp.total_due

    def test_expense_date_range_filter(self, db):
        vendor = _vendor(db)
        _make_expense(db, vendor, expense_date=date.today() - timedelta(days=40))
        recent = _make_expense(db, vendor, expense_date=date.today())

        card = VendorCardsRepository(db).invoices(
            vendor.id, date_from=date.today() - timedelta(days=7)
        )
        refs = {r.ref_no for r in card.rows}
        assert recent.expense_number in refs
        assert len(card.rows) == 1


# SERVICE GUARD + EXPORT PARITY


@pg_only
class TestServiceAndExport:
    def test_unknown_vendor_raises_404(self, db):
        import uuid

        svc = VendorService(db)
        with pytest.raises(NotFoundException):
            svc.get_purchase_orders_card(uuid.uuid4())

    def test_excel_export_consumes_card_rows(self, db):
        from app.common.excel import ExcelExporter

        vendor = _vendor(db)
        po = _make_po(db, vendor)
        _send(db, po)

        card = VendorService(db).get_purchase_orders_card(vendor.id)
        # Export parity: the exporter serialises exactly the on-screen rows.
        xlsx = ExcelExporter().export_vendor_purchase_orders(card.rows)
        assert isinstance(xlsx, bytes)
        assert len(xlsx) > 0

    def test_pdf_export_renders_for_each_card(self, db):
        from app.common.card_pdf import CardPdfExporter

        vendor = _vendor(db)
        po = _make_po(db, vendor)
        _send(db, po)
        _pay(db, po, Decimal("10.00"))
        _make_expense(db, vendor)

        svc = VendorService(db)
        exporter = CardPdfExporter(db)

        po_pdf = exporter.export_purchase_orders(svc.get_purchase_orders_card(vendor.id))
        pay_pdf = exporter.export_payments(svc.get_payments_card(vendor.id))
        inv_pdf = exporter.export_invoices(svc.get_invoices_card(vendor.id))

        for pdf in (po_pdf, pay_pdf, inv_pdf):
            assert isinstance(pdf, bytes)
            # A real PDF stream starts with the %PDF magic bytes.
            assert pdf[:4] == b"%PDF"

    def test_pdf_export_handles_empty_card(self, db):
        # An empty card must still render a valid (headers-only) PDF, not error.
        from app.common.card_pdf import CardPdfExporter

        vendor = _vendor(db)
        card = VendorService(db).get_purchase_orders_card(vendor.id)
        pdf = CardPdfExporter(db).export_purchase_orders(card)
        assert pdf[:4] == b"%PDF"
