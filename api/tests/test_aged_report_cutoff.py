"""Future-dated documents must not appear in point-in-time aged reports."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import app.modules.reports.audit as report_audit
import app.modules.reports.router as report_router
from app.common.dependencies import get_current_user
from app.constants.enums import (
    Currency,
    ExpenseStatus,
    InvoiceStatus,
    UserRole,
)
from app.main import app
from app.modules.customers.models import Customer
from app.modules.expenses.models import Expense
from app.modules.invoices.models import Invoice
from app.modules.reports.queries import ReportsRepository
from app.modules.vendors.models import Vendor

AS_OF_DATE = date(2026, 1, 31)


def _customer(db, name: str) -> Customer:
    customer = Customer(
        customer_type="business",
        company_name=name,
        first_name="Jane",
        last_name="Doe",
        email=f"{uuid4().hex}@example.test",
        phone="0700000000",
        currency=Currency.KES,
    )
    db.add(customer)
    db.flush()
    return customer


def _vendor(db, name: str) -> Vendor:
    vendor = Vendor(
        vendor_name=name,
        email=f"{uuid4().hex}@vendor.test",
        status="active",
        currency=Currency.KES,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _invoice(
    db,
    customer: Customer,
    *,
    transaction_date: date,
    due_date: date,
    amount: Decimal,
) -> Invoice:
    invoice = Invoice(
        invoice_number=f"INV-{uuid4().hex[:12]}",
        invoice_reference=f"IN-{uuid4().hex[:8]}",
        customer_id=customer.id,
        transaction_date=transaction_date,
        due_date=due_date,
        status=InvoiceStatus.SENT,
        currency=Currency.KES,
        subtotal=amount,
        tax_total=Decimal("0.00"),
        total_due=amount,
        amount_paid=Decimal("0.00"),
        balance_due=amount,
    )
    db.add(invoice)
    db.flush()
    return invoice


def _expense(
    db,
    vendor: Vendor,
    *,
    expense_date: date,
    due_date: date,
    amount: Decimal,
) -> Expense:
    expense = Expense(
        expense_number=f"EXP-{uuid4().hex[:12]}",
        expense_reference=f"EX-{uuid4().hex[:8]}",
        vendor_id=vendor.id,
        expense_date=expense_date,
        due_date=due_date,
        status=ExpenseStatus.PENDING,
        currency=Currency.KES,
        subtotal=amount,
        tax_total=Decimal("0.00"),
        total_due=amount,
        amount_paid=Decimal("0.00"),
        balance_due=amount,
    )
    db.add(expense)
    db.flush()
    return expense


def _install_accounting_date(monkeypatch) -> None:
    monkeypatch.setattr(report_router, "reporting_date", lambda: AS_OF_DATE)
    monkeypatch.setattr(report_audit, "reporting_date", lambda: AS_OF_DATE)


def _install_admin() -> None:
    actor = SimpleNamespace(id=uuid4(), role=UserRole.ADMIN)
    app.dependency_overrides[get_current_user] = lambda: actor


def _money(value) -> Decimal:
    return Decimal(str(value))


def test_aged_receivables_exclude_future_invoices_from_every_query_and_endpoint(
    client,
    db,
    monkeypatch,
):
    _install_accounting_date(monkeypatch)
    _install_admin()

    existing_customer = _customer(db, "Existing customer")
    future_customer = _customer(db, "Future customer")
    existing = _invoice(
        db,
        existing_customer,
        transaction_date=AS_OF_DATE - timedelta(days=30),
        due_date=AS_OF_DATE - timedelta(days=10),
        amount=Decimal("100.00"),
    )
    future = _invoice(
        db,
        future_customer,
        transaction_date=AS_OF_DATE + timedelta(days=1),
        due_date=AS_OF_DATE + timedelta(days=31),
        amount=Decimal("900.00"),
    )
    db.commit()

    totals = ReportsRepository(db).aged_receivables_totals("KES", AS_OF_DATE)
    assert sum(totals.values(), Decimal("0")) == Decimal("100.00")

    try:
        summary_response = client.get(
            "/api/v1/reports/aged-receivables",
            params={"currency": "KES"},
        )
        detail_response = client.get(
            "/api/v1/reports/aged-receivables/detail",
            params={"currency": "KES", "page": 1, "per_page": 25},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert _money(summary["totals"]["total"]) == Decimal("100.00")
    assert {row["customer_id"] for row in summary["customers"]} == {
        str(existing_customer.id)
    }

    assert detail_response.status_code == 200
    detail_ids = {row["id"] for row in detail_response.json()["items"]}
    assert detail_ids == {str(existing.id)}
    assert str(future.id) not in detail_ids


def test_aged_payables_exclude_future_expenses_from_every_query_and_endpoint(
    client,
    db,
    monkeypatch,
):
    _install_accounting_date(monkeypatch)
    _install_admin()

    existing_vendor = _vendor(db, "Existing vendor")
    future_vendor = _vendor(db, "Future vendor")
    existing = _expense(
        db,
        existing_vendor,
        expense_date=AS_OF_DATE - timedelta(days=40),
        due_date=AS_OF_DATE - timedelta(days=20),
        amount=Decimal("200.00"),
    )
    future = _expense(
        db,
        future_vendor,
        expense_date=AS_OF_DATE + timedelta(days=1),
        due_date=AS_OF_DATE + timedelta(days=15),
        amount=Decimal("800.00"),
    )
    db.commit()

    totals = ReportsRepository(db).aged_payables_totals("KES", AS_OF_DATE)
    assert sum(totals.values(), Decimal("0")) == Decimal("200.00")

    try:
        summary_response = client.get(
            "/api/v1/reports/aged-payables",
            params={"currency": "KES"},
        )
        detail_response = client.get(
            "/api/v1/reports/aged-payables/detail",
            params={"currency": "KES", "page": 1, "per_page": 25},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert _money(summary["totals"]["total"]) == Decimal("200.00")
    assert {row["vendor_id"] for row in summary["vendors"]} == {str(existing_vendor.id)}

    assert detail_response.status_code == 200
    detail_ids = {row["source_id"] for row in detail_response.json()["items"]}
    assert detail_ids == {str(existing.id)}
    assert str(future.id) not in detail_ids
