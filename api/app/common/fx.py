"""Display/conversion FX conventions (Sales Desk, issue #44).

Single source of truth for currency conversion across the Quotes & pricing
surfaces: the USD-equivalent column on the quote list, the reference-currency
conversions row in the quote builder, and any other display-only conversion.

Conventions (calibrated against the verbatim design exports on branch
``sales-desk-designs``):

- Rates are expressed as *units of currency per 1 USD*
  (:data:`app.constants.settings_defaults.DEFAULT_FX_UNITS_PER_USD`).
- Conversions are DISPLAY-ONLY. Nothing converted here is ever persisted as
  a document amount; quotes are stored solely in their own (USD/KES)
  currency and totals are computed by the existing document services.
- EUR/GBP are *reference* currencies: amounts may be converted into them
  for display, but no document can be issued in them.
- All results are quantized with the shared money policy
  (:func:`app.common.financial.quantize_money`).
"""

from decimal import Decimal

from app.common.financial import quantize_money
from app.constants.settings_defaults import DEFAULT_FX_UNITS_PER_USD


def units_per_usd(currency: str) -> Decimal:
    """Units of ``currency`` per 1 USD.

    Raises ``ValueError`` for a currency outside the FX table — a missing
    rate is a display-correctness bug, and the Currency enum already
    constrains callers to valid values.
    """
    try:
        return Decimal(DEFAULT_FX_UNITS_PER_USD[currency])
    except KeyError as e:
        raise ValueError(f"No FX convention for currency: {currency!r}") from e


def usd_equivalent(amount: Decimal, currency: str) -> Decimal:
    """Convert an amount in ``currency`` to its USD equivalent (2 dp)."""
    return quantize_money(Decimal(amount) / units_per_usd(currency))


def convert_from_usd(amount_usd: Decimal, currency: str) -> Decimal:
    """Convert a USD amount into ``currency`` (2 dp)."""
    return quantize_money(Decimal(amount_usd) * units_per_usd(currency))


def reference_conversions(amount: Decimal, currency: str) -> dict[str, Decimal]:
    """The builder's conversions row for an amount in ``currency``.

    Returns the USD equivalent plus the EUR/GBP reference-currency values
    derived from it (design: "~= $1,489.44 * (EUR)1,370.28 * (GBP)1,176.66"),
    each quantized independently per the shared money policy.
    """
    usd = usd_equivalent(amount, currency)
    return {
        "USD": usd,
        "EUR": convert_from_usd(usd, "EUR"),
        "GBP": convert_from_usd(usd, "GBP"),
    }
