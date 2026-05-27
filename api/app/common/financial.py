"""
Shared financial calculation utilities.

Single source of truth for tax rates and discount logic used across
the Invoices, Quotes, and Expenses modules. Any future tax type must be added here only.
"""
from datetime import date
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from app.constants.enums import DiscountType, TaxType


# Line Item Input Protocol

@runtime_checkable
class LineItemInput(Protocol):
    """Structural type for any object with the fields needed to build a line item."""
    item_name: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_type: TaxType | str



# Tax Rates


TAX_RATES: dict[TaxType, Decimal] = {
    TaxType.VAT_16: Decimal("0.16"),
    TaxType.VAT_8:  Decimal("0.08"),
    TaxType.VAT_0:  Decimal("0.00"),
    TaxType.EXEMPT: Decimal("0.00"),
    TaxType.NO_TAX: Decimal("0.00"),
}


def get_tax_rate(tax_type: TaxType) -> Decimal:
    """Return the tax rate for a given TaxType. Defaults to 0.00 for unknown types."""
    return TAX_RATES.get(tax_type, Decimal("0.00"))



# Line Item Calculation


def calculate_line_item(
    quantity: Decimal,
    unit_price: Decimal,
    tax_type: TaxType,
) -> tuple[Decimal, Decimal]:
    """
    Compute (line_total, tax_amount) for a single line item.

    Returns:
        line_total  = quantity × unit_price
        tax_amount  = line_total × tax_rate
    """
    line_total = quantity * unit_price
    tax_rate   = get_tax_rate(tax_type)
    tax_amount = line_total * tax_rate
    return line_total, tax_amount


# Line Item Builders


def build_line_items(raw_items: list[Any]) -> list[dict]:
    """
    Build calculated line-item dicts from a list of inputs.

    Each input must have: item_name, description, quantity, unit_price, tax_type.
    Inputs may be Pydantic models, dataclasses, or plain dicts — accessor
    normalisation is handled internally.

    Returns a list of dicts ready for ORM model construction, each with
    line_number, item_name, description, quantity, unit_price, line_total,
    tax_type, tax_amount.
    """
    result: list[dict] = []
    for idx, item in enumerate(raw_items, start=1):
        # Support both attribute access (Pydantic/dataclass) and dict access
        _get = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)
        quantity   = _get("quantity")
        unit_price = _get("unit_price")
        tax_type   = _get("tax_type")

        line_total, tax_amount = calculate_line_item(quantity, unit_price, tax_type)
        result.append({
            "line_number":  idx,
            "item_name":    _get("item_name"),
            "description":  _get("description"),
            "quantity":     quantity,
            "unit_price":   unit_price,
            "line_total":   line_total,
            "tax_type":     tax_type,
            "tax_amount":   tax_amount,
        })
    return result


def sum_line_totals(line_items_data: list[dict]) -> tuple[Decimal, Decimal]:
    """
    Return (subtotal, tax_total) from pre-calculated line-item dicts.

    Safe on empty lists — returns (Decimal('0.00'), Decimal('0.00')).
    """
    subtotal = sum(
        (item["line_total"] for item in line_items_data), Decimal("0.00")
    )
    tax_total = sum(
        (item["tax_amount"] for item in line_items_data), Decimal("0.00")
    )
    return subtotal, tax_total



# Discount Calculation


def calculate_discount(
    subtotal: Decimal,
    discount_type: DiscountType | None,
    discount_amount: Decimal | None,
    discount_percentage: Decimal | None,
) -> Decimal:
    """
    Calculate the discount value from the provided parameters.

    Rules:
    - Fixed-amount discount is capped at subtotal to prevent negative totals.
    - Percentage discount is always applied against subtotal.
    - Returns 0.00 when no discount type is supplied.
    """
    if discount_type == DiscountType.AMOUNT and discount_amount:
        return min(discount_amount, subtotal)
    if discount_type == DiscountType.PERCENTAGE and discount_percentage:
        return (subtotal * discount_percentage / Decimal("100")).quantize(Decimal("0.01"))
    return Decimal("0.00")



# Overdue Status Calculation


def check_is_overdue(status: str, due_date: date) -> bool:
    """
    Check if a record is overdue based on status and due date.
    Considers 'paid' and 'canceled' as terminal statuses that cannot be overdue.
    """
    from datetime import date as dt_date
    return status.lower() not in ("paid", "canceled") and due_date < dt_date.today()


def calculate_days_overdue(status: str, due_date: date) -> int:
    """
    Calculate the number of days a record is overdue.
    Returns 0 if not overdue.
    """
    from datetime import date as dt_date
    if not check_is_overdue(status, due_date):
        return 0
    return (dt_date.today() - due_date).days
