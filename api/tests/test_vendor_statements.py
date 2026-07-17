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
