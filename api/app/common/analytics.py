"""Central product-analytics emitter.

Single dispatch point for product analytics events so call sites never
hand-roll emission (DRY). The v1 sink is a dedicated structured logger
(``analytics``); replacing it with a real analytics provider later touches
only this module.

Three hard guarantees, all enforced here so every call site inherits them:

1. **Fire-and-forget.** :func:`emit` never raises. Any failure building or
   dispatching an event is swallowed and logged at WARNING, so a broken
   emitter can never fail the business action it instruments (PO-14 / gate
   B).
2. **No PII.** Only IDs, booleans and scalar counts are intended in event
   properties. :func:`_scrub_properties` drops any value that looks like a
   raw email address as a defence-in-depth net; recipient presence is
   represented as the boolean ``recipient_email_present``, never the
   address itself.
3. **Sanitised failure reasons.** :func:`sanitize_error_reason` reduces an
   exception or message to a short, stable category token rather than a raw
   stack trace, for ``po_send_failed.error_reason``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Dedicated sink so analytics can be routed/scraped independently of app logs.
_analytics_logger = logging.getLogger("analytics")

# Rough email shape — used only to scrub values that should never be sent.
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class POEvent:
    """Purchase-order analytics event names (PRD §17). Declared once."""

    CREATED = "po_created"
    SAVED = "po_saved"
    VIEWED = "po_viewed"
    SENT = "po_sent"
    SEND_FAILED = "po_send_failed"
    CONVERTED_TO_BILL = "po_converted_to_bill"
    PDF_DOWNLOADED = "po_pdf_downloaded"
    DUPLICATED = "po_duplicated"
    CANCELLED = "po_cancelled"
    DELETED = "po_deleted"
    DOCUMENT_ATTACHED = "po_document_attached"
    DOCUMENT_DOWNLOADED = "po_document_downloaded"
    DOCUMENT_DELETED = "po_document_deleted"
    LIST_EXPORTED = "po_list_exported"


# Documented property contract per event (PRD §17). Used by tests to assert
# every event is emitted with exactly its declared property set, and to keep
# the names/props defined in one place.
PO_EVENT_PROPERTIES: dict[str, tuple[str, ...]] = {
    POEvent.CREATED: ("po_id", "vendor_id", "currency", "item_count", "recurring"),
    POEvent.SAVED: ("po_id", "status"),
    POEvent.VIEWED: ("po_id",),
    POEvent.SENT: ("po_id", "vendor_id", "recipient_email_present"),
    POEvent.SEND_FAILED: ("po_id", "error_reason"),
    POEvent.CONVERTED_TO_BILL: ("po_id", "bill_id"),
    POEvent.PDF_DOWNLOADED: ("po_id",),
    POEvent.DUPLICATED: ("source_po_id", "new_po_id"),
    POEvent.CANCELLED: ("po_id",),
    POEvent.DELETED: ("po_id",),
    POEvent.DOCUMENT_ATTACHED: ("po_id", "file_size_kb"),
    POEvent.DOCUMENT_DOWNLOADED: ("po_id", "document_id"),
    POEvent.DOCUMENT_DELETED: ("po_id", "document_id"),
    POEvent.LIST_EXPORTED: ("active_filter_tab", "row_count"),
}

# Stable, sanitised categories for po_send_failed.error_reason. Mapped from
# coarse signals in the underlying error so we never leak a stack trace.
_SEND_ERROR_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("throttl", "ses_throttled"),
    ("rate exceeded", "ses_throttled"),
    ("timeout", "timeout"),
    ("timed out", "timeout"),
    ("connection", "connection_error"),
    ("credential", "auth_error"),
    ("access denied", "auth_error"),
    ("not authorized", "auth_error"),
    ("address", "invalid_recipient"),
    ("recipient", "invalid_recipient"),
    ("verify", "unverified_sender"),
)


def sanitize_error_reason(error: Exception | str | None) -> str:
    """Reduce an error to a short, stable, PII-free category token.

    Never returns a stack trace or a raw provider message: the result is one
    of a small set of categories (plus ``unknown_error``), safe to store and
    chart. Matching is done on a lower-cased view of the message only.
    """
    if error is None:
        return "unknown_error"
    message = str(error).lower()
    for needle, category in _SEND_ERROR_CATEGORIES:
        if needle in message:
            return category
    return "unknown_error"


def _scrub_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """Defence-in-depth: drop any property value that looks like a raw email.

    Call sites are expected to pass only IDs/booleans/scalars, but this guard
    guarantees a stray email can never reach the analytics sink even if a
    caller regresses.
    """
    scrubbed: dict[str, Any] = {}
    for key, value in properties.items():
        if isinstance(value, str) and _EMAIL_RE.search(value):
            # Preserve the signal (presence) without the PII.
            scrubbed[key] = "[redacted]"
            continue
        scrubbed[key] = value
    return scrubbed


def emit(event: str, /, **properties: Any) -> None:
    """Emit a single analytics event. Never raises.

    Fire-and-forget by contract: any exception while scrubbing or dispatching
    is caught and logged at WARNING so the calling business action is never
    affected. ``properties`` should contain only IDs, booleans and scalar
    counts — no PII.
    """
    try:
        payload = _scrub_properties(properties)
        _analytics_logger.info(
            "analytics_event",
            extra={"event": event, "properties": payload},
        )
    except Exception:  # noqa: BLE001 — analytics must never break the caller
        logger.warning("Analytics emission failed for %s", event, exc_info=True)
