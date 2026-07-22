"""Integration regressions for date-aware duplication and quote conversion."""

from datetime import date
from decimal import Decimal
from itertools import count

import pytest

from app.common.exceptions import BadRequestException
from app.common.financial import get_tax_rate, quantize_money
from app.constants.enums import (
    Currency,
    CustomerStatus,
    DiscountType,
    ExpenseStatus,
    QuoteStatus,
    TaxType,
    VendorStatus,
)
from app.modules.customers.models import Customer
from app.modules.expenses.models import Expense
from app.modules.expenses.schemas import ExpenseCreate
from app.modules.expenses.service import ExpenseService
from app.modules.invoices.models import Invoice
from app.modules.invoices.schemas import InvoiceCreate
from app.modules.invoices.service import InvoiceService
from app.modules.quotes.models import Quote, QuoteLineItem
from app.modules.quotes.service import QuoteService
from app.modules.vendors.models import Vendor

CURRENT_DATE = date(2026, 1, 15)


@pytest.fixture(autouse=True)
def _stable_dates_and_references(monkeypatch):
    invoice_numbers = count(1)
    expense_numbers = count(1)
    monkeypatch.setattr(
        "app.modules.invoices.service.reporting_date",
        lambda: CURRENT_DATE,
    )
    monkeypatch.setattr(
        "app.modules.expenses.service.reporting_date",
        lambda: CURRENT_DATE,
    )
    monkeypatch.setattr(
        "app.modules.quotes.service.reporting_date",
        lambda: CURRENT_DATE,
    )
    monkeypatch.setattr(
        InvoiceService,
        "_generate_invoice_number",
        lambda self: f"INV-WF-{next(invoice_numbers):04d}",
    )
    monkeypatch.setattr(
        InvoiceService,
        "_generate_invoice_reference",
        lambda self: f"IN-WF-{next(invoice_numbers):04d}",
    )
    monkeypatch.setattr(
        ExpenseService,
        "_generate_expense_number",
        lambda self: f"EXP-WF-{next(expense_numbers):04d}",
    )
    monkeypatch.setattr(
        ExpenseService,
        "_generate_expense_reference",
        lambda self: f"EXPR-WF-{next(expense_numbers):04d}",
    )


def _customer(
    db,
    suffix: str,
    *,
    currency: Currency = Currency.KES,
    status: CustomerStatus = CustomerStatus.ACTIVE,
) -> Customer:
    customer = Customer(
        customer_type="business",
        company_name=f"Workflow Customer {suffix}",
        first_name="Ada",
        last_name=suffix,
        email=f"workflow-{suffix.lower()}@example.test",
        phone=f"+2547000{len(suffix):05d}",
        currency=currency,
        status=status,
    )
    db.add(customer)
    db.flush()
    return customer


def _vendor(
    db,
    suffix: str,
    *,
    currency: Currency = Currency.KES,
    status: VendorStatus = VendorStatus.ACTIVE,
) -> Vendor:
    vendor = Vendor(
        vendor_name=f"Workflow Vendor {suffix}",
        currency=currency,
        status=status,
        version=1,
    )
    db.add(vendor)
    db.flush()
    return vendor


def _line(tax_type: TaxType, price: str = "100.00") -> dict:
    return {
        "item_name": "Service",
        "description": "Consulting service",
        "quantity": Decimal("1.00"),
        "unit_price": Decimal(price),
        "tax_type": tax_type,
    }


def _invoice(
    db,
    customer: Customer,
    *,
    transaction_date: date,
    tax_type: TaxType = TaxType.VAT_16,
    document_vat: bool = False,
) -> Invoice:
    return InvoiceService(db).create(
        InvoiceCreate(
            customer_id=customer.id,
            transaction_date=transaction_date,
            due_date=date(2026, 2, 15),
            currency=customer.currency,
            line_items=[_line(TaxType.NO_TAX if document_vat else tax_type)],
            discount_type=DiscountType.PERCENTAGE,
            discount_percentage=Decimal("10.00"),
            vat_enabled=document_vat,
            vat_rate=Decimal("0.16") if document_vat else None,
            rfq_number="SOURCE-RFQ",
            notes="Copied note",
        )
    )


def _expense(
    db,
    vendor: Vendor,
    *,
    expense_date: date,
    tax_type: TaxType = TaxType.VAT_16,
) -> Expense:
    return ExpenseService(db).create(
        ExpenseCreate(
            vendor_id=vendor.id,
            expense_date=expense_date,
            due_date=date(2026, 2, 15),
            currency=vendor.currency,
            is_recurring=False,
            line_items=[_line(tax_type)],
            notes="Copied expense note",
        )
    )


def _quote(
    db,
    customer: Customer,
    suffix: str,
    *,
    tax_type: TaxType = TaxType.VAT_16,
    document_vat: bool = False,
) -> Quote:
    line_total = Decimal("100.00")
    historical_8_percent = tax_type in {
        TaxType.VAT_8,
        TaxType.PETROLEUM_VAT_8,
    }

    line_tax = (
        Decimal("0.00")
        if document_vat
        else quantize_money(line_total * get_tax_rate(tax_type))
    )
    quote = Quote(
        quote_number=f"QTE-WF-{suffix}",
        quote_reference=f"QT-WF-{suffix}",
        customer_id=customer.id,
        transaction_date=(date(2023, 6, 30) if historical_8_percent else CURRENT_DATE),
        due_date=date(2026, 2, 15),
        currency=customer.currency,
        status=QuoteStatus.APPROVED,
        subtotal=line_total,
        discount_type=DiscountType.PERCENTAGE,
        discount_amount=None,
        discount_percentage=Decimal("10.00"),
        vat_enabled=document_vat,
        vat_rate=Decimal("0.16") if document_vat else None,
        tax_total=Decimal("999.00"),
        total_due=Decimal("999.00"),
        rfq_rfp_number="QUOTE-RFQ",
        notes="Quote note",
        version=1,
    )
    db.add(quote)
    db.flush()

    db.add(
        QuoteLineItem(
            quote_id=quote.id,
            line_number=1,
            item_name="Service",
            description="Consulting service",
            quantity=Decimal("1.00"),
            unit_price=line_total,
            line_total=line_total,
            tax_type=TaxType.NO_TAX if document_vat else tax_type,
            tax_amount=line_tax,
        )
    )
    db.flush()
    return quote


def test_invoice_duplicate_recalculates_and_preserves_explicit_document_vat(db):
    customer = _customer(db, "InvoiceDocVat")
    source = _invoice(
        db,
        customer,
        transaction_date=CURRENT_DATE,
        document_vat=True,
    )
    source.tax_total = Decimal("999.00")
    source.total_due = Decimal("999.00")
    source.balance_due = Decimal("999.00")
    db.flush()

    result = InvoiceService(db).duplicate_invoice(source.id)
    duplicate = db.get(Invoice, result.new_invoice_id)

    assert duplicate.transaction_date == CURRENT_DATE
    assert duplicate.currency == Currency.KES
    assert duplicate.vat_enabled is True
    assert duplicate.vat_rate == Decimal("0.1600")
    assert duplicate.subtotal == Decimal("100.00")
    assert duplicate.tax_total == Decimal("14.40")
    assert duplicate.total_due == Decimal("104.40")
    assert duplicate.total_due != source.total_due
    assert duplicate.rfq_number == "SOURCE-RFQ"


def test_invoice_duplicate_rejects_historical_8_percent_on_current_tax_point(db):
    customer = _customer(db, "InvoiceHistorical")
    source = _invoice(
        db,
        customer,
        transaction_date=date(2023, 6, 30),
        tax_type=TaxType.PETROLEUM_VAT_8,
    )

    with pytest.raises(BadRequestException):
        InvoiceService(db).duplicate_invoice(source.id)


def test_invoice_duplicate_preserves_foreign_currency_and_rechecks_customer(db):
    customer = _customer(db, "InvoiceUsd", currency=Currency.USD)
    source = _invoice(db, customer, transaction_date=CURRENT_DATE)
    result = InvoiceService(db).duplicate_invoice(source.id)
    duplicate = db.get(Invoice, result.new_invoice_id)
    assert duplicate.currency == Currency.USD

    customer.status = CustomerStatus.INACTIVE
    db.flush()
    with pytest.raises(BadRequestException):
        InvoiceService(db).duplicate_invoice(source.id)


def test_expense_duplicate_recalculates_and_revalidates_vendor(db):
    vendor = _vendor(db, "ExpenseCurrent")
    source = _expense(db, vendor, expense_date=CURRENT_DATE)
    source.tax_total = Decimal("999.00")
    source.total_due = Decimal("999.00")
    source.balance_due = Decimal("999.00")
    db.flush()

    duplicate = ExpenseService(db).duplicate(source.id)
    assert duplicate.status == ExpenseStatus.PENDING
    assert duplicate.expense_date == CURRENT_DATE
    assert duplicate.subtotal == Decimal("100.00")
    assert duplicate.tax_total == Decimal("16.00")
    assert duplicate.total_due == Decimal("116.00")

    vendor.status = VendorStatus.INACTIVE
    db.flush()
    with pytest.raises(BadRequestException):
        ExpenseService(db).duplicate(source.id)


def test_expense_duplicate_rejects_historical_8_percent_on_current_tax_point(db):
    vendor = _vendor(db, "ExpenseHistorical")
    source = _expense(
        db,
        vendor,
        expense_date=date(2023, 6, 30),
        tax_type=TaxType.PETROLEUM_VAT_8,
    )
    with pytest.raises(BadRequestException):
        ExpenseService(db).duplicate(source.id)


def test_quote_conversion_recalculates_document_vat_and_preserves_due_date(db):
    customer = _customer(db, "QuoteDocVat")
    quote = _quote(db, customer, "DOC", document_vat=True)

    result = QuoteService(db).convert_to_invoice(quote.id)
    invoice = db.get(Invoice, result["invoice_id"])

    assert invoice.transaction_date == CURRENT_DATE
    assert invoice.due_date == quote.due_date
    assert invoice.vat_enabled is True
    assert invoice.vat_rate == Decimal("0.1600")
    assert invoice.subtotal == Decimal("100.00")
    assert invoice.tax_total == Decimal("14.40")
    assert invoice.total_due == Decimal("104.40")
    assert invoice.rfq_number == "QUOTE-RFQ"
    assert quote.status == QuoteStatus.INVOICED
    assert quote.related_invoice_id == invoice.id


def test_quote_conversion_recalculates_line_vat_and_preserves_currency(db):
    customer = _customer(db, "QuoteUsd", currency=Currency.USD)
    quote = _quote(db, customer, "USD", document_vat=False)

    result = QuoteService(db).convert_to_invoice(quote.id)
    invoice = db.get(Invoice, result["invoice_id"])

    assert invoice.currency == Currency.USD
    assert invoice.vat_enabled is False
    assert invoice.tax_total == Decimal("14.40")
    assert invoice.total_due == Decimal("104.40")
    assert invoice.line_items[0].tax_type == TaxType.VAT_16
    assert invoice.line_items[0].taxable_value == Decimal("90.00")
    assert invoice.line_items[0].tax_amount == Decimal("14.40")


def test_quote_conversion_rejects_a_due_date_before_the_invoice_date(db):
    customer = _customer(db, "QuotePastDue")
    quote = _quote(db, customer, "PAST-DUE")
    quote.transaction_date = date(2026, 1, 1)
    quote.due_date = date(2026, 1, 14)
    db.flush()

    with pytest.raises(BadRequestException, match="before the new invoice date"):
        QuoteService(db).convert_to_invoice(quote.id)

    assert quote.status == QuoteStatus.APPROVED
    assert quote.related_invoice_id is None


def test_quote_conversion_rejects_expired_petroleum_vat_8(db):
    customer = _customer(db, "QuotePetroleumHistorical")
    historical = _quote(
        db,
        customer,
        "PETROLEUM-HIST",
        tax_type=TaxType.PETROLEUM_VAT_8,
    )

    with pytest.raises(BadRequestException):
        QuoteService(db).convert_to_invoice(historical.id)


def test_quote_conversion_rejects_legacy_unscoped_vat_8(db):
    customer = _customer(db, "QuoteLegacyVat")
    legacy = _quote(
        db,
        customer,
        "LEGACY-HIST",
        tax_type=TaxType.VAT_8,
    )

    with pytest.raises(BadRequestException):
        QuoteService(db).convert_to_invoice(legacy.id)


def test_quote_conversion_rechecks_customer_status(db):
    customer = _customer(db, "QuoteInactive")
    quote = _quote(db, customer, "INACTIVE")
    customer.status = CustomerStatus.INACTIVE
    db.flush()

    with pytest.raises(BadRequestException):
        QuoteService(db).convert_to_invoice(quote.id)
