"""
Pure-function tests for common/financial.py helpers.

Covers: get_tax_rate, calculate_line_item, calculate_discount,
check_is_overdue, calculate_days_overdue.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.common.exceptions import BadRequestException
from app.common.financial import (
    TAX_RATES,
    calculate_discount,
    calculate_line_item,
    get_tax_rate,
    validate_document_vat_rate_for_date,
    validate_tax_treatment_for_date,
)
from app.common.reporting_time import calculate_days_overdue, check_is_overdue
from app.constants.enums import DiscountType, TaxType

# get_tax_rate


class TestGetTaxRate:
    """Tax rate lookup from TaxType enum."""

    def test_vat_16(self):
        assert get_tax_rate(TaxType.VAT_16) == Decimal("0.16")

    def test_vat_8(self):
        assert get_tax_rate(TaxType.VAT_8) == Decimal("0.08")

    def test_petroleum_vat_13(self):
        assert get_tax_rate(TaxType.PETROLEUM_VAT_13) == Decimal("0.13")

    def test_petroleum_vat_8(self):
        assert get_tax_rate(TaxType.PETROLEUM_VAT_8) == Decimal("0.08")

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


class TestEffectiveDatedTaxTreatment:
    """Eligibility is date-aware while the arithmetic engine stays historical."""

    def test_non_petroleum_item_cannot_use_generic_vat_8(self):
        with pytest.raises(BadRequestException):
            validate_tax_treatment_for_date(TaxType.VAT_8, date(2023, 6, 30))

    @pytest.mark.parametrize(
        ("tax_point", "is_valid"),
        [
            (date(2018, 8, 31), False),
            (date(2018, 9, 1), True),
            (date(2023, 6, 30), True),
            (date(2023, 7, 1), False),
            (date(2026, 4, 15), False),
            (date(2026, 4, 16), True),
            (date(2026, 10, 14), True),
            (date(2026, 10, 15), False),
        ],
    )
    def test_petroleum_vat_8_boundaries(self, tax_point: date, is_valid: bool):
        if is_valid:
            validate_tax_treatment_for_date(TaxType.PETROLEUM_VAT_8, tax_point)
            return
        with pytest.raises(BadRequestException):
            validate_tax_treatment_for_date(
                TaxType.PETROLEUM_VAT_8,
                tax_point,
            )

    @pytest.mark.parametrize(
        ("tax_point", "is_valid"),
        [
            (date(2026, 4, 14), False),
            (date(2026, 4, 15), True),
            (date(2026, 4, 16), False),
        ],
    )
    def test_petroleum_vat_13_boundaries(self, tax_point: date, is_valid: bool):
        if is_valid:
            validate_tax_treatment_for_date(TaxType.PETROLEUM_VAT_13, tax_point)
            return
        with pytest.raises(BadRequestException):
            validate_tax_treatment_for_date(TaxType.PETROLEUM_VAT_13, tax_point)

    @pytest.mark.parametrize("rate", [Decimal("0.08"), Decimal("0.13")])
    def test_document_level_petroleum_rates_require_scope(self, rate: Decimal):
        with pytest.raises(BadRequestException) as exc_info:
            validate_document_vat_rate_for_date(rate, date(2026, 4, 16))
        assert exc_info.value.extra.get("field") == "vat_rate"

    def test_current_rates_remain_valid(self):
        validate_tax_treatment_for_date(TaxType.VAT_16, date(2026, 1, 1))


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
        result = calculate_discount(Decimal("500.00"), DiscountType.AMOUNT, None, None)
        assert result == Decimal("0.00")


# check_is_overdue

ACCOUNTING_DATE = date(2026, 7, 22)


class TestCheckIsOverdue:
    """Overdue detection based on status and due date."""

    def test_pending_past_due(self):
        assert check_is_overdue(
            "pending", ACCOUNTING_DATE - timedelta(days=1), as_of_date=ACCOUNTING_DATE
        )

    def test_pending_not_yet_due(self):
        assert not check_is_overdue(
            "pending", ACCOUNTING_DATE + timedelta(days=1), as_of_date=ACCOUNTING_DATE
        )

    def test_paid_is_never_overdue(self):
        assert not check_is_overdue(
            "paid", ACCOUNTING_DATE - timedelta(days=1), as_of_date=ACCOUNTING_DATE
        )

    def test_canceled_is_never_overdue(self):
        assert not check_is_overdue(
            "canceled", ACCOUNTING_DATE - timedelta(days=1), as_of_date=ACCOUNTING_DATE
        )

    def test_due_today_is_not_overdue(self):
        """Due date == today should NOT be overdue (overdue means past due)."""
        assert not check_is_overdue(
            "pending", ACCOUNTING_DATE, as_of_date=ACCOUNTING_DATE
        )


# calculate_days_overdue


class TestCalculateDaysOverdue:
    """Days overdue calculation."""

    def test_one_day_overdue(self):
        assert (
            calculate_days_overdue(
                "pending",
                ACCOUNTING_DATE - timedelta(days=1),
                as_of_date=ACCOUNTING_DATE,
            )
            == 1
        )

    def test_thirty_days_overdue(self):
        assert (
            calculate_days_overdue(
                "overdue",
                ACCOUNTING_DATE - timedelta(days=30),
                as_of_date=ACCOUNTING_DATE,
            )
            == 30
        )

    def test_not_overdue_returns_zero(self):
        assert (
            calculate_days_overdue(
                "pending",
                ACCOUNTING_DATE + timedelta(days=1),
                as_of_date=ACCOUNTING_DATE,
            )
            == 0
        )

    def test_paid_returns_zero_even_if_past_due(self):
        assert (
            calculate_days_overdue(
                "paid", ACCOUNTING_DATE - timedelta(days=10), as_of_date=ACCOUNTING_DATE
            )
            == 0
        )
