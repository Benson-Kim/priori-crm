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

# Fixed FX table for the Sales Desk USD-equivalent deal value fields
# (lists/aggregations display USD equivalents; detail views show the deal's
# native currency). Values are UNITS OF CURRENCY PER 1 USD, kept as decimal
# strings so no layer ever touches floats. Mirrors the deal-desk prototype's
# FX constant (KES 129.5 per USD). A live FX source is out of scope for the
# deals module and is left to the analytics backend (#45) if needed.
BILLING_CURRENCY_PER_USD: dict[str, str] = {
    "USD": "1",
    "KES": "129.50",
}
