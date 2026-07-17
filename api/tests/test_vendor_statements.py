"""Vendor statement correctness tests."""

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


def _make_po(db, vendor, **kw):
    svc = PurchaseOrderService(db)
    kw.setdefault("vat_enabled", False)
    payload = PurchaseOrderCreate(
        vendor_id=vendor.id,
        order_date=kw.pop("order_date", date.today()),
        line_items=[
            PurchaseOrderLineItemCreate(
                item_name="Office supplies",
                description="Pens, notebooks, etc.",
                quantity=Decimal("10"),
                unit_price=Decimal("5.00"),
                tax_type=TaxType.NO_TAX,
            )
        ],
        **kw,
    )
    return svc.create(payload)


def test_vendor_statement_excludes_canceled_expenses_and_their_payments(db):
    vendor = _vendor(db)
    expense_date = date.today() - timedelta(days=1)
    expense = _make_expense(db, vendor, expense_date=expense_date)

    expense_service = ExpenseService(db)
    expense_service.record_payment(
        expense.id,
        ExpensePaymentCreate(
            amount=Decimal("100.00"),
            payment_date=date.today(),
        ),
    )
    expense_service.cancel(expense.id)

    statement = VendorService(db).generate_statement(
        vendor.id,
        date.today() - timedelta(days=30),
        date.today() + timedelta(days=1),
    )

    assert statement.summary.opening_balance == Decimal("0.00")
    assert statement.summary.invoiced_amount == Decimal("0.00")
    assert statement.summary.amount_paid == Decimal("0.00")
    assert statement.summary.balance_due == Decimal("0.00")
    assert [txn.description for txn in statement.transactions] == ["Opening Balance"]


def test_vendor_statement_includes_sent_po_and_po_payment(db):
    vendor = _vendor(db)
    today = date.today()

    sent_po = _make_po(db, vendor, order_date=today)

    po_service = PurchaseOrderService(db)
    po_service.mark_as_sent(sent_po.id)
    po_service.record_payment(
        sent_po.id,
        PurchaseOrderPaymentCreate(
            amount=Decimal("75.00"),
            payment_date=today,
            reference="PO-PMT-001",
            currency="KES",
            exchange_rate=Decimal("1.0"),
        ),
    )

    statement = VendorService(db).generate_statement(
        vendor.id,
        today - timedelta(days=1),
        today + timedelta(days=1),
    )

    expected_paid = Decimal("75.00")
    expected_balance = sent_po.total - expected_paid
    assert statement.summary.opening_balance == Decimal("0.00")
    assert statement.summary.invoiced_amount == sent_po.total
    assert statement.summary.amount_paid == expected_paid
    assert statement.summary.balance_due == expected_balance

    po_rows = [
        txn for txn in statement.transactions if txn.source_type == "purchase_order"
    ]
    assert len(po_rows) == 1
    assert po_rows[0].source_id == sent_po.id
    assert po_rows[0].description == "Purchase Order"
    assert po_rows[0].reference == sent_po.po_number

    po_payment_rows = [
        txn for txn in statement.transactions if txn.source_type == "po_payment"
    ]
    assert len(po_payment_rows) == 1
    assert po_payment_rows[0].reference == "PO-PMT-001"
    assert po_payment_rows[0].detail.endswith(f"for {sent_po.po_number}")


def test_vendor_statement_opening_balance_includes_prior_sent_po_and_payment(db):
    vendor = _vendor(db)
    today = date.today()
    period_start = today

    prior_po = _make_po(db, vendor, order_date=period_start - timedelta(days=10))
    po_service = PurchaseOrderService(db)
    po_service.mark_as_sent(prior_po.id)
    po_service.record_payment(
        prior_po.id,
        PurchaseOrderPaymentCreate(
            amount=Decimal("40.00"),
            payment_date=period_start - timedelta(days=5),
            reference="PO-PMT-PRIOR",
            currency="KES",
            exchange_rate=Decimal("1.0"),
        ),
    )

    statement = VendorService(db).generate_statement(
        vendor.id,
        period_start,
        period_start + timedelta(days=1),
    )

    expected_opening = prior_po.total - Decimal("40.00")
    assert statement.summary.opening_balance == expected_opening
    assert statement.summary.invoiced_amount == Decimal("0.00")
    assert statement.summary.amount_paid == Decimal("0.00")
    assert statement.summary.balance_due == expected_opening


def test_vendor_statement_pdf_and_excel_exports(db):
    vendor = _vendor(db)
    today = date.today()
    expense = _make_expense(db, vendor, expense_date=today)
    ExpenseService(db).record_payment(
        expense.id,
        ExpensePaymentCreate(
            amount=Decimal("50.00"),
            payment_date=today,
            reference="EXP-PMT-001",
            currency="KES",
            exchange_rate=Decimal("1.0"),
        ),
    )

    service = VendorService(db)
    pdf_bytes, pdf_statement = service.generate_statement_pdf(
        vendor_id=vendor.id,
        period_start=today - timedelta(days=1),
        period_end=today + timedelta(days=1),
    )
    xlsx_bytes, xlsx_statement = service.generate_statement_excel(
        vendor_id=vendor.id,
        period_start=today - timedelta(days=1),
        period_end=today + timedelta(days=1),
    )

    assert pdf_bytes.startswith(b"%PDF")
    assert xlsx_bytes.startswith(b"PK")
    assert len(pdf_statement.transactions) == len(xlsx_statement.transactions)


def test_vendor_statement_rows_are_structured_for_display(db):
    vendor = _vendor(db)
    today = date.today()
    expense = _make_expense(db, vendor, expense_date=today)

    ExpenseService(db).record_payment(
        expense.id,
        ExpensePaymentCreate(
            amount=Decimal("100.00"),
            payment_date=today,
            reference="EXP-PMT-001",
        ),
    )

    statement = VendorService(db).generate_statement(
        vendor.id,
        today - timedelta(days=1),
        today + timedelta(days=1),
    )

    opening, expense_row, payment_row = statement.transactions
    assert opening.source_type == "opening_balance"
    assert opening.detail == "Balance carried forward"

    assert expense_row.source_type == "expense"
    assert expense_row.source_id == expense.id
    assert expense_row.description == "Expense"
    assert expense_row.reference == expense.expense_number

    assert payment_row.source_type == "expense_payment"
    assert payment_row.description == "Payment Made"
    assert payment_row.reference == "EXP-PMT-001"
    assert payment_row.detail == (
        f"{vendor.currency} 100.00 for {expense.expense_number}"
    )
    assert "—" not in payment_row.description
