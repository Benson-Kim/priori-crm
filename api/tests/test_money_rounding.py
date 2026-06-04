"""Tests for the single money-rounding policy (W2.1, P-3).

The historical bug: calculate_line_item returned unrounded products, so
summing several lines accumulated sub-cent drift that could violate a 2-dp
money column or the balance_due >= 0 CHECK, and made totals non-reproducible.
Existing tests only used cleanly-dividing values, so this exercises the odd
cases explicitly.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.financial import (
    MONEY_QUANTUM,
    build_line_items,
    calculate_discount,
    calculate_line_item,
    quantize_money,
    sum_line_totals,
)
from app.constants.enums import (
    Currency,
    DiscountType,
    InvoiceStatus,
    TaxType,
)
from app.modules.customers.models import Customer
from app.modules.invoices.models import Invoice, InvoiceLineItem

from tests.conftest import USING_POSTGRES

TWO_DP = MONEY_QUANTUM


def _is_two_dp(value: Decimal) -> bool:
    return value == value.quantize(TWO_DP)


class TestQuantizeMoney:
    def test_rounds_half_up(self):
        assert quantize_money(Decimal("1.005")) == Decimal("1.01")
        assert quantize_money(Decimal("2.345")) == Decimal("2.35")
        assert quantize_money(Decimal("2.344")) == Decimal("2.34")

    def test_idempotent(self):
        once = quantize_money(Decimal("33.336"))
        assert quantize_money(once) == once


class TestLineItemQuantization:
    def test_line_total_and_tax_quantized(self):
        # 3 x 33.33 = 99.99 exactly; tax 16% = 15.9984 -> 16.00
        line_total, tax_amount = calculate_line_item(
            Decimal("3"), Decimal("33.33"), TaxType.VAT_16
        )
        assert line_total == Decimal("99.99")
        assert tax_amount == Decimal("16.00")
        assert _is_two_dp(line_total)
        assert _is_two_dp(tax_amount)

    def test_tax_on_odd_cents_quantized(self):
        # 1 x 10.01 @ 8% = 0.8008 -> 0.80
        line_total, tax_amount = calculate_line_item(
            Decimal("1"), Decimal("10.01"), TaxType.VAT_8
        )
        assert line_total == Decimal("10.01")
        assert tax_amount == Decimal("0.80")

    def test_summed_totals_have_no_subcent_drift(self):
        raw = [
            {
                "item_name": f"Item {i}",
                "description": "x",
                "quantity": Decimal("1"),
                "unit_price": Decimal("33.33"),
                "tax_type": TaxType.VAT_16,
            }
            for i in range(3)
        ]
        built = build_line_items(raw)
        subtotal, tax_total = sum_line_totals(built)
        assert subtotal == Decimal("99.99")
        assert _is_two_dp(subtotal)
        assert _is_two_dp(tax_total)

    def test_totals_reproducible_across_recalculation(self):
        raw = [
            {
                "item_name": "A",
                "description": "x",
                "quantity": Decimal("7"),
                "unit_price": Decimal("14.29"),
                "tax_type": TaxType.VAT_16,
            },
            {
                "item_name": "B",
                "description": "y",
                "quantity": Decimal("3"),
                "unit_price": Decimal("0.07"),
                "tax_type": TaxType.VAT_8,
            },
        ]
        first = sum_line_totals(build_line_items(raw))
        second = sum_line_totals(build_line_items(raw))
        assert first == second


class TestDiscountQuantization:
    def test_percentage_discount_quantized(self):
        # 12.5% of 99.99 = 12.49875 -> 12.50
        value = calculate_discount(
            Decimal("99.99"), DiscountType.PERCENTAGE, None, Decimal("12.5")
        )
        assert value == Decimal("12.50")
        assert _is_two_dp(value)

    def test_amount_discount_quantized_and_capped(self):
        capped = calculate_discount(
            Decimal("50.00"), DiscountType.AMOUNT, Decimal("75.00"), None
        )
        assert capped == Decimal("50.00")


class TestPersistenceNoCheckViolation:
    """An invoice built from odd values must persist with balance_due >= 0."""

    def test_odd_value_invoice_persists_within_two_dp(self, db):
        customer = Customer(
            customer_type="business",
            company_name="Acme",
            first_name="Ada",
            last_name="L",
            email="ada.money@acme.test",
            phone="+254700000000",
            currency=Currency.KES,
        )
        db.add(customer)
        db.flush()

        raw = [
            {
                "item_name": f"Item {i}",
                "description": "x",
                "quantity": Decimal("1"),
                "unit_price": Decimal("33.33"),
                "tax_type": TaxType.VAT_16,
            }
            for i in range(3)
        ]
        built = build_line_items(raw)
        subtotal, tax_total = sum_line_totals(built)
        discount = calculate_discount(
            subtotal, DiscountType.PERCENTAGE, None, Decimal("12.5")
        )
        total_due = subtotal - discount + tax_total

        assert _is_two_dp(subtotal)
        assert _is_two_dp(tax_total)
        assert _is_two_dp(total_due)
        assert total_due >= 0

        today = date.today()
        invoice = Invoice(
            invoice_number="INV-MONEY-1",
            invoice_reference="IN-M001",
            customer_id=customer.id,
            transaction_date=today,
            due_date=today + timedelta(days=30),
            currency=Currency.KES,
            status=InvoiceStatus.DRAFT,
            subtotal=subtotal,
            discount_type=DiscountType.PERCENTAGE,
            discount_percentage=Decimal("12.5"),
            tax_total=tax_total,
            total_due=total_due,
            amount_paid=Decimal("0.00"),
            balance_due=total_due,
        )
        db.add(invoice)
        db.flush()
        for item in built:
            db.add(InvoiceLineItem(invoice_id=invoice.id, **item))
        db.flush()

        stored = db.query(Invoice).filter(Invoice.id == invoice.id).first()
        assert stored is not None
        assert stored.balance_due >= 0
        assert _is_two_dp(stored.balance_due)
