"""Data-sensitivity classification of API resources (issue #67).

Every request is classified BEFORE the route handler runs so the ABAC
policy engine can weigh *what* is being touched, not just *who* is
touching it. Classification is by route path (the only request attribute
that exists for every endpoint, including 404s and validation failures),
using ordered longest-match-first rules.

Levels, lowest to highest:

- PUBLIC       — unauthenticated probes (health, ping). Exempt from policy
                 evaluation entirely so load-balancer checks can never be
                 denied, challenged, or audited into noise.
- INTERNAL     — ordinary business data (customers, deals, quotes, ...).
- CONFIDENTIAL — financial documents and reporting (invoices, expenses,
                 purchase orders, statements, reports).
- RESTRICTED   — money movement (payment recording / mark-as-paid), the
                 owner and platform administration surfaces (ADR-0011),
                 and the audit trail itself.

The mapping is intentionally a single ordered table in one module: adding
a router without thinking about its sensitivity means it defaults to
INTERNAL, and the classification tests pin the levels the issue names
(invoices, payments, owner/platform, audit trail).
"""

from enum import StrEnum

from app.lib.config import settings


class SensitivityLevel(StrEnum):
    """How damaging unauthorized access to a resource would be."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def rank(self) -> int:
        """Numeric ordering (higher = more sensitive) for threshold rules."""
        return _RANKS[self]

    def at_least(self, other: "SensitivityLevel") -> bool:
        """Whether this level is at least as sensitive as ``other``."""
        return self.rank >= other.rank


_RANKS: dict[SensitivityLevel, int] = {
    SensitivityLevel.PUBLIC: 0,
    SensitivityLevel.INTERNAL: 1,
    SensitivityLevel.CONFIDENTIAL: 2,
    SensitivityLevel.RESTRICTED: 3,
}


#: Path *segment* markers that escalate a financial document route to
#: RESTRICTED: recording money against a document is more sensitive than
#: reading or editing the document itself.
_RESTRICTED_SEGMENTS: tuple[str, ...] = (
    "/payments",
    "/mark-paid",
)

#: Exact-path rules evaluated BEFORE the prefix table. PUBLIC probes match
#: exactly, never by prefix: a "/health" prefix rule classified everything
#: beneath it — including the internal-secret-gated ``/health/detailed``,
#: which returns DB/pool internals — as PUBLIC, exempting it from policy
#: evaluation (IP/geo DENY rules) and decision auditing entirely (#67
#: review F6). Anything new under /health now defaults to INTERNAL, so an
#: added sub-route is policy-covered from its first request.
_EXACT_RULES: dict[str, SensitivityLevel] = {
    # Probes — exempt from evaluation (see module docstring).
    "/health": SensitivityLevel.PUBLIC,
    "/ping": SensitivityLevel.PUBLIC,
    # Infrastructure internals (database connectivity, pool statistics):
    # as sensitive as the platform administration surface.
    # verify_internal_secret still owns authentication; ABAC DENY rules
    # and decision auditing apply on top, and machine callers presenting
    # the verified secret are exempt from OTP-challenge rules (H1).
    "/health/detailed": SensitivityLevel.RESTRICTED,
}

#: Ordered (prefix, level) rules, matched against the path with the API
#: prefix stripped. First match wins; more specific prefixes must come
#: before shorter ones. No PUBLIC entry belongs here — a PUBLIC prefix
#: would exempt every sub-path from evaluation (see _EXACT_RULES).
_PREFIX_RULES: tuple[tuple[str, SensitivityLevel], ...] = (
    # Owner + platform administration (ADR-0011) and the audit trail.
    ("/owner", SensitivityLevel.RESTRICTED),
    ("/platform", SensitivityLevel.RESTRICTED),
    ("/audit", SensitivityLevel.RESTRICTED),
    # ABAC / session-risk ops surface (issue #85): session termination,
    # baseline trust expiry and the risk-event listing are incident-
    # response tooling — as sensitive as the platform console it will
    # share the #73 operator step-up gate with once both land.
    ("/admin", SensitivityLevel.RESTRICTED),
    # Financial documents and reporting.
    ("/invoices", SensitivityLevel.CONFIDENTIAL),
    ("/expenses", SensitivityLevel.CONFIDENTIAL),
    ("/purchase-orders", SensitivityLevel.CONFIDENTIAL),
    ("/statements", SensitivityLevel.CONFIDENTIAL),
    ("/reports", SensitivityLevel.CONFIDENTIAL),
)


def _strip_api_prefix(path: str) -> str:
    prefix = settings.API_V1_PREFIX
    if prefix and path.startswith(prefix):
        return path[len(prefix) :] or "/"
    return path


def classify_path(path: str) -> SensitivityLevel:
    """Classify one request path into a :class:`SensitivityLevel`.

    ``path`` is the raw request path (API prefix included or not). Unknown
    paths default to INTERNAL — never PUBLIC — so a new router is policy-
    covered from its first request.
    """
    relative = _strip_api_prefix(path)

    exact = _EXACT_RULES.get(relative)
    if exact is not None:
        return exact

    for prefix, level in _PREFIX_RULES:
        if relative == prefix or relative.startswith(prefix + "/"):
            # Payment recording under a financial document escalates.
            if level is SensitivityLevel.CONFIDENTIAL and any(
                marker in relative for marker in _RESTRICTED_SEGMENTS
            ):
                return SensitivityLevel.RESTRICTED
            return level

    return SensitivityLevel.INTERNAL
