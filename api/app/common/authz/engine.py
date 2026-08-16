"""ABAC policy engine: context in, allow / deny / challenge out (#67).

Evaluates one :class:`~app.common.authz.context.AccessContext` against an
ordered list of pure rules. The first rule with an opinion wins; when no
rule objects the decision is ALLOW. Rules only ever RESTRICT — the engine
can never grant access that the existing RBAC gates (``require_role`` /
``require_privileged``, ADR-0011 platform isolation) would refuse: it runs
*before* them and its ALLOW simply means "no contextual objection".

Decisions:

- ALLOW      — proceed to authentication / RBAC as before.
- CHALLENGE  — the caller must step up (re-run the login → OTP flow) before
               this action is allowed; surfaced as 401 ``STEP_UP_REQUIRED``.
               Every rule that can return CHALLENGE MUST also honour the
               ``sua`` step-up claim, or the challenge is unanswerable and
               the 401 becomes a lockout: a new token pair changes neither
               the wall clock nor the requested path.
- DENY       — contextual refusal (e.g. denylisted IP); surfaced as 403.
- TERMINATE  — the session is dead (risk threshold crossed); surfaced as
               401 and the session can never be used again.

Every rule is a pure function of the context so the authorization matrix
(same role + same permission, different context, different outcome) is
directly unit-testable.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

from app.common.authz.context import AccessContext
from app.common.authz.sensitivity import SensitivityLevel
from app.lib.config import settings


class Decision(StrEnum):
    """Outcome of a policy evaluation."""

    ALLOW = "allow"
    DENY = "deny"
    CHALLENGE = "challenge"
    TERMINATE = "terminate"


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    """One policy decision with its provenance (for auditing)."""

    decision: Decision
    rule: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


ALLOW_VERDICT = PolicyVerdict(
    decision=Decision.ALLOW, rule="default", reason="No contextual objection"
)


def _off_hours_window() -> tuple[int, int] | None:
    """The configured off-hours window, or None when disabled.

    ``ABAC_OFF_HOURS_START == ABAC_OFF_HOURS_END`` means "no window" (the
    test suite pins this to 0/0 so business tests are wall-clock
    independent; deployments default to 22 → 6 local time).
    """
    start = settings.ABAC_OFF_HOURS_START
    end = settings.ABAC_OFF_HOURS_END
    if start == end:
        return None
    return start, end


def is_off_hours(local_hour: int) -> bool:
    """Whether a local hour falls inside the configured off-hours window."""
    window = _off_hours_window()
    if window is None:
        return False
    start, end = window
    if start < end:
        return start <= local_hour < end
    return local_hour >= start or local_hour < end


# --- Rules (ordered; each returns a verdict or None = no opinion) ----------


def _rule_ip_reputation(context: AccessContext) -> PolicyVerdict | None:
    """A denylisted source IP is refused outright, whoever it claims to be."""
    if context.ip_denylisted:
        return PolicyVerdict(
            decision=Decision.DENY,
            rule="ip_reputation",
            reason=f"Source IP {context.ip} is denylisted",
        )
    return None


@lru_cache(maxsize=8)
def _parse_geo_blocklist(raw: str) -> frozenset[str]:
    """Parse ``ABAC_GEO_BLOCKLIST`` into upper-case ISO country codes.

    Cached on the raw string, matching ``_parse_denylist``: this runs on
    every request, and a settings change (tests monkeypatch it) invalidates
    the entry naturally.
    """
    return frozenset(code.strip().upper() for code in raw.split(",") if code.strip())


def _rule_geo_blocklist(context: AccessContext) -> PolicyVerdict | None:
    """Requests geolocated to a blocked country are refused."""
    blocklist = _parse_geo_blocklist(settings.ABAC_GEO_BLOCKLIST)
    if not blocklist:
        return None
    if context.geo and context.geo.country and context.geo.country in blocklist:
        return PolicyVerdict(
            decision=Decision.DENY,
            rule="geo_blocklist",
            reason=f"Request geolocated to blocked country {context.geo.country}",
        )
    return None


def _rule_off_hours(context: AccessContext) -> PolicyVerdict | None:
    """Sensitive access in the middle of the (tenant-local) night steps up.

    Same role, same permission: a RESTRICTED touch (any method) or a
    CONFIDENTIAL write inside the off-hours window requires a fresh OTP
    round-trip. Reads of CONFIDENTIAL data stay allowed so night-shift
    lookups do not lock anyone out of read paths.

    A caller who has already completed that round trip within
    ``ABAC_STEP_UP_TTL_MINUTES`` passes: the challenge has been *answered*.
    Without this, the rule reads only the wall clock and the path, so no
    amount of re-authenticating could ever clear it — the 401 would be a
    hard lockout for the whole window, not a step-up. Someone working a
    late shift proves who they are once and then works; someone holding a
    stolen token has no inbox and so can never produce the claim at all.
    """
    if not is_off_hours(context.local_hour):
        return None
    if context.principal == "service":
        # A machine-to-machine caller (X-Internal-Secret) has no inbox, so
        # an OTP challenge is unanswerable — for it, CHALLENGE is a
        # permanent lockout, and the nightly scheduled jobs (cron 02:00,
        # inside the default 22→6 window) hit CONFIDENTIAL-write internal
        # endpoints every night. Its trust model is the constant-time
        # secret comparison (verify_internal_secret still runs and still
        # refuses a wrong secret) plus the process boundary. This cannot
        # be abused to dodge a step-up: an authenticated user stays
        # principal "user" whatever headers they add (user_id wins in
        # build_access_context), and without a bearer token the RBAC
        # gates refuse business routes regardless. DENY rules (IP
        # reputation, geo blocklist) still apply to service callers.
        return None
    if context.stepped_up_within(settings.ABAC_STEP_UP_TTL_MINUTES):
        return None
    if context.sensitivity is SensitivityLevel.RESTRICTED or (
        context.sensitivity is SensitivityLevel.CONFIDENTIAL and context.is_write
    ):
        return PolicyVerdict(
            decision=Decision.CHALLENGE,
            rule="off_hours",
            reason=(
                f"{context.sensitivity.value} access at "
                f"{context.local_hour:02d}h local time requires step-up"
            ),
        )
    return None


def _rule_unknown_geo_restricted_write(
    context: AccessContext,
) -> PolicyVerdict | None:
    """RESTRICTED writes with no geolocation signal step up.

    Only applies when the deployment HAS a geo signal
    (``ABAC_TRUST_CONTEXT_HEADERS``): a missing location then means the
    request bypassed the trusted edge, which is exactly the stolen-
    credential shape the issue describes. Without a configured signal the
    rule stays silent — absence of infrastructure is not an anomaly.
    """
    if settings.ABAC_TRUST_CONTEXT_HEADERS is not True:
        return None
    if context.principal == "service":
        # Same reasoning as the off-hours rule: a machine cannot answer an
        # OTP challenge, and a scheduler's server-to-server call never
        # passes the geo-stamping edge anyway. verify_internal_secret owns
        # its trust; DENY rules still apply.
        return None
    if context.stepped_up_within(settings.ABAC_STEP_UP_TTL_MINUTES):
        # Same reasoning as the off-hours rule: a fresh OTP is the answer to
        # this challenge, so honour it rather than looping the caller.
        return None
    if (
        context.sensitivity is SensitivityLevel.RESTRICTED
        and context.is_write
        and (context.geo is None or context.geo.country is None)
    ):
        return PolicyVerdict(
            decision=Decision.CHALLENGE,
            rule="unknown_geo",
            reason="Restricted write without a geolocation signal requires step-up",
        )
    return None


_RULES: tuple[Callable[[AccessContext], PolicyVerdict | None], ...] = (
    _rule_ip_reputation,
    _rule_geo_blocklist,
    _rule_off_hours,
    _rule_unknown_geo_restricted_write,
)


def evaluate(context: AccessContext) -> PolicyVerdict:
    """Run the ordered rules; first opinion wins, default ALLOW."""
    for rule in _RULES:
        verdict = rule(context)
        if verdict is not None:
            return verdict
    return ALLOW_VERDICT
