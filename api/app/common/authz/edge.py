"""Edge authentication for trust-context headers (issue #83).

``ABAC_TRUST_CONTEXT_HEADERS`` makes the geo/device signals only as good
as the deployment's proxy configuration: nothing in the code verified
that a request actually traversed the stripping edge, and the failure
mode (a proxy that stops stripping) was invisible (#67 line review §2).
This module makes the trust boundary *verifiable in code*, two ways:

- **Edge HMAC** (preferred): the edge stamps ``X-Geo-Signature:
  v1=<hex HMAC-SHA256>`` over the geo header values plus the current
  unix minute. When ``ABAC_EDGE_HMAC_KEY`` is configured, the ``X-Geo-*``
  headers are honoured ONLY with a valid signature inside a small clock
  skew — a spoofed or replayed-stale geo context reads as "no geo
  signal". ``ABAC_EDGE_HMAC_KEY_NEXT`` is accepted alongside for
  zero-downtime rotation, mirroring ``INTERNAL_API_SECRET_NEXT``.
- **Edge CIDR allowlist** (defence in depth, or standalone for edges
  that cannot stamp an HMAC): when ``ABAC_EDGE_CIDRS`` is configured,
  ALL context headers are honoured only when the DIRECT peer (the socket
  address — never X-Forwarded-For, which the edge itself writes) is
  inside the configured ranges.

Both checks fail SAFE, not open: a missing/invalid signature or an
off-range peer degrades the request to "context untrusted" — the same
shape as stripped headers, which the unknown-geo rules already treat as
anomalous for sensitive access — never a lockout and never
"trusted-empty".

Neither mechanism can authenticate ``X-Device-Fingerprint``: it is
deliberately browser-sent (CORS-approved, #67 review F3), so the edge
never generates it. That is why ``client:``-prefixed fingerprints are
corroborating-only by construction (see ``context.py`` /
``baselines.py``): they may FIRE signals but can never suppress the
new-device signal on their own when the server-derived fingerprint
disagrees.

The canonical string signed by the edge is::

    <X-Geo-Country>|<X-Geo-Lat>|<X-Geo-Lon>|<unix_minute>

with the raw header values as sent (missing headers as empty strings)
and ``unix_minute = floor(unix_seconds / 60)``. The edge signs the
CURRENT minute; the API accepts any minute bucket within
``ABAC_EDGE_HMAC_SKEW_SECONDS`` of its own clock, so ordinary clock
drift never drops the geo signal while a captured signature goes stale
within minutes.
"""

import hashlib
import hmac
import ipaddress
import logging
from datetime import datetime
from functools import lru_cache

from fastapi import Request

from app.lib.config import settings

logger = logging.getLogger(__name__)

#: Geo headers covered by the signature, in canonical order.
_SIGNED_HEADERS: tuple[str, ...] = ("X-Geo-Country", "X-Geo-Lat", "X-Geo-Lon")

_SIGNATURE_HEADER = "X-Geo-Signature"
_SIGNATURE_VERSION_PREFIX = "v1="


def edge_hmac_configured() -> bool:
    """Whether geo headers require an edge HMAC signature."""
    return bool(settings.ABAC_EDGE_HMAC_KEY or settings.ABAC_EDGE_HMAC_KEY_NEXT)


def edge_cidrs_configured() -> bool:
    """Whether context headers require the peer to be a known edge."""
    return bool(settings.ABAC_EDGE_CIDRS.strip())


def edge_trust_mode() -> str:
    """The effective trust mode for context headers (for the startup log).

    One of ``disabled`` (context headers not trusted at all),
    ``unauthenticated`` (trusted with no in-code edge verification —
    today's pre-#83 behaviour), ``hmac``, ``cidr`` or ``hmac+cidr``.
    """
    if settings.ABAC_TRUST_CONTEXT_HEADERS is not True:
        return "disabled"
    hmac_on = edge_hmac_configured()
    cidr_on = edge_cidrs_configured()
    if hmac_on and cidr_on:
        return "hmac+cidr"
    if hmac_on:
        return "hmac"
    if cidr_on:
        return "cidr"
    return "unauthenticated"


def log_edge_trust_mode() -> None:
    """Startup visibility of the context-header trust mode (issue #83).

    Deployments must be able to SEE what they are running: an
    unauthenticated-trusted edge is a purely operational invariant whose
    failure is invisible at request time, so it logs as a WARNING —
    consistent with the loud fail-open guards (#67 line review §7).
    """
    mode = edge_trust_mode()
    if mode == "disabled":
        logger.info(
            "ABAC context headers: not trusted (ABAC_TRUST_CONTEXT_HEADERS=false); "
            "geo signals are disabled and device fingerprints are server-derived"
        )
        return
    if mode == "unauthenticated":
        logger.warning(
            "ABAC context headers: trusted WITHOUT in-code edge "
            "authentication. The X-Geo-* / X-Device-Fingerprint headers are "
            "honoured on the sole strength of the proxy configuration "
            "setting and stripping them; if any path bypasses the edge they "
            "are attacker-controlled, and nothing at request time will flag "
            "it. Configure ABAC_EDGE_HMAC_KEY (preferred) and/or "
            "ABAC_EDGE_CIDRS (issue #83). ALERT: ABAC_EDGE_UNAUTHENTICATED"
        )
        return
    logger.info("ABAC context headers: trusted with edge authentication (%s)", mode)


@lru_cache(maxsize=8)
def _parse_edge_cidrs(
    raw: str,
) -> tuple[frozenset[str], tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]]:
    """Parse ``ABAC_EDGE_CIDRS`` into exact entries and CIDR networks.

    Same grammar as ``ABAC_IP_DENYLIST`` (context.py ``_parse_denylist``):
    entries that parse as an IP/CIDR become network matches; anything
    else is an exact-string match (useful for non-IP client identifiers
    such as test transports). Cached on the raw string so a settings
    change invalidates naturally.
    """
    exact: set[str] = set()
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            exact.add(entry)
    return frozenset(exact), tuple(networks)


def peer_is_edge(request: Request) -> bool:
    """Whether the DIRECT peer is inside the configured edge ranges.

    Always the socket peer, never X-Forwarded-For: XFF is written by the
    very edge this check authenticates, so honouring it here would let
    any client vouch for itself. When ``ABAC_EDGE_CIDRS`` is empty this
    check is not configured and returns True (the CIDR layer is opt-in;
    the caller composes it with the HMAC layer and the trust flag).
    """
    if not edge_cidrs_configured():
        return True
    peer = request.client.host if request.client else ""
    if not peer:
        return False
    exact, networks = _parse_edge_cidrs(settings.ABAC_EDGE_CIDRS)
    if peer in exact:
        return True
    if networks:
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        return any(address in network for network in networks)
    return False


def _canonical_message(request: Request, unix_minute: int) -> bytes:
    values = [request.headers.get(name, "") for name in _SIGNED_HEADERS]
    values.append(str(unix_minute))
    return "|".join(values).encode()


def compute_geo_signature(key: str, request: Request, unix_minute: int) -> str:
    """The ``v1=`` signature an edge holding ``key`` would stamp.

    Exposed so tests (and an edge implementation checking itself) share
    one definition of the canonical string with the verifier.
    """
    digest = hmac.new(
        key.encode(), _canonical_message(request, unix_minute), hashlib.sha256
    ).hexdigest()
    return f"{_SIGNATURE_VERSION_PREFIX}{digest}"


def verify_geo_signature(request: Request, now: datetime) -> bool:
    """Whether the request's geo headers carry a valid edge signature.

    ``now`` is the request's single evaluation instant (the same clock
    every other context/risk read uses), so signature freshness cannot
    drift from the rest of the policy evaluation.

    Fail-closed by construction:

    - not configured → False (callers only consult this when
      ``edge_hmac_configured()`` — the return value then means "geo
      headers must not be honoured");
    - missing / malformed / wrong-version signature → False;
    - signature outside the skew window → False (a captured signature
      goes stale within minutes; replaying it later proves nothing);
    - empty configured keys never match.

    Every candidate (minute bucket x key slot) is compared in constant
    time and ALL candidates are always evaluated, so timing reveals
    neither which key slot matched nor which minute bucket did.
    """
    if not edge_hmac_configured():
        return False
    supplied = request.headers.get(_SIGNATURE_HEADER, "")
    if not supplied.startswith(_SIGNATURE_VERSION_PREFIX):
        return False

    # Minute buckets covered by the skew window, current minute included.
    # skew=0 accepts only the current minute.
    skew_minutes = max(0, -(-settings.ABAC_EDGE_HMAC_SKEW_SECONDS // 60))
    current_minute = int(now.timestamp()) // 60
    minutes = range(current_minute - skew_minutes, current_minute + skew_minutes + 1)

    matched = False
    for key in (settings.ABAC_EDGE_HMAC_KEY, settings.ABAC_EDGE_HMAC_KEY_NEXT):
        if not key:
            continue
        for unix_minute in minutes:
            expected = compute_geo_signature(key, request, unix_minute)
            if hmac.compare_digest(supplied, expected):
                matched = True
    return matched
