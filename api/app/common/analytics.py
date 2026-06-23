"""Centralized, fire-and-forget analytics instrumentation.

This module is the single source of truth for product-analytics events. It
exists so that:

- Event names live in one place (the ``PurchaseOrderEvent`` enum and friends)
  instead of being copy-pasted as string literals across services and
  routers.
- Emission can NEVER break the business action it instruments. ``emit_event``
  swallows every error (a misbehaving sink, an unserialisable property) and
  logs it; it never raises.
- No PII leaves the process. Event properties are restricted to IDs, enums,
  counts and booleans (e.g. ``recipient_email_present``). A defensive guard
  drops any value that looks like a raw email address, and
  ``sanitize_error_reason`` reduces an exception to a stable category string
  rather than a raw message / stack trace.

The actual delivery sink is intentionally minimal for v1: events are written
to a dedicated structured logger (``app.analytics``) which the platform's log
pipeline forwards to the analytics warehouse. Swapping in a real client later
is a single change in ``_dispatch`` — call sites do not change.
"""

from __future__ import annotations

import logging
import re
from enum import StrEnum
from typing import Any

# Dedicated logger so analytics events can be routed/sampled independently of
# application logs by the log pipeline.
analytics_logger = logging.getLogger("app.analytics")

# Internal logger for instrumentation failures (kept off the analytics stream
# so a sink problem is visible in ordinary application logs).
logger = logging.getLogger(__name__)


class PurchaseOrderEvent(StrEnum):
    """The 14 Purchase Order analytics events.

    Documented properties per event (carried in the ``properties`` dict):

    - PO_CREATED            -> po_id, vendor_id, currency, item_count, recurring
    - PO_SAVED              -> po_id, status
    - PO_VIEWED             -> po_id
    - PO_SENT               -> po_id, vendor_id, recipient_email_present
    - PO_SEND_FAILED        -> po_id, error_reason  (sanitised category)
    - PO_CONVERTED_TO_BILL  -> po_id, bill_id
    - PO_PDF_DOWNLOADED     -> po_id
    - PO_DUPLICATED         -> source_po_id, new_po_id
    - PO_CANCELLED          -> po_id
    - PO_DELETED            -> po_id
    - PO_DOCUMENT_ATTACHED  -> po_id, file_size_kb
    - PO_DOCUMENT_DOWNLOADED-> po_id, document_id
    - PO_DOCUMENT_DELETED   -> po_id, document_id
    - PO_LIST_EXPORTED      -> active_filter_tab, row_count
    - PO_PAYMENT_RECORDED   -> po_id, settled  (PO-19; settled = balance cleared)
    - PO_MARKED_SENT        -> po_id  (PO-20; DRAFT->SENT without email)
    """

    PO_CREATED = "po_created"
    PO_SAVED = "po_saved"
    PO_VIEWED = "po_viewed"
    PO_SENT = "po_sent"
    PO_SEND_FAILED = "po_send_failed"
    PO_CONVERTED_TO_BILL = "po_converted_to_bill"
    PO_PDF_DOWNLOADED = "po_pdf_downloaded"
    PO_DUPLICATED = "po_duplicated"
    PO_CANCELLED = "po_cancelled"
    PO_DELETED = "po_deleted"
    PO_DOCUMENT_ATTACHED = "po_document_attached"
    PO_DOCUMENT_DOWNLOADED = "po_document_downloaded"
    PO_DOCUMENT_DELETED = "po_document_deleted"
    PO_LIST_EXPORTED = "po_list_exported"
    PO_PAYMENTS_EXPORTED = "po_payments_exported"
    PO_PAYMENT_RECORDED = "po_payment_recorded"
    PO_MARKED_SENT = "po_marked_sent"


# Heuristic e-mail detector used to keep raw addresses out of analytics. We
# never *want* an email in a property (call sites pass booleans), this is a
# defence-in-depth net so an accidental address is dropped, not shipped.
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

#: Stable, PII-free categories for ``po_send_failed.error_reason``. The raw
#: exception is mapped to one of these so the warehouse sees a low-cardinality
#: category rather than a stack trace or a message that might embed an address.
SEND_ERROR_TIMEOUT = "timeout"
SEND_ERROR_RECIPIENT_REJECTED = "recipient_rejected"
SEND_ERROR_THROTTLED = "throttled"
SEND_ERROR_AUTH = "auth_error"
SEND_ERROR_PROVIDER_UNAVAILABLE = "provider_unavailable"
SEND_ERROR_UNKNOWN = "unknown"


def sanitize_error_reason(error: BaseException | str | None) -> str:
    """Map an arbitrary send error to a stable, PII-free category.

    The result is one of the ``SEND_ERROR_*`` constants. The raw error text is
    classified by keyword and then discarded, so neither a stack trace nor a
    message that might contain a recipient address is ever emitted
    (``error_reason`` is a sanitised category, not a raw trace).
    """
    if error is None:
        return SEND_ERROR_UNKNOWN

    text = (error if isinstance(error, str) else str(error)).lower()

    if "timeout" in text or "timed out" in text:
        return SEND_ERROR_TIMEOUT
    if "throttl" in text or "rate" in text or "too many" in text:
        return SEND_ERROR_THROTTLED
    if (
        "rejected" in text
        or "recipient" in text
        or "mailbox" in text
        or "address" in text
    ):
        return SEND_ERROR_RECIPIENT_REJECTED
    if (
        "credential" in text
        or "auth" in text
        or "access denied" in text
        or "forbidden" in text
        or "signature" in text
    ):
        return SEND_ERROR_AUTH
    if (
        "unavailable" in text
        or "connection" in text
        or "service" in text
        or "5" in text[:1]  # leading 5xx provider status
    ):
        return SEND_ERROR_PROVIDER_UNAVAILABLE
    return SEND_ERROR_UNKNOWN


def _scrub(properties: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``properties`` with obvious PII stripped.

    Any string value that matches an email pattern is replaced with the
    sentinel ``"<redacted>"``. This never raises: an unexpected value type is
    passed through untouched.
    """
    if not properties:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in properties.items():
        if isinstance(value, str) and _EMAIL_RE.search(value):
            cleaned[key] = "<redacted>"
        else:
            cleaned[key] = value
    return cleaned


def _dispatch(event_name: str, properties: dict[str, Any]) -> None:
    """Deliver a single event to the sink.

    v1 sink: a structured log line on the dedicated ``app.analytics`` logger.
    Isolated in its own function so a future swap to a real analytics client
    is a one-line change with no impact on call sites.
    """
    analytics_logger.info(
        "analytics_event",
        extra={"event": event_name, "properties": properties},
    )


def emit_event(
    event: PurchaseOrderEvent | str,
    properties: dict[str, Any] | None = None,
) -> None:
    """Emit an analytics event — fire-and-forget, never raises.

    Centralised dispatch for every call site: callers pass an event from
    ``PurchaseOrderEvent`` and a small property dict of IDs / enums / counts /
    booleans. Any failure — a bad payload, a sink/logging error — is caught
    and logged here, so instrumentation can never break or fail the business
    action that triggered it.
    """
    try:
        event_name = event.value if isinstance(event, PurchaseOrderEvent) else event
        _dispatch(event_name, _scrub(properties))
    except Exception:
        # Swallow-and-log: a failed emit must not propagate to the caller.
        logger.warning(
            "Failed to emit analytics event; business action unaffected",
            exc_info=True,
            extra={"event": str(event)},
        )
