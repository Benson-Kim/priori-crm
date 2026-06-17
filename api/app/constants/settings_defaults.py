"""Organisation-level default values for document settings (PO-11).

Single source of truth for the org-scoped Purchase Order defaults persisted
on the :class:`~app.modules.owner.models.OwnerProfile` singleton. Defining
them here (rather than inline in the model/service) keeps the out-of-the-box
values in one place and mirrors the frontend's ``lib/compliance.ts`` constants
so the two layers cannot drift.

These are *seed* defaults only: once an organisation edits its Settings the
persisted value wins. They are applied to a Purchase Order at CREATE TIME
ONLY, so changing a default never retroactively alters existing POs (PO-11
acceptance criteria).
"""

# Out-of-the-box Terms & Conditions text pre-filled on a new Purchase Order
# when the organisation has not set its own default. Must match the frontend
# ``DEFAULT_PURCHASE_ORDER_TERMS`` (lib/compliance.ts) verbatim.
DEFAULT_PURCHASE_ORDER_TERMS: str = (
    "Upon accepting this purchase order, you hereby agree to the terms & "
    "conditions."
)

# Default org jurisdiction (ISO 3166-1 alpha-2). Drives the jurisdiction-aware
# compliance-reference label/tooltip (PO-10). Mirrors the frontend
# ``DEFAULT_ORG_JURISDICTION``.
DEFAULT_ORG_JURISDICTION: str = "KE"

# Hard cap on the stored default Terms & Conditions text. Matches the per-PO
# cap (MAX_TERMS_AND_CONDITIONS_LENGTH) so a default can always be copied onto
# a PO without exceeding the PO-level limit.
MAX_DEFAULT_TERMS_LENGTH: int = 2000

# Hard cap on the stored default Send message (the email body pre-filled in the
# Send modal, PRD §6.6). Matches the PurchaseOrderSendRequest.body cap.
MAX_DEFAULT_SEND_MESSAGE_LENGTH: int = 5000
