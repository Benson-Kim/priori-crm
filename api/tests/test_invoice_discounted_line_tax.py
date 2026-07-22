"""Discounted line-tax invoices keep taxable bases, rates, and VAT consistent."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.common.financial import (
    allocate_discounted_line_taxes,
    build_line_items,
    calculate_discount,
    get_tax_rate,
    quantize_money,
)
from app.constants.enums import (
    Currency,
    CustomerStatus,
    DiscountType,
    InvoiceStatus,
    TaxType,
)
from app.modules.customers.models import Customer
from app.modules.invoices.models import Invoice, InvoiceLineItem
from app.modules.invoices.schemas import (
    InvoiceCreate,
    InvoiceLineItemCreate,
    InvoiceUpdate,
)
from app.modules.invoices.service import InvoiceService
from app.modules.reports.service import ReportsService
from app.modules.statements.schemas import RangePreset, ResolvedPeriod


def _customer(db) -> Customer:
    customer = Customer(
        customer_type="business",
        company_name="Discount Test",
        first_name="Test",
        last_name="Customer",
        email=f"{uuid4().hex}@example.test",
        phone="0700000000",
        currency=Currency.KES,
        status=CustomerStatus.ACTIVE,
    )
    db.add(customer)
    db.flush()
    return customer


def _items() -> list[InvoiceLineItemCreate]:
    return [
        InvoiceLineItemCreate(
            item_name="Standard-rated",
            description="Standard-rated",
            quantity=Decimal("1"),
            unit_price=Decimal("200.00"),
            tax_type=TaxType.VAT_16,
        ),
        InvoiceLineItemCreate(
            item_name="Zero-rated",
            description="Zero-rated",
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            tax_type=TaxType.VAT_0,
        ),
    ]


def test_discount_allocation_persists_line_level_tax_invariant():
    built = build_line_items(_items())
    discount = calculate_discount(
        Decimal("300.00"),
        DiscountType.PERCENTAGE,
        None,
        Decimal("20.00"),
    )

    allocate_discounted_line_taxes(built, discount)

    assert [item["taxable_value"] for item in built] == [
        Decimal("160.00"),
        Decimal("80.00"),
    ]
    assert sum((item["taxable_value"] for item in built), Decimal("0.00")) == Decimal(
        "240.00"
    )
    for item in built:
        rate = get_tax_rate(TaxType(item["tax_type"]))
        assert item["tax_amount"] == quantize_money(item["taxable_value"] * rate)


def test_invoice_create_recalculates_vat_after_document_discount(db):
    customer = _customer(db)
    invoice = InvoiceService(db).create(
        InvoiceCreate(
            customer_id=customer.id,
            transaction_date=date(2026, 7, 1),
            due_date=date(2026, 7, 31),
            currency=Currency.KES,
            line_items=_items(),
            discount_type=DiscountType.PERCENTAGE,
            discount_percentage=Decimal("20.00"),
            vat_enabled=False,
        )
    )

    rows = sorted(invoice.line_items, key=lambda item: item.line_number)
    assert [row.taxable_value for row in rows] == [
        Decimal("160.00"),
        Decimal("80.00"),
    ]
    assert [row.tax_amount for row in rows] == [
        Decimal("25.60"),
        Decimal("0.00"),
    ]
    assert invoice.tax_total == Decimal("25.60")
    assert invoice.total_due == Decimal("265.60")


def test_legacy_issued_discounted_tax_is_reported_without_false_rate_or_base(db):
    customer = _customer(db)
    invoice = Invoice(
        invoice_number=f"INV-{uuid4().hex[:12]}",
        invoice_reference=f"IN-{uuid4().hex[:8]}",
        customer_id=customer.id,
        transaction_date=date(2026, 7, 1),
        due_date=date(2026, 7, 31),
        status=InvoiceStatus.SENT,
        currency=Currency.KES,
        subtotal=Decimal("200.00"),
        discount_type=DiscountType.PERCENTAGE,
        discount_percentage=Decimal("20.00"),
        vat_enabled=False,
        tax_total=Decimal("32.00"),
        total_due=Decimal("192.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("192.00"),
    )
    db.add(invoice)
    db.flush()
    db.add(
        InvoiceLineItem(
            invoice_id=invoice.id,
            line_number=1,
            item_name="Legacy standard-rated",
            description="Legacy standard-rated",
            quantity=Decimal("1"),
            unit_price=Decimal("200.00"),
            line_total=Decimal("200.00"),
            tax_type=TaxType.VAT_16,
            taxable_value=None,
            tax_amount=Decimal("32.00"),
        )
    )
    db.commit()

    period = ResolvedPeriod.resolve(
        RangePreset.CUSTOM,
        date(2026, 7, 1),
        date(2026, 7, 31),
    )
    report = ReportsService(db).get_tax_report(period, Currency.KES)
    legacy = next(
        row
        for row in report.sales_by_tax_type
        if row.tax_type == "unreconciled_historical"
    )

    assert legacy.tax_rate is None
    assert legacy.taxable_value == Decimal("0.00")
    assert legacy.tax_amount == Decimal("32.00")
    assert legacy.document_count == 1
    assert report.metrics.vat_collected == Decimal("32.00")


def test_discount_update_recalculates_persisted_line_tax(db):
    customer = _customer(db)
    service = InvoiceService(db)
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer.id,
            transaction_date=date(2026, 7, 1),
            due_date=date(2026, 7, 31),
            currency=Currency.KES,
            line_items=_items(),
            discount_type=DiscountType.PERCENTAGE,
            discount_percentage=Decimal("20.00"),
            vat_enabled=False,
        )
    )

    updated = service.update(
        invoice.id,
        InvoiceUpdate(
            discount_type=DiscountType.PERCENTAGE,
            discount_percentage=Decimal("10.00"),
        ),
        expected_version=invoice.version,
    )

    rows = sorted(updated.line_items, key=lambda item: item.line_number)
    assert [row.taxable_value for row in rows] == [
        Decimal("180.00"),
        Decimal("90.00"),
    ]
    assert [row.tax_amount for row in rows] == [
        Decimal("28.80"),
        Decimal("0.00"),
    ]
    assert updated.tax_total == Decimal("28.80")
    assert updated.total_due == Decimal("298.80")


def test_legacy_draft_is_reconciled_before_it_becomes_issued(db):
    customer = _customer(db)
    invoice = Invoice(
        invoice_number=f"INV-{uuid4().hex[:12]}",
        invoice_reference=f"IN-{uuid4().hex[:8]}",
        customer_id=customer.id,
        transaction_date=date(2026, 7, 1),
        due_date=date(2026, 7, 31),
        status=InvoiceStatus.DRAFT,
        currency=Currency.KES,
        subtotal=Decimal("200.00"),
        discount_type=DiscountType.PERCENTAGE,
        discount_percentage=Decimal("20.00"),
        vat_enabled=False,
        tax_total=Decimal("32.00"),
        total_due=Decimal("192.00"),
        amount_paid=Decimal("0.00"),
        balance_due=Decimal("192.00"),
    )
    db.add(invoice)
    db.flush()
    line = InvoiceLineItem(
        invoice_id=invoice.id,
        line_number=1,
        item_name="Legacy draft",
        description="Legacy draft",
        quantity=Decimal("1"),
        unit_price=Decimal("200.00"),
        line_total=Decimal("200.00"),
        tax_type=TaxType.VAT_16,
        taxable_value=None,
        tax_amount=Decimal("32.00"),
    )
    db.add(line)
    db.flush()

    issued = InvoiceService(db).mark_as_sent(invoice.id)

    db.refresh(line)
    assert issued.status == InvoiceStatus.SENT
    assert line.taxable_value == Decimal("160.00")
    assert line.tax_amount == Decimal("25.60")
    assert issued.tax_total == Decimal("25.60")
    assert issued.total_due == Decimal("185.60")
