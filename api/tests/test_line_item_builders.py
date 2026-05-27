"""
Tests for the shared line-item builder helpers in common/financial.py.

RED→GREEN cycle: build_line_items / sum_line_totals
"""
from decimal import Decimal

import pytest

from app.common.financial import build_line_items, sum_line_totals
from app.constants.enums import TaxType


# Helpers

def _item(
    item_name: str = "Widget",
    description: str = "A widget",
    quantity: Decimal = Decimal("1"),
    unit_price: Decimal = Decimal("100.00"),
    tax_type: TaxType = TaxType.VAT_16,
) -> dict:
    """Build a minimal line-item input dict matching the Protocol contract."""
    return {
        "item_name": item_name,
        "description": description,
        "quantity": quantity,
        "unit_price": unit_price,
        "tax_type": tax_type,
    }


# build_line_items

class TestBuildLineItems:
    """Behaviors for the generic build_line_items helper."""

    def test_single_item_returns_correct_structure(self):
        """A single item produces a dict with calculated line_total and tax_amount."""
        items = [_item(quantity=Decimal("2"), unit_price=Decimal("500.00"))]
        result = build_line_items(items)

        assert len(result) == 1
        row = result[0]
        assert row["line_number"] == 1
        assert row["item_name"] == "Widget"
        assert row["description"] == "A widget"
        assert row["quantity"] == Decimal("2")
        assert row["unit_price"] == Decimal("500.00")
        assert row["line_total"] == Decimal("1000.00")
        assert row["tax_type"] == TaxType.VAT_16
        assert row["tax_amount"] == Decimal("160.00")  # 1000 × 0.16

    def test_multiple_items_sequential_line_numbers(self):
        """Multiple items receive sequential 1-based line_number values."""
        items = [
            _item(item_name="A"),
            _item(item_name="B"),
            _item(item_name="C"),
        ]
        result = build_line_items(items)

        assert [r["line_number"] for r in result] == [1, 2, 3]
        assert [r["item_name"] for r in result] == ["A", "B", "C"]

    def test_no_tax_item(self):
        """An item with NO_TAX has zero tax_amount."""
        items = [_item(tax_type=TaxType.NO_TAX)]
        result = build_line_items(items)
        assert result[0]["tax_amount"] == Decimal("0")


# sum_line_totals

class TestSumLineTotals:
    """Behaviors for the generic sum_line_totals helper."""

    def test_sums_correctly_across_items(self):
        """Correctly sums line_total and tax_amount across items."""
        items = build_line_items([
            _item(quantity=Decimal("1"), unit_price=Decimal("200.00"), tax_type=TaxType.VAT_16),
            _item(quantity=Decimal("3"), unit_price=Decimal("100.00"), tax_type=TaxType.VAT_8),
        ])
        subtotal, tax_total = sum_line_totals(items)

        assert subtotal == Decimal("200.00") + Decimal("300.00")  # 500
        assert tax_total == Decimal("32.00") + Decimal("24.00")    # 56

    def test_empty_list_returns_zeros(self):
        """Empty list returns (0.00, 0.00)."""
        subtotal, tax_total = sum_line_totals([])
        assert subtotal == Decimal("0.00")
        assert tax_total == Decimal("0.00")
