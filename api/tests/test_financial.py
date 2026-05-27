"""
Pure-function tests for common/financial.py helpers.

Covers: get_tax_rate, calculate_line_item, calculate_discount,
check_is_overdue, calculate_days_overdue.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.financial import (
    TAX_RATES,
    calculate_discount,
    calculate_days_overdue,
    calculate_line_item,
    check_is_overdue,
    get_tax_rate,
)
from app.constants.enums import DiscountType, TaxType


# get_tax_rate


class TestGetTaxRate:
    """Tax rate lookup from TaxType enum."""

    def test_vat_16(self):
        assert get_tax_rate(TaxType.VAT_16) == Decimal("0.16")

    def test_vat_8(self):
        assert get_tax_rate(TaxType.VAT_8) == Decimal("0.08")

    def test_vat_0(self):
        assert get_tax_rate(TaxType.VAT_0) == Decimal("0.00")

    def test_exempt(self):
        assert get_tax_rate(TaxType.EXEMPT) == Decimal("0.00")

    def test_no_tax(self):
        assert get_tax_rate(TaxType.NO_TAX) == Decimal("0.00")

    def test_all_enum_members_covered(self):
        """Every TaxType member must have a rate in TAX_RATES."""
        for member in TaxType:
            assert member in TAX_RATES, f"Missing rate for {member}"


# calculate_line_item


class TestCalculateLineItem:
    """Line item total and tax calculation."""

    def test_basic_vat_16(self):
        line_total, tax = calculate_line_item(
            Decimal("2"), Decimal("100.00"), TaxType.VAT_16
        )
        assert line_total == Decimal("200.00")
        assert tax == Decimal("32.00")

    def test_zero_quantity(self):
        """Zero quantity should produce zero totals (guard, not enforced here)."""
        line_total, tax = calculate_line_item(
            Decimal("0"), Decimal("500.00"), TaxType.VAT_16
        )
        assert line_total == Decimal("0")
        assert tax == Decimal("0")

    def test_no_tax_type(self):
        line_total, tax = calculate_line_item(
            Decimal("3"), Decimal("50.00"), TaxType.NO_TAX
        )
        assert line_total == Decimal("150.00")
        assert tax == Decimal("0.00")

    def test_fractional_quantities(self):
        """Decimal quantities (e.g. hours worked) must be handled."""
        line_total, tax = calculate_line_item(
            Decimal("1.5"), Decimal("200.00"), TaxType.VAT_8
        )
        assert line_total == Decimal("300.00")
        assert tax == Decimal("24.00")


# calculate_discount


class TestCalculateDiscount:
    """Discount calculation from subtotal."""

    def test_fixed_amount(self):
        result = calculate_discount(
            Decimal("1000.00"), DiscountType.AMOUNT, Decimal("150.00"), None
        )
        assert result == Decimal("150.00")

    def test_fixed_amount_capped_at_subtotal(self):
        """Fixed discount cannot exceed subtotal (no negative totals)."""
        result = calculate_discount(
            Decimal("100.00"), DiscountType.AMOUNT, Decimal("500.00"), None
        )
        assert result == Decimal("100.00")

    def test_percentage(self):
        result = calculate_discount(
            Decimal("1000.00"), DiscountType.PERCENTAGE, None, Decimal("10")
        )
        assert result == Decimal("100.00")

    def test_percentage_precision(self):
        """Percentage discounts quantize to 2 decimal places."""
        result = calculate_discount(
            Decimal("333.33"), DiscountType.PERCENTAGE, None, Decimal("15")
        )
        assert result == Decimal("50.00")

    def test_no_discount_type(self):
        result = calculate_discount(Decimal("500.00"), None, None, None)
        assert result == Decimal("0.00")

    def test_amount_type_but_no_value(self):
        """Discount type set but value is None/0 → no discount."""
        result = calculate_discount(
            Decimal("500.00"), DiscountType.AMOUNT, None, None
        )
        assert result == Decimal("0.00")


# check_is_overdue


class TestCheckIsOverdue:
    """Overdue detection based on status and due date."""

    def test_pending_past_due(self):
        yesterday = date.today() - timedelta(days=1)
        assert check_is_overdue("pending", yesterday) is True

    def test_pending_not_yet_due(self):
        tomorrow = date.today() + timedelta(days=1)
        assert check_is_overdue("pending", tomorrow) is False

    def test_paid_is_never_overdue(self):
        yesterday = date.today() - timedelta(days=1)
        assert check_is_overdue("paid", yesterday) is False

    def test_canceled_is_never_overdue(self):
        yesterday = date.today() - timedelta(days=1)
        assert check_is_overdue("canceled", yesterday) is False

    def test_due_today_is_not_overdue(self):
        """Due date == today should NOT be overdue (overdue means past due)."""
        assert check_is_overdue("pending", date.today()) is False


# calculate_days_overdue


class TestCalculateDaysOverdue:
    """Days overdue calculation."""

    def test_one_day_overdue(self):
        yesterday = date.today() - timedelta(days=1)
        assert calculate_days_overdue("pending", yesterday) == 1

    def test_thirty_days_overdue(self):
        past = date.today() - timedelta(days=30)
        assert calculate_days_overdue("overdue", past) == 30

    def test_not_overdue_returns_zero(self):
        tomorrow = date.today() + timedelta(days=1)
        assert calculate_days_overdue("pending", tomorrow) == 0

    def test_paid_returns_zero_even_if_past_due(self):
        past = date.today() - timedelta(days=10)
        assert calculate_days_overdue("paid", past) == 0
