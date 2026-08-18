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
- **Step-up freshness** — the signed ``sua`` claim recording when the
  caller last completed an OTP. This is what makes a contextual CHALLENGE
  *satisfiable*: rules over wall-clock time alone cannot be cleared by
  re-authenticating, because a new token pair does not move the clock.
"""

import hashlib
import ipaddress
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Request
from jose import JWTError, jwt

from app.common.authz.sensitivity import SensitivityLevel, classify_path
from app.common.security import matches_internal_secret
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
    stepped_up_at: datetime | None = None

    @property
    def is_write(self) -> bool:
        """Whether the request mutates state."""
        return self.method.upper() in _WRITE_METHODS

    def stepped_up_within(self, minutes: int) -> bool:
        """Whether a fresh OTP step-up backs this request.

        Reads the signed ``sua`` ("stepped-up-at") claim that ``verify_otp``
        stamps on both tokens, so a contextual CHALLENGE is *satisfiable*:
        completing the login → OTP round trip changes an input the rules
        actually read. Without it, a rule over wall-clock time alone can
        never be cleared by re-authenticating, and its 401 is a lockout
        wearing a challenge's clothing.

        A missing claim (tokens minted before this existed) reads as "not
        stepped up" — fail closed. Those sessions are challenged once and
        heal on the next OTP.
        """
        if self.stepped_up_at is None:
            return False
        age = (self.requested_at - self.stepped_up_at).total_seconds()
        # A future-dated claim means a clock skew or a forged-but-signed
        # token; neither earns clearance.
        return 0 <= age <= minutes * 60


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


#: Version numbers inside a User-Agent, e.g. "Chrome/127.0.6533.89".
_UA_VERSION = re.compile(r"/\d+(?:\.\d+)*")


def _stable_user_agent(user_agent: str) -> str:
    """Strip patch versions from a User-Agent, keeping the major.

    Browsers auto-update constantly. Hashing the raw UA makes every silent
    update look like a brand-new device (+RISK_SCORE_DEVICE_CHANGE) for a
    user who did nothing — the single most common false positive in this
    detector. Keeping the major version preserves the signal that actually
    matters (a different browser, engine or OS entirely) while ignoring the
    churn that does not.
    """
    return _UA_VERSION.sub(lambda m: "/" + m.group(0)[1:].split(".")[0], user_agent)


def _device_fingerprint(request: Request) -> str | None:
    """Client-supplied fingerprint, else one derived from stable headers.

    The derived form hashes a version-stabilized User-Agent plus the primary
    Accept-Language tag: coarse, but it changes when the presenting device
    or browser genuinely changes, which is the signal session risk scoring
    needs. Prefixes distinguish provenance so a client-supplied value is
    never confused with a derived one.
    """
    if settings.ABAC_TRUST_CONTEXT_HEADERS is True:
        supplied = request.headers.get("X-Device-Fingerprint")
        if supplied:
            return f"client:{supplied.strip()[:128]}"
    user_agent = request.headers.get("User-Agent", "")
    accept_language = request.headers.get("Accept-Language", "")
    if not user_agent and not accept_language:
        return None
    # Only the primary language tag: browsers reorder and re-weight the
    # q-value list as the user browses, which is not a device change.
    primary_language = accept_language.split(",")[0].split(";")[0].strip().lower()
    material = f"{_stable_user_agent(user_agent)}\n{primary_language}"
    digest = hashlib.sha256(material.encode()).hexdigest()
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


def _parse_stepped_up_at(value: object) -> datetime | None:
    """Read the ``sua`` claim (unix seconds) as a UTC instant.

    The claim rides inside the signature-verified payload, so it cannot be
    forged; a malformed value is simply treated as absent (fail closed).
    """
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _is_verified_service_caller(request: Request) -> bool:
    """Whether the caller presents the CORRECT machine-to-machine secret.

    The "service" principal is exempt from the OTP-challenge rules (a
    machine has no inbox — the H1 exemption), so it must never be granted
    on header PRESENCE alone: anyone could attach a garbage
    X-Internal-Secret to dodge the off-hours / unknown-geo challenges
    (review H9). Classification requires the header VALUE to match a
    configured secret in constant time — via the SAME helper
    ``verify_internal_secret`` uses on the gated routes (which still runs
    and still owns its own rejection), so the two checks cannot drift.
    ``INTERNAL_API_SECRET_NEXT`` is honoured during a rotation overlap
    window (#67 line review §1c). A wrong or unconfigured secret
    classifies the caller as anonymous (fail closed).
    """
    return matches_internal_secret(request.headers.get("X-Internal-Secret"))


def build_access_context(request: Request) -> AccessContext:
    """Assemble the per-request :class:`AccessContext`."""
    token = _decode_token_leniently(request)
    user_id = _parse_uuid(token.get("sub")) if token else None
    session_id = _parse_uuid(token.get("sid")) if token else None
    stepped_up_at = _parse_stepped_up_at(token.get("sua")) if token else None

    principal: PrincipalType
    if user_id is not None:
        principal = "user"
    elif _is_verified_service_caller(request):
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
        stepped_up_at=stepped_up_at,
    )
