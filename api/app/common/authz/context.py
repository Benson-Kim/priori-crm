"""Per-request access-context assembly for the ABAC policy engine (#67).

Builds one immutable :class:`AccessContext` per request from the signals
the issue names: time of day, geolocation, device fingerprint, IP
reputation and the data-sensitivity level of the target resource. The
context is assembled BEFORE the route handler runs and handed to the
policy engine (``app.common.authz.engine``), so the same role + permission
can yield different outcomes in different contexts.

Signal sources are deliberately pluggable-but-simple:

- **IP** — the socket peer, or the first X-Forwarded-For hop when the
  deployment explicitly trusts its proxy (same trust switch semantics as
  the rate limiter's ``RATE_LIMIT_TRUST_FORWARDED_FOR``).
- **Geolocation** — supplied by a trusted edge (CDN / reverse proxy) via
  ``X-Geo-Country`` / ``X-Geo-Lat`` / ``X-Geo-Lon`` headers, honoured only
  when ``ABAC_TRUST_CONTEXT_HEADERS`` is enabled. There is no outbound
  geo-IP lookup: the API makes no network calls on the request path.
- **Device fingerprint** — a client-supplied ``X-Device-Fingerprint``
  (trusted-header mode), else derived server-side from stable client
  headers so every request carries *some* device signal.
- **IP reputation** — a config-driven denylist (``ABAC_IP_DENYLIST``:
  exact IPs, CIDR ranges, or literal identifiers), evaluated locally.
- **Time** — the request instant converted to the organisation's
  ``REPORTING_TIMEZONE`` so "middle of the night" means the tenant's
  night, not UTC's.
"""

import hashlib
import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Request
from jose import JWTError, jwt

from app.common.authz.sensitivity import SensitivityLevel, classify_path
from app.lib.config import settings

#: HTTP methods that mutate state.
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

PrincipalType = Literal["user", "service", "anonymous"]


def _now() -> datetime:
    """Current instant (UTC). Module-level so simulations can freeze it."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """A resolved request geolocation (any field may be missing)."""

    country: str | None = None
    lat: float | None = None
    lon: float | None = None

    @property
    def has_coordinates(self) -> bool:
        return self.lat is not None and self.lon is not None


@dataclass(frozen=True, slots=True)
class AccessContext:
    """Everything the policy engine may weigh for one request."""

    principal: PrincipalType
    user_id: uuid.UUID | None
    session_id: uuid.UUID | None
    ip: str
    ip_denylisted: bool
    geo: GeoPoint | None
    device_fingerprint: str | None
    requested_at: datetime
    local_hour: int
    method: str
    path: str
    sensitivity: SensitivityLevel

    @property
    def is_write(self) -> bool:
        """Whether the request mutates state."""
        return self.method.upper() in _WRITE_METHODS


@lru_cache(maxsize=8)
def _parse_denylist(
    raw: str,
) -> tuple[frozenset[str], tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]]:
    """Parse ``ABAC_IP_DENYLIST`` into exact entries and CIDR networks.

    Cached on the raw string so a settings change (tests monkeypatch it)
    invalidates naturally. Entries that parse as an IP/CIDR become network
    matches; anything else is an exact-string match (useful for
    non-IP client identifiers such as test transports).
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


def is_ip_denylisted(ip: str) -> bool:
    """Whether an IP (or literal client identifier) has bad reputation."""
    exact, networks = _parse_denylist(settings.ABAC_IP_DENYLIST)
    if ip in exact:
        return True
    if networks:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(address in network for network in networks)
    return False


def _client_ip(request: Request) -> str:
    """Resolve the client IP, honouring a trusted proxy's X-Forwarded-For."""
    if settings.RATE_LIMIT_TRUST_FORWARDED_FOR is True:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if isinstance(forwarded, str) and forwarded:
            first_hop = forwarded.split(",")[0].strip()
            if first_hop:
                return first_hop
    return request.client.host if request.client else "unknown"


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _resolve_geo(request: Request) -> GeoPoint | None:
    """Geolocation from trusted edge headers; None when there is no signal."""
    if settings.ABAC_TRUST_CONTEXT_HEADERS is not True:
        return None
    country = request.headers.get("X-Geo-Country") or None
    lat = _parse_float(request.headers.get("X-Geo-Lat"))
    lon = _parse_float(request.headers.get("X-Geo-Lon"))
    if country is None and lat is None and lon is None:
        return None
    return GeoPoint(country=country.upper() if country else None, lat=lat, lon=lon)


def _device_fingerprint(request: Request) -> str | None:
    """Client-supplied fingerprint, else one derived from stable headers.

    The derived form hashes User-Agent + Accept-Language: coarse, but it
    changes when the presenting device/browser changes, which is the signal
    session risk scoring needs. Prefixes distinguish provenance so a
    client-supplied value is never confused with a derived one.
    """
    if settings.ABAC_TRUST_CONTEXT_HEADERS is True:
        supplied = request.headers.get("X-Device-Fingerprint")
        if supplied:
            return f"client:{supplied.strip()[:128]}"
    user_agent = request.headers.get("User-Agent", "")
    accept_language = request.headers.get("Accept-Language", "")
    if not user_agent and not accept_language:
        return None
    digest = hashlib.sha256(f"{user_agent}\n{accept_language}".encode()).hexdigest()
    return f"derived:{digest[:32]}"


def _decode_token_leniently(request: Request) -> dict | None:
    """Best-effort decode of the bearer token for context identity.

    Authentication is NOT decided here: a missing/invalid token simply
    yields an anonymous context, and ``get_current_user`` still owns the
    401 on protected routes. The signature IS verified, so a forged token
    cannot plant a fake identity in the context or the audit trail.
    """
    authorization = request.headers.get("Authorization", "")
    if not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError:
        return None
    if payload.get("type") != "access":
        return None
    return payload


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def build_access_context(request: Request) -> AccessContext:
    """Assemble the per-request :class:`AccessContext`."""
    token = _decode_token_leniently(request)
    user_id = _parse_uuid(token.get("sub")) if token else None
    session_id = _parse_uuid(token.get("sid")) if token else None

    principal: PrincipalType
    if user_id is not None:
        principal = "user"
    elif request.headers.get("X-Internal-Secret"):
        # Machine-to-machine caller; verify_internal_secret still owns the
        # constant-time secret comparison on the routes that require it.
        principal = "service"
    else:
        principal = "anonymous"

    ip = _client_ip(request)
    requested_at = _now()
    local_hour = requested_at.astimezone(ZoneInfo(settings.REPORTING_TIMEZONE)).hour

    return AccessContext(
        principal=principal,
        user_id=user_id,
        session_id=session_id,
        ip=ip,
        ip_denylisted=is_ip_denylisted(ip),
        geo=_resolve_geo(request),
        device_fingerprint=_device_fingerprint(request),
        requested_at=requested_at,
        local_hour=local_hour,
        method=request.method,
        path=request.url.path,
        sensitivity=classify_path(request.url.path),
    )
