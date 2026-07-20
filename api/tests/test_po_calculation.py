"""
Purchase Order totals & calculation engine.

PO-27: VAT is a PO-level charge on the subtotal, not a per-line tax. When VAT
is enabled the tax is ``round(subtotal * vat_rate)`` and lines are persisted as
no_tax/0; when disabled tax_total is 0 and total == subtotal.

- subtotal = Σ line_total; tax_total = round(subtotal * vat_rate) when enabled;
  total = subtotal + tax_total.
- All money is Decimal, quantized to 2 dp.
- The rate is a fraction sourced from the shared TAX_RATES table (DRY).
- Preview (calculate_totals) must match what create()/update() persist for the
  same inputs (parity).

Pure-function tests — no database required (calculate_totals is a classmethod).
"""

from datetime import date
from decimal import Decimal

import pytest

from app.common.exceptions import BadRequestException
from app.constants.enums import TaxType
from app.modules.purchase_orders.schemas import PurchaseOrderLineItemCreate
from app.modules.purchase_orders.service import PurchaseOrderService

# 16% VAT as a fraction, sourced conceptually from the shared TAX_RATES table.
VAT_16_RATE = Decimal("0.16")
VAT_8_RATE = Decimal("0.08")
CURRENT_TAX_POINT = date(2026, 1, 15)
HISTORICAL_TAX_POINT = date(2023, 6, 30)


def _calculate(
    line_items,
    *,
    tax_point_date=CURRENT_TAX_POINT,
    vat_enabled=False,
    vat_rate=None,
):
    return PurchaseOrderService.calculate_totals(
        line_items,
        tax_point_date=tax_point_date,
        vat_enabled=vat_enabled,
        vat_rate=vat_rate,
    )


def _po_item(**kw) -> PurchaseOrderLineItemCreate:
    base = {
        "item_name": "Item",
        "description": "desc",
        "quantity": Decimal("1"),
        "unit_price": Decimal("100.00"),
        # PO-27: per-line tax is retired for POs — lines carry no_tax and VAT
        # is charged at the PO level. The default reflects what the editor
        # now sends.
        "tax_type": TaxType.NO_TAX,
    }
    base.update(kw)
    return PurchaseOrderLineItemCreate(**base)


class TestPurchaseOrderCalculateTotals:
    """calculate_totals over the PO-level-VAT input matrix."""

    def test_empty_line_items(self):
        result = _calculate([])
        assert result.subtotal == Decimal("0.00")
        assert result.tax_total == Decimal("0.00")
        assert result.total == Decimal("0.00")
        assert result.line_items == []

    def test_vat_off_has_no_tax(self):
        # VAT disabled: tax_total is 0 and total == subtotal regardless of the
        # (now ignored) per-line tax type.
        result = _calculate(
            [_po_item(quantity=Decimal("2"), unit_price=Decimal("100.00"))]
        )
        assert result.subtotal == Decimal("200.00")
        assert result.tax_total == Decimal("0.00")
        assert result.total == Decimal("200.00")
        assert len(result.line_items) == 1

    def test_single_item_vat_16(self):
        result = _calculate(
            [_po_item(quantity=Decimal("2"), unit_price=Decimal("100.00"))],
            vat_enabled=True,
            vat_rate=VAT_16_RATE,
        )
        # subtotal = 2 x 100 = 200.00; PO-level VAT = 200 x 0.16 = 32.00
        assert result.subtotal == Decimal("200.00")
        assert result.tax_total == Decimal("32.00")
        assert result.total == Decimal("232.00")
        assert len(result.line_items) == 1
        # Lines persist as no_tax/0 — they never contribute to PO tax.
        assert all(li["tax_type"] == TaxType.NO_TAX.value for li in result.line_items)
        assert all(Decimal(str(li["tax_amount"])) == 0 for li in result.line_items)

    def test_multi_item_vat_on_subtotal(self):
        # PO-27: VAT is computed once on the whole subtotal, not per line, so
        # the (previously "mixed") per-line tax types are irrelevant.
        result = _calculate(
            [
                _po_item(quantity=Decimal("2"), unit_price=Decimal("100.00")),
                _po_item(quantity=Decimal("3"), unit_price=Decimal("50.00")),
            ],
            vat_enabled=True,
            vat_rate=VAT_16_RATE,
        )
        # subtotal = 200.00 + 150.00 = 350.00; VAT = 350 x 0.16 = 56.00
        assert result.subtotal == Decimal("350.00")
        assert result.tax_total == Decimal("56.00")
        assert result.total == Decimal("406.00")

    def test_rate_change_recomputes_tax(self):
        items = [_po_item(quantity=Decimal("2"), unit_price=Decimal("100.00"))]
        at_16 = _calculate(items, vat_enabled=True, vat_rate=VAT_16_RATE)
        at_8 = _calculate(
            items,
            tax_point_date=HISTORICAL_TAX_POINT,
            vat_enabled=True,
            vat_rate=VAT_8_RATE,
        )
        assert at_16.tax_total == Decimal("32.00")
        assert at_8.tax_total == Decimal("16.00")

    def test_retired_rate_is_rejected_for_current_tax_point(self):
        with pytest.raises(BadRequestException):
            _calculate(
                [_po_item()],
                vat_enabled=True,
                vat_rate=VAT_8_RATE,
            )

    def test_large_quantities_no_drift(self):
        result = _calculate(
            [_po_item(quantity=Decimal("100000"), unit_price=Decimal("999.99"))],
            vat_enabled=True,
            vat_rate=VAT_16_RATE,
        )
        # 100000 x 999.99 = 99,999,000.00; VAT = x 0.16 = 15,999,840.00
        assert result.subtotal == Decimal("99999000.00")
        assert result.tax_total == Decimal("15999840.00")
        assert result.total == Decimal("115998840.00")

    def test_all_money_is_decimal_2dp(self):
        result = _calculate(
            [_po_item(quantity=Decimal("3"), unit_price=Decimal("33.33"))],
            vat_enabled=True,
            vat_rate=VAT_16_RATE,
        )
        for value in (result.subtotal, result.tax_total, result.total):
            assert isinstance(value, Decimal)
            assert -value.as_tuple().exponent == 2


class TestDelegatesToSharedFinancial:
    """No-duplication guard: the service must call app.common.financial."""

    def test_build_and_sum_delegate(self, monkeypatch):
        import app.modules.purchase_orders.service as svc

        calls = {"build": 0, "sum": 0}
        real_build = svc.build_line_items
        real_sum = svc.sum_line_totals

        def spy_build(items, *, tax_point_date=None):
            calls["build"] += 1
            assert tax_point_date == CURRENT_TAX_POINT
            return real_build(items, tax_point_date=tax_point_date)

        def spy_sum(data):
            calls["sum"] += 1
            return real_sum(data)

        monkeypatch.setattr(svc, "build_line_items", spy_build)
        monkeypatch.setattr(svc, "sum_line_totals", spy_sum)

        _calculate([_po_item()])
        assert calls["build"] == 1
        assert calls["sum"] == 1
