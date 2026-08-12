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
