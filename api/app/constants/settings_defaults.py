"""Organisation-level default values for document settings.

Single source of truth for the org-scoped Purchase Order defaults persisted
on the :class:`~app.modules.owner.models.OwnerProfile` singleton. Defining
them here (rather than inline in the model/service) keeps the out-of-the-box
values in one place and mirrors the frontend's ``lib/compliance.ts`` constants
so the two layers cannot drift.

These are *seed* defaults only: once an organisation edits its Settings the
persisted value wins. They are applied to a Purchase Order at CREATE TIME
ONLY, so changing a default never retroactively alters existing POs acceptance criteria).
"""

# Out-of-the-box Terms & Conditions text pre-filled on a new Purchase Order
# when the organisation has not set its own default. Must match the frontend
# ``DEFAULT_PURCHASE_ORDER_TERMS`` (lib/compliance.ts) verbatim.
DEFAULT_PURCHASE_ORDER_TERMS: str = (
    "Upon accepting this purchase order, you hereby agree to the terms & conditions."
)

# Default org jurisdiction (ISO 3166-1 alpha-2). Drives the jurisdiction-aware
# compliance-reference label/tooltip. Mirrors the frontend
# ``DEFAULT_ORG_JURISDICTION``.
DEFAULT_ORG_JURISDICTION: str = "KE"

# Hard cap on the stored default Terms & Conditions text. Matches the per-PO
# cap (MAX_TERMS_AND_CONDITIONS_LENGTH) so a default can always be copied onto
# a PO without exceeding the PO-level limit.
MAX_DEFAULT_TERMS_LENGTH: int = 2000

# Hard cap on the stored default Send message (the email body pre-filled in the Send modal).
# Matches the PurchaseOrderSendRequest.body cap.
MAX_DEFAULT_SEND_MESSAGE_LENGTH: int = 5000

# Seed values for the dual USD/KES customer billing profiles (deal-desk
# parity). Applied when a customer is created AND by the one-off backfill
# migration for pre-existing customers. Credit limits are expressed in the
# profile's OWN currency (no USD round-trip) and kept as decimal strings so
# neither the service layer nor the migration ever touches floats.
DEFAULT_BILLING_PROFILE_DEFAULTS: dict[str, dict[str, str]] = {
    "USD": {
        "payment_terms": "30 days",
        "tax_treatment": "Zero-rated (export)",
        "credit_limit": "25000.00",
    },
    "KES": {
        "payment_terms": "14 days",
        "tax_treatment": "VAT 16%",
        "credit_limit": "10000.00",
    },
}

# Sales Desk product catalog (Quotes & pricing, issue #44). Seeded verbatim
# from the design export ``Quotes___Pricing.svg`` (branch
# ``sales-desk-designs``): the 8 Microsoft 365 SKUs with their USD list
# price per seat per month. Kept as decimal strings so no layer ever
# touches floats. The price-list read (owner module) derives the design's
# "Annual / seat" (monthly x 12) and "10-seat ARR" (monthly x 12 x 10)
# columns from these raw values.
DEFAULT_PRODUCT_CATALOG: list[dict[str, str]] = [
    {"name": "Microsoft 365 Business Basic", "usd_per_seat_month": "6.00"},
    {"name": "Microsoft 365 Business Standard", "usd_per_seat_month": "12.50"},
    {"name": "Microsoft 365 Business Premium", "usd_per_seat_month": "22.00"},
    {"name": "Microsoft 365 E3", "usd_per_seat_month": "36.00"},
    {"name": "Microsoft 365 E5", "usd_per_seat_month": "57.00"},
    {"name": "Exchange Online Plan 1", "usd_per_seat_month": "4.00"},
    {"name": "Teams Phone Standard", "usd_per_seat_month": "8.00"},
    {"name": "Copilot for Microsoft 365", "usd_per_seat_month": "30.00"},
]

# Discount applied to the per-user/month price when a quote line is billed
# annually ("Annual -15%" toggle in the quote builder). Percentage points.
DEFAULT_ANNUAL_BILLING_DISCOUNT_PCT: str = "15"

# Display/conversion FX conventions (Sales Desk). Units of each currency
# per 1 USD, calibrated against the design exports (``App__3_.svg`` totals
# row: KSh 192,882.48 = $1,489.44 = (EUR)1,370.28 = (GBP)1,176.66).
# USD and KES are the billable currencies; EUR/GBP are reference
# (display-only) currencies — a quote can never be issued in them.
DEFAULT_FX_UNITS_PER_USD: dict[str, str] = {
    "USD": "1",
    "KES": "129.50",
    "EUR": "0.92",
    "GBP": "0.79",
}
