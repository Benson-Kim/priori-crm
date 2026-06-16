"""
Purchase Order totals & calculation engine.

- line_total = quantity x unit_price; tax_amount = line_total x tax_rate;
  subtotal = Σ line_total; tax_total = Σ tax_amount; total = subtotal + tax_total.
- All money is Decimal, quantized to 2 dp.
- tax_rate derives from tax_type via get_tax_rate; zero-rated allowed.
- Parity: identical results to the Expenses engine for the same inputs (DRY).
- No duplicated calc logic: the service delegates to app.common.financial.

Pure-function tests — no database required (calculate_totals is a classmethod).
"""

from decimal import Decimal
from typing import ClassVar

from app.constants.enums import TaxType
from app.modules.expenses.schemas import ExpenseLineItemCreate
from app.modules.expenses.service import ExpenseService
from app.modules.purchase_orders.schemas import PurchaseOrderLineItemCreate
from app.modules.purchase_orders.service import PurchaseOrderService


def _po_item(**kw) -> PurchaseOrderLineItemCreate:
    base = {
        "item_name": "Item",
        "description": "desc",
        "quantity": Decimal("1"),
        "unit_price": Decimal("100.00"),
        "tax_type": TaxType.VAT_16,
    }
    base.update(kw)
    return PurchaseOrderLineItemCreate(**base)


class TestPurchaseOrderCalculateTotals:
    """calculate_totals over the acceptance-criteria input matrix."""

    def test_empty_line_items(self):
        result = PurchaseOrderService.calculate_totals([])
        assert result.subtotal == Decimal("0.00")
        assert result.tax_total == Decimal("0.00")
        assert result.total == Decimal("0.00")
        assert result.line_items == []

    def test_single_item_vat_16(self):
        result = PurchaseOrderService.calculate_totals(
            [_po_item(quantity=Decimal("2"), unit_price=Decimal("100.00"))]
        )
        # line_total = 2 x 100 = 200.00; tax = 200 x 0.16 = 32.00
        assert result.subtotal == Decimal("200.00")
        assert result.tax_total == Decimal("32.00")
        assert result.total == Decimal("232.00")
        assert len(result.line_items) == 1

    def test_multi_item_mixed_tax(self):
        result = PurchaseOrderService.calculate_totals(
            [
                _po_item(quantity=Decimal("2"), unit_price=Decimal("100.00")),
                _po_item(
                    quantity=Decimal("3"),
                    unit_price=Decimal("50.00"),
                    tax_type=TaxType.VAT_8,
                ),
            ]
        )
        # 200.00 + 150.00 = 350.00 subtotal
        # tax = 32.00 + 12.00 = 44.00
        assert result.subtotal == Decimal("350.00")
        assert result.tax_total == Decimal("44.00")
        assert result.total == Decimal("394.00")

    def test_zero_rated_allowed(self):
        result = PurchaseOrderService.calculate_totals(
            [
                _po_item(unit_price=Decimal("500.00"), tax_type=TaxType.VAT_0),
                _po_item(unit_price=Decimal("250.00"), tax_type=TaxType.EXEMPT),
            ]
        )
        assert result.subtotal == Decimal("750.00")
        assert result.tax_total == Decimal("0.00")
        assert result.total == Decimal("750.00")

    def test_large_quantities_no_drift(self):
        result = PurchaseOrderService.calculate_totals(
            [_po_item(quantity=Decimal("100000"), unit_price=Decimal("999.99"))]
        )
        # 100000 x 999.99 = 99,999,000.00; tax = x 0.16 = 15,999,840.00
        assert result.subtotal == Decimal("99999000.00")
        assert result.tax_total == Decimal("15999840.00")
        assert result.total == Decimal("115998840.00")

    def test_all_money_is_decimal_2dp(self):
        result = PurchaseOrderService.calculate_totals(
            [_po_item(quantity=Decimal("3"), unit_price=Decimal("33.33"))]
        )
        for value in (result.subtotal, result.tax_total, result.total):
            assert isinstance(value, Decimal)
            assert -value.as_tuple().exponent == 2


class TestExpenseParity:
    """PO totals must be byte-for-byte identical to Expenses for same inputs."""

    INPUTS: ClassVar = [
        {
            "item_name": "A",
            "description": "d",
            "quantity": Decimal("2"),
            "unit_price": Decimal("100.00"),
            "tax_type": TaxType.VAT_16,
        },
        {
            "item_name": "B",
            "description": "d",
            "quantity": Decimal("7"),
            "unit_price": Decimal("12.34"),
            "tax_type": TaxType.VAT_8,
        },
        {
            "item_name": "C",
            "description": "d",
            "quantity": Decimal("1"),
            "unit_price": Decimal("0.00"),
            "tax_type": TaxType.VAT_0,
        },
    ]

    def test_totals_match_expenses(self):
        po = PurchaseOrderService.calculate_totals(
            [PurchaseOrderLineItemCreate(**i) for i in self.INPUTS]
        )
        exp = ExpenseService.calculate_totals(
            [ExpenseLineItemCreate(**i) for i in self.INPUTS]
        )
        assert po.subtotal == exp.subtotal
        assert po.tax_total == exp.tax_total
        # PO.total is the same arithmetic as Expense.total_due (no discount).
        assert po.total == exp.total_due
        assert po.line_items == exp.line_items


class TestDelegatesToSharedFinancial:
    """No-duplication guard: the service must call app.common.financial."""

    def test_build_and_sum_delegate(self, monkeypatch):
        import app.modules.purchase_orders.service as svc

        calls = {"build": 0, "sum": 0}
        real_build = svc.build_line_items
        real_sum = svc.sum_line_totals

        def spy_build(items):
            calls["build"] += 1
            return real_build(items)

        def spy_sum(data):
            calls["sum"] += 1
            return real_sum(data)

        monkeypatch.setattr(svc, "build_line_items", spy_build)
        monkeypatch.setattr(svc, "sum_line_totals", spy_sum)

        PurchaseOrderService.calculate_totals([_po_item()])
        assert calls["build"] == 1
        assert calls["sum"] == 1
