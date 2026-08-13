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

# Default onboarding task template (issue #41). Seeded verbatim, in this
# exact order, from the Onboarding.svg design export. Used whenever the
# organisation has never configured its own template (the owner_profiles
# column is NULL). Copied onto each onboarding checklist at CREATE TIME
# ONLY (template versioning): editing the template never mutates existing
# checklists.
DEFAULT_ONBOARDING_TASKS: tuple[str, ...] = (
    "Kick-off meeting",
    "Tenant & domain setup",
    "Licenses assigned",
    "Data migration",
    "Security baseline",
    "User training",
    "Handover to support",
)

# Bounds for a configurable onboarding task template (schema-enforced).
MAX_ONBOARDING_TEMPLATE_TASKS: int = 20
MAX_ONBOARDING_TASK_LABEL_LENGTH: int = 255

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

# Sales Desk analytics (issue #45). Default per-stage win probabilities used
# for the weighted pipeline value, calibrated against the Pipeline.svg
# "Weighted" column ($2,700→$270, $5,760→$1,440, $11,880→$5,940,
# $51,840→$38,880). These are *seed* defaults: an organisation can override
# them via the owner settings (owner_profiles.deal_stage_probabilities);
# the persisted value wins. Kept as decimal strings — no floats anywhere.
DEFAULT_DEAL_STAGE_PROBABILITIES: dict[str, str] = {
    "activation": "0.10",
    "qualification": "0.25",
    "proposal_quote": "0.50",
    "negotiation": "0.75",
}

# Default quarterly won-value target (USD) for the Dashboard's
# "Rep pipeline vs. quota" panel (issue #45), applied to any rep without an
# explicit entry in the owner rep_quarterly_targets setting. Calibrated
# against Dashboard.svg ("$6,210 / $90,000").
DEFAULT_REP_QUARTERLY_TARGET_USD: str = "90000.00"

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
