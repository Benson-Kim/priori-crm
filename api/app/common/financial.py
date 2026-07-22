"""
Shared financial calculation utilities.

Single source of truth for tax rates and discount logic used across
the Invoices, Quotes, and Expenses modules. Any future tax type must be added here only.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.common.exceptions import BadRequestException
from app.constants.enums import DiscountType, TaxType

# Money Rounding Policy

# Single source of truth for monetary rounding across every document module,
# statement, PDF, and Excel export. Money is stored in 2-dp columns, so every
# computed monetary value must be quantized to two decimal places using
# banker's-free ROUND_HALF_UP before it is summed or persisted. Rounding per
# line (rather than only at the end) keeps persisted line totals consistent
# with their summed document totals and reproducible across recalculation
MONEY_QUANTUM = Decimal("0.01")


def quantize_money(value: Decimal) -> Decimal:
    """Round a monetary value to 2 decimal places using ROUND_HALF_UP."""
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


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


class TaxApplicability(StrEnum):
    """Business scope required for an effective-dated tax treatment."""

    GENERAL = "general"
    PETROLEUM = "petroleum"


@dataclass(frozen=True)
class TaxRateRule:
    """Effective-dated and scoped tax-treatment rule."""

    jurisdiction: str
    treatment: TaxType
    rate: Decimal
    effective_from: date
    effective_to: date | None
    applicability: TaxApplicability


KENYA_TAX_RATE_RULES: tuple[TaxRateRule, ...] = (
    TaxRateRule(
        "KE", TaxType.VAT_16, Decimal("0.16"), date.min, None, TaxApplicability.GENERAL
    ),
    TaxRateRule(
        "KE",
        TaxType.PETROLEUM_VAT_8,
        Decimal("0.08"),
        date(2018, 9, 1),
        date(2023, 6, 30),
        TaxApplicability.PETROLEUM,
    ),
    TaxRateRule(
        "KE",
        TaxType.PETROLEUM_VAT_13,
        Decimal("0.13"),
        date(2026, 4, 15),
        date(2026, 4, 15),
        TaxApplicability.PETROLEUM,
    ),
    TaxRateRule(
        "KE",
        TaxType.PETROLEUM_VAT_8,
        Decimal("0.08"),
        date(2026, 4, 16),
        date(2026, 10, 14),
        TaxApplicability.PETROLEUM,
    ),
    TaxRateRule(
        "KE", TaxType.VAT_0, Decimal("0.00"), date.min, None, TaxApplicability.GENERAL
    ),
    TaxRateRule(
        "KE", TaxType.EXEMPT, Decimal("0.00"), date.min, None, TaxApplicability.GENERAL
    ),
    TaxRateRule(
        "KE", TaxType.NO_TAX, Decimal("0.00"), date.min, None, TaxApplicability.GENERAL
    ),
)

# Arithmetic registry: retired treatments remain calculable for historical records.
TAX_RATES: dict[TaxType, Decimal] = {
    TaxType.VAT_8: Decimal("0.08"),
    **{rule.treatment: rule.rate for rule in KENYA_TAX_RATE_RULES},
}


def validate_tax_treatment_for_date(tax_type: TaxType | str, tax_point: date) -> None:
    """Reject a treatment that was not effective on the document tax point."""
    treatment = TaxType(tax_type)
    if treatment == TaxType.VAT_8:
        raise BadRequestException(
            detail=(
                "Legacy unscoped VAT 8% records are read-only; select a "
                "petroleum-scoped treatment for a supported tax point"
            ),
            field="tax_type",
        )
    matching = [
        rule
        for rule in KENYA_TAX_RATE_RULES
        if rule.treatment == treatment
        and rule.effective_from <= tax_point
        and (rule.effective_to is None or tax_point <= rule.effective_to)
    ]
    if not matching:
        raise BadRequestException(
            detail=(
                f"Tax treatment '{treatment.value}' is not valid on "
                f"{tax_point.isoformat()}"
            ),
            field="tax_type",
        )


def validate_document_vat_rate_for_date(
    vat_rate: Decimal | None,
    tax_point: date,
) -> None:
    """Reject petroleum rates where the document has no applicability scope."""
    if vat_rate is None:
        return
    normalized_rate = Decimal(str(vat_rate))
    if normalized_rate in {Decimal("0.08"), Decimal("0.13")}:
        raise BadRequestException(
            detail=(
                "Petroleum VAT rates require a petroleum-scoped line treatment "
                f"for tax point {tax_point.isoformat()}"
            ),
            field="vat_rate",
        )


def validate_line_item_treatments_for_date(
    line_items: Iterable[Any],
    tax_point: date,
) -> None:
    """Validate persisted or incoming line-item treatments for one tax point.

    Date-only document updates do not rebuild line items, so validation must
    also work against ORM rows. This helper accepts mappings and attribute-based
    objects and deliberately performs eligibility validation without changing
    any stored historical rate or amount.
    """
    for item in line_items:
        tax_type = (
            item.get("tax_type")
            if isinstance(item, dict)
            else getattr(item, "tax_type", None)
        )
        if tax_type is not None:
            validate_tax_treatment_for_date(tax_type, tax_point)


def get_tax_rate(tax_type: TaxType) -> Decimal:
    """Return the tax rate for a given TaxType.

    Raises ValueError on an unknown tax type rather than silently returning
    0.00: a missing rate is a revenue/compliance risk, and the
    TaxType enum already constrains callers to valid values, so this only
    fires on genuinely bad data.
    """
    try:
        return TAX_RATES[tax_type]
    except KeyError as e:
        raise ValueError(f"Unknown tax type: {tax_type!r}") from e


# Line Item Calculation


def calculate_line_item(
    quantity: Decimal,
    unit_price: Decimal,
    tax_type: TaxType,
) -> tuple[Decimal, Decimal]:
    """
    Compute (line_total, tax_amount) for a single line item.

    Returns:
        line_total  = round(quantity x unit_price)
        tax_amount  = round(line_total x tax_rate)

    Both values are quantized to 2 dp via the shared money policy so that
    summing line items can never accumulate sub-cent drift.
    """
    line_total = quantize_money(quantity * unit_price)
    tax_rate = get_tax_rate(tax_type)
    tax_amount = quantize_money(line_total * tax_rate)
    return line_total, tax_amount


# Line Item Builders


def build_line_items(
    raw_items: list[Any], *, tax_point_date: date | None = None
) -> list[dict]:
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
        # Support both attribute access (Pydantic/dataclass) and dict access.
        # Bind `item` as a default arg so the closure captures this iteration's
        # value rather than the loop variable (B023).
        _get = (
            item.get
            if isinstance(item, dict)
            else lambda k, d=None, _it=item: getattr(_it, k, d)
        )

        item_name = _get("item_name")
        description = _get("description")
        quantity = _get("quantity")
        unit_price = _get("unit_price")
        raw_tax_type = _get("tax_type")

        # Validate required fields are present rather than silently coercing a
        # missing value to None (which would TypeError in calculation or
        # persist a NULL).
        if item_name is None:
            raise ValueError(f"Line item {idx} is missing required field 'item_name'.")
        if description is None:
            raise ValueError(
                f"Line item {idx} is missing required field 'description'."
            )
        if quantity is None:
            raise ValueError(f"Line item {idx} is missing required field 'quantity'.")
        if unit_price is None:
            raise ValueError(f"Line item {idx} is missing required field 'unit_price'.")
        if raw_tax_type is None:
            raise ValueError(f"Line item {idx} is missing required field 'tax_type'.")

        tax_type = TaxType(raw_tax_type)
        if tax_point_date:
            validate_tax_treatment_for_date(tax_type, tax_point_date)

        line_total, tax_amount = calculate_line_item(quantity, unit_price, tax_type)
        result.append(
            {
                "line_number": idx,
                "item_name": item_name,
                "description": description,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_total": line_total,
                "tax_type": tax_type,
                "tax_amount": tax_amount,
            }
        )
    return result


def sum_line_totals(line_items_data: list[dict]) -> tuple[Decimal, Decimal]:
    """
    Return (subtotal, tax_total) from pre-calculated line-item dicts.

    Safe on empty lists — returns (Decimal('0.00'), Decimal('0.00')).
    """
    subtotal = sum((item["line_total"] for item in line_items_data), Decimal("0.00"))
    tax_total = sum((item["tax_amount"] for item in line_items_data), Decimal("0.00"))
    return subtotal, tax_total


def allocate_discounted_line_taxes(
    line_items_data: list[dict],
    discount_value: Decimal,
) -> list[dict]:
    """Allocate a document discount across line taxable bases and recalculate tax.

    The gross ``line_total`` values remain unchanged so the invoice subtotal and
    displayed document discount retain their existing meaning. ``taxable_value``
    stores each line's post-discount base, while ``tax_amount`` is rounded from
    that base using the shared money policy.

    The final line absorbs any cent-level allocation remainder so the persisted
    taxable bases always sum exactly to ``subtotal - discount_value``.
    """
    subtotal = sum(
        (Decimal(str(item["line_total"])) for item in line_items_data),
        Decimal("0.00"),
    )
    net_subtotal = quantize_money(max(subtotal - discount_value, Decimal("0.00")))

    if subtotal <= 0:
        for item in line_items_data:
            item["taxable_value"] = Decimal("0.00")
            item["tax_amount"] = Decimal("0.00")
        return line_items_data

    exact_shares = [
        Decimal(str(item["line_total"])) * net_subtotal / subtotal
        for item in line_items_data
    ]
    taxable_values = [
        share.quantize(MONEY_QUANTUM, rounding=ROUND_DOWN) for share in exact_shares
    ]
    remaining_cents = int(
        (net_subtotal - sum(taxable_values, Decimal("0.00"))) / MONEY_QUANTUM
    )
    remainder_order = sorted(
        range(len(line_items_data)),
        key=lambda index: (exact_shares[index] - taxable_values[index], -index),
        reverse=True,
    )
    for index in remainder_order[:remaining_cents]:
        taxable_values[index] += MONEY_QUANTUM

    for item, taxable_value in zip(line_items_data, taxable_values, strict=True):
        tax_rate = get_tax_rate(TaxType(item["tax_type"]))
        item["taxable_value"] = taxable_value
        item["tax_amount"] = quantize_money(taxable_value * tax_rate)

    return line_items_data


# Document-level (subtotal) VAT


def calculate_subtotal_vat(
    subtotal: Decimal,
    vat_enabled: bool,
    vat_rate: Decimal | None,
) -> Decimal:
    """
    Compute document-level VAT on the subtotal.

    Used by purchase orders, whose VAT is a single PO-level charge on the
    subtotal rather than a per-line tax. Returns 0.00 when VAT is disabled or
    the rate is missing. The result is quantized to 2 dp with the shared money
    policy so it matches persisted/PDF totals exactly.

    ``vat_rate`` is a client-supplied fraction in [0, 1] (e.g.
    Decimal('0.16') for 16%). It is NOT looked up from the shared
    TAX_RATES table — PO-level VAT is a free rate chosen by the user,
    not a per-line tax type. The schema enforces the 0..1 range; the
    service enforces that the rate is present when VAT is enabled.
    """
    if not vat_enabled or not vat_rate:
        return Decimal("0.00")
    return quantize_money(subtotal * vat_rate)


def neutralize_line_tax(line_items_data: list[dict]) -> list[dict]:
    """Force per-line tax to no_tax/0 when document-level VAT is enabled.

    Document-level VAT (purchase orders, and sales invoices/quotes with the
    VAT toggle on) is charged once on the (discounted) subtotal, so a line
    item must never also carry per-line tax — that would double-tax the
    document. Mutates the built dicts in place and returns them.
    """
    for item in line_items_data:
        item["tax_type"] = TaxType.NO_TAX.value
        item["tax_amount"] = Decimal("0.00")
    return line_items_data


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
        return quantize_money(min(discount_amount, subtotal))
    if discount_type == DiscountType.PERCENTAGE and discount_percentage:
        return quantize_money(subtotal * discount_percentage / Decimal("100"))
    return Decimal("0.00")
