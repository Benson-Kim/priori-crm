"""Regression coverage for the review findings on PR #33."""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.modules.reports.service as reports_service_module
from app.common.exceptions import BadRequestException
from app.constants.enums import (
    Currency,
    ExpenseStatus,
    InvoiceStatus,
    PurchaseOrderStatus,
)
from app.modules.customers.models import Customer
from app.modules.expenses.models import Expense, ExpenseLineItem
from app.modules.invoices.models import Invoice, InvoiceLineItem
from app.modules.purchase_orders.models import PurchaseOrder
from app.modules.reports.queries import ReportsRepository
from app.modules.reports.router import get_tax_report
from app.modules.reports.service import ReportsService
from app.modules.statements.schemas import RangePreset, ResolvedPeriod
from app.modules.vendors.models import Vendor

DAY = date(2026, 6, 15)
PERIOD = ResolvedPeriod.resolve(RangePreset.CUSTOM, DAY, DAY)


def _customer(db, *, email: str, name: str = "Acme Limited") -> Customer:
    customer = Customer(
        customer_type="business",
        company_name=name,
        first_name="Jane",
        last_name="Doe",
        email=email,
        phone="0700000000",
        currency=Currency.KES,
    )
    db.add(customer)
    db.flush()
    return customer


def _vendor(db, *, name: str = "Vendor Limited") -> Vendor:
    vendor = Vendor(vendor_name=name, status="active", currency=Currency.KES)
    db.add(vendor)
    db.flush()
    return vendor


def _invoice(
    db,
    customer: Customer,
    *,
    subtotal: Decimal,
    tax_total: Decimal = Decimal("0.00"),
    total_due: Decimal | None = None,
    vat_enabled: bool = False,
    vat_rate: Decimal | None = None,
    status: InvoiceStatus = InvoiceStatus.SENT,
    discount_amount: Decimal | None = None,
) -> Invoice:
    total_due = total_due if total_due is not None else subtotal + tax_total
    invoice = Invoice(
        invoice_number=f"INV-{uuid4().hex[:12]}",
        invoice_reference=f"IN-{uuid4().hex[:8]}",
        customer_id=customer.id,
        transaction_date=DAY,
        due_date=DAY + timedelta(days=30),
        status=status,
        currency=Currency.KES,
        subtotal=subtotal,
        discount_type="amount" if discount_amount is not None else None,
        discount_amount=discount_amount,
        vat_enabled=vat_enabled,
        vat_rate=vat_rate,
        tax_total=tax_total,
        total_due=total_due,
        amount_paid=total_due if status == InvoiceStatus.PAID else Decimal("0.00"),
        balance_due=Decimal("0.00") if status == InvoiceStatus.PAID else total_due,
    )
    db.add(invoice)
    db.flush()
    return invoice


def _invoice_line(
    db,
    invoice: Invoice,
    *,
    number: int,
    name: str,
    amount: Decimal,
    tax_type: str = "no_tax",
    tax_amount: Decimal = Decimal("0.00"),
) -> None:
    db.add(
        InvoiceLineItem(
            invoice_id=invoice.id,
            line_number=number,
            item_name=name,
            description=name,
            quantity=Decimal("1.00"),
            unit_price=amount,
            line_total=amount,
            tax_type=tax_type,
            tax_amount=tax_amount,
        )
    )


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("service_method", "repository_method"),
    [
        ("export_sales_ledger", "sales_ledger"),
        ("export_purchases_ledger", "purchases_ledger"),
    ],
)
def test_oversized_report_export_fails_instead_of_truncating(
    monkeypatch, service_method: str, repository_method: str
) -> None:
    service = ReportsService(object())
    captured: dict[str, int] = {}

    def fake_query(*args, **kwargs):
        captured["limit"] = kwargs["limit"]
        return [object(), object(), object()]

    monkeypatch.setattr(reports_service_module, "_REPORT_EXPORT_MAX_ROWS", 2)
    monkeypatch.setattr(service.repo, repository_method, fake_query)

    with pytest.raises(BadRequestException, match="more than 2 rows"):
        getattr(service, service_method)(PERIOD, Currency.KES)

    assert captured["limit"] == 3


def test_document_level_vat_breakdown_includes_positive_and_zero_rates(db) -> None:
    customer = _customer(db, email="vat@example.test")
    standard = _invoice(
        db,
        customer,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("16.00"),
        vat_enabled=True,
        vat_rate=Decimal("0.1600"),
    )
    zero_rated = _invoice(
        db,
        customer,
        subtotal=Decimal("200.00"),
        vat_enabled=True,
        vat_rate=Decimal("0.0000"),
    )
    _invoice_line(db, standard, number=1, name="Standard", amount=Decimal("100.00"))
    _invoice_line(db, zero_rated, number=1, name="Export", amount=Decimal("200.00"))
    db.commit()

    rows = ReportsRepository(db).tax_by_type_sales(DAY, DAY, Currency.KES)
    by_type = {row.tax_type: row for row in rows}

    assert Decimal(str(by_type["vat_16"].tax_amount)) == Decimal("16.00")
    assert int(by_type["vat_16"].document_count) == 1
    assert Decimal(str(by_type["vat_0"].tax_amount)) == Decimal("0.00")
    assert int(by_type["vat_0"].document_count) == 1


def test_zero_rated_purchase_order_remains_in_purchase_tax_breakdown(db) -> None:
    vendor = _vendor(db)
    po = PurchaseOrder(
        po_number=f"PO-{uuid4().hex[:12]}",
        po_reference=f"PO-{uuid4().hex[:8]}",
        vendor_id=vendor.id,
        order_date=DAY,
        status=PurchaseOrderStatus.SENT,
        currency=Currency.KES,
        subtotal=Decimal("300.00"),
        vat_enabled=True,
        vat_rate=Decimal("0.0000"),
        tax_total=Decimal("0.00"),
        total=Decimal("300.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("300.00"),
    )
    db.add(po)
    db.commit()

    rows = ReportsRepository(db).tax_by_type_purchases(DAY, DAY, Currency.KES)
    by_type = {row.tax_type: Decimal(str(row.tax_amount)) for row in rows}

    assert by_type["vat_0"] == Decimal("0.00")


def test_paid_vendor_credit_does_not_reduce_outstanding_payables(db) -> None:
    vendor = _vendor(db)
    expense = Expense(
        expense_number=f"EXP-{uuid4().hex[:12]}",
        expense_reference=f"EX-{uuid4().hex[:8]}",
        vendor_id=vendor.id,
        expense_date=DAY,
        due_date=DAY + timedelta(days=14),
        status=ExpenseStatus.PAID,
        currency=Currency.KES,
        subtotal=Decimal("100.00"),
        tax_total=Decimal("0.00"),
        total_due=Decimal("100.00"),
        amount_paid=Decimal("120.00"),
        balance_due=Decimal("-20.00"),
    )
    db.add(expense)
    db.flush()
    db.add(
        ExpenseLineItem(
            expense_id=expense.id,
            line_number=1,
            item_name="Service",
            description="Service",
            quantity=Decimal("1.00"),
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
            tax_type="no_tax",
            tax_amount=Decimal("0.00"),
        )
    )
    db.commit()

    summary = ReportsRepository(db).purchases_summary(DAY, DAY, Currency.KES)

    assert summary.outstanding_balance == Decimal("0")


def test_duplicate_customer_names_remain_separate_report_rows(db) -> None:
    first = _customer(db, email="first@example.test", name="Same Name Limited")
    second = _customer(db, email="second@example.test", name="Same Name Limited")
    _invoice(db, first, subtotal=Decimal("100.00"))
    _invoice(db, second, subtotal=Decimal("200.00"))
    db.commit()

    rows = ReportsRepository(db).revenue_by_customer(DAY, DAY, Currency.KES)

    assert len(rows) == 2
    assert {row.customer_id for row in rows} == {first.id, second.id}


def test_category_breakdown_reconciles_after_document_discount(db) -> None:
    customer = _customer(db, email="discount@example.test")
    invoice = _invoice(
        db,
        customer,
        subtotal=Decimal("100.00"),
        total_due=Decimal("90.00"),
        status=InvoiceStatus.PAID,
        discount_amount=Decimal("10.00"),
    )
    _invoice_line(db, invoice, number=1, name="Consulting", amount=Decimal("60.00"))
    _invoice_line(db, invoice, number=2, name="Licensing", amount=Decimal("40.00"))
    db.commit()

    rows = ReportsRepository(db).revenue_by_category(DAY, DAY, Currency.KES)
    amounts = {
        row.category: Decimal(str(row.amount)).quantize(Decimal("0.01")) for row in rows
    }

    assert amounts == {
        "Consulting": Decimal("54.00"),
        "Licensing": Decimal("36.00"),
    }
    assert sum(amounts.values(), Decimal("0.00")) == Decimal("90.00")


@pytest.mark.no_db
def test_tax_router_ignores_non_kes_currency() -> None:
    captured = SimpleNamespace(currency=None)

    class StubService:
        def get_tax_report(self, period, currency):
            captured.currency = currency
            return "report"

    result = get_tax_report(
        StubService(),
        RangePreset.CUSTOM,
        DAY,
        DAY,
        Currency.USD,
    )

    assert result == "report"
    assert captured.currency == Currency.KES
