"""Unit tests for the ABAC policy engine and its inputs (issue #67).

Pure, in-memory checks of:

- data-sensitivity classification of the resources the issue names
  (invoices, payments, owner/platform endpoints, audit trail);
- the IP-reputation denylist parser (exact IPs, CIDR ranges, literal
  client identifiers);
- the off-hours window semantics (wrapping midnight; start == end
  disabled);
- the authorization matrix: the SAME principal with the SAME role and
  permission gets DIFFERENT decisions in DIFFERENT contexts.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.common.authz.context import (
    AccessContext,
    GeoPoint,
    _parse_denylist,
    is_ip_denylisted,
)
from app.common.authz.engine import Decision, evaluate, is_off_hours
from app.common.authz.sensitivity import SensitivityLevel, classify_path
from app.lib.config import settings

pytestmark = pytest.mark.no_db


def _context(
    *,
    method: str = "GET",
    path: str = "/api/v1/invoices",
    sensitivity: SensitivityLevel | None = None,
    local_hour: int = 12,
    ip: str = "203.0.113.10",
    ip_denylisted: bool = False,
    geo: GeoPoint | None = None,
    principal: str = "user",
    session_id: uuid.UUID | None = None,
    stepped_up_at: datetime | None = None,
) -> AccessContext:
    """Build a context by hand so every attribute is test-controlled."""
    return AccessContext(
        principal=principal,  # type: ignore[arg-type]
        user_id=uuid.uuid4() if principal == "user" else None,
        session_id=session_id,
        ip=ip,
        ip_denylisted=ip_denylisted,
        geo=geo,
        device_fingerprint="derived:abc123",
        requested_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
        local_hour=local_hour,
        method=method,
        path=path,
        sensitivity=sensitivity if sensitivity is not None else classify_path(path),
        stepped_up_at=stepped_up_at,
    )


@pytest.fixture
def off_hours_window(monkeypatch):
    """Pin the 22h → 6h off-hours window explicitly (conftest keeps it live)."""
    monkeypatch.setattr(settings, "ABAC_OFF_HOURS_START", 22)
    monkeypatch.setattr(settings, "ABAC_OFF_HOURS_END", 6)


class TestSensitivityClassification:
    """The classification the issue names, pinned per path."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            # Probes: exempt from evaluation entirely — but only the exact
            # probe paths. /health/detailed exposes DB/pool internals and
            # is as sensitive as the platform surface (review F6); an
            # unknown future /health sub-route defaults to INTERNAL.
            ("/api/v1/health", SensitivityLevel.PUBLIC),
            ("/api/v1/health/detailed", SensitivityLevel.RESTRICTED),
            ("/api/v1/health/some-future-probe", SensitivityLevel.INTERNAL),
            ("/api/v1/ping", SensitivityLevel.PUBLIC),
            ("/api/v1/ping/extra", SensitivityLevel.INTERNAL),
            # Financial documents.
            ("/api/v1/invoices", SensitivityLevel.CONFIDENTIAL),
            ("/api/v1/invoices/123", SensitivityLevel.CONFIDENTIAL),
            ("/api/v1/expenses/9/documents", SensitivityLevel.CONFIDENTIAL),
            ("/api/v1/purchase-orders", SensitivityLevel.CONFIDENTIAL),
            ("/api/v1/statements", SensitivityLevel.CONFIDENTIAL),
            ("/api/v1/reports/sales", SensitivityLevel.CONFIDENTIAL),
            # Money movement escalates above the document itself.
            ("/api/v1/invoices/123/payments", SensitivityLevel.RESTRICTED),
            ("/api/v1/purchase-orders/9/payments/7", SensitivityLevel.RESTRICTED),
            ("/api/v1/expenses/5/mark-paid", SensitivityLevel.RESTRICTED),
            # Owner / platform administration (ADR-0011) and audit trail.
            ("/api/v1/owner", SensitivityLevel.RESTRICTED),
            ("/api/v1/owner/modules", SensitivityLevel.RESTRICTED),
            ("/api/v1/platform/owners", SensitivityLevel.RESTRICTED),
            ("/api/v1/audit", SensitivityLevel.RESTRICTED),
            ("/api/v1/audit/events", SensitivityLevel.RESTRICTED),
            # Ordinary business data defaults to INTERNAL.
            ("/api/v1/customers", SensitivityLevel.INTERNAL),
            ("/api/v1/deals/42", SensitivityLevel.INTERNAL),
            ("/api/v1/auth/login", SensitivityLevel.INTERNAL),
            # Unknown paths never default to PUBLIC.
            ("/api/v1/some-new-module", SensitivityLevel.INTERNAL),
        ],
    )
    def test_classification(self, path, expected):
        assert classify_path(path) is expected

    def test_prefix_similarity_does_not_leak_levels(self):
        # "/invoicesx" is not "/invoices"; prefix matching is per-segment.
        assert classify_path("/api/v1/invoicesx") is SensitivityLevel.INTERNAL
        assert classify_path("/api/v1/healthx") is SensitivityLevel.INTERNAL

    def test_ranking_is_ordered(self):
        assert SensitivityLevel.RESTRICTED.at_least(SensitivityLevel.CONFIDENTIAL)
        assert SensitivityLevel.CONFIDENTIAL.at_least(SensitivityLevel.INTERNAL)
        assert not SensitivityLevel.INTERNAL.at_least(SensitivityLevel.CONFIDENTIAL)


class TestIpReputation:
    def test_exact_ip_match(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_IP_DENYLIST", "203.0.113.99")
        assert is_ip_denylisted("203.0.113.99")
        assert not is_ip_denylisted("203.0.113.98")

    def test_cidr_range_match(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_IP_DENYLIST", "10.66.0.0/16")
        assert is_ip_denylisted("10.66.4.2")
        assert not is_ip_denylisted("10.67.4.2")

    def test_literal_identifier_match(self, monkeypatch):
        # Non-IP client identifiers (e.g. test transports) match exactly.
        monkeypatch.setattr(settings, "ABAC_IP_DENYLIST", "badclient")
        assert is_ip_denylisted("badclient")
        assert not is_ip_denylisted("goodclient")

    def test_empty_denylist_matches_nothing(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_IP_DENYLIST", "")
        assert not is_ip_denylisted("203.0.113.99")

    def test_parser_separates_networks_and_literals(self):
        exact, networks = _parse_denylist("10.0.0.0/8, badclient , 192.0.2.7")
        assert "badclient" in exact
        # Single IPs parse as /32 networks.
        assert len(networks) == 2


class TestOffHoursWindow:
    def test_start_equals_end_means_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_OFF_HOURS_START", 0)
        monkeypatch.setattr(settings, "ABAC_OFF_HOURS_END", 0)
        assert not any(is_off_hours(h) for h in range(24))

    def test_window_wraps_midnight(self, off_hours_window):
        assert is_off_hours(23)
        assert is_off_hours(2)
        assert not is_off_hours(12)
        assert not is_off_hours(6)  # end is exclusive
        assert is_off_hours(22)  # start is inclusive

    def test_non_wrapping_window(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_OFF_HOURS_START", 1)
        monkeypatch.setattr(settings, "ABAC_OFF_HOURS_END", 5)
        assert is_off_hours(3)
        assert not is_off_hours(23)


class TestAuthzMatrix:
    """Same role + same permission, different context, different outcome."""

    def test_confidential_write_day_vs_night(self, off_hours_window):
        day = evaluate(_context(method="POST", local_hour=14))
        night = evaluate(_context(method="POST", local_hour=23))
        assert day.decision is Decision.ALLOW
        assert night.decision is Decision.CHALLENGE
        assert night.rule == "off_hours"

    def test_confidential_read_allowed_even_at_night(self, off_hours_window):
        night_read = evaluate(_context(method="GET", local_hour=23))
        assert night_read.decision is Decision.ALLOW

    def test_restricted_read_challenged_at_night(self, off_hours_window):
        path = "/api/v1/platform/owners"
        day = evaluate(_context(method="GET", path=path, local_hour=10))
        night = evaluate(_context(method="GET", path=path, local_hour=1))
        assert day.decision is Decision.ALLOW
        assert night.decision is Decision.CHALLENGE

    def test_internal_write_unaffected_by_off_hours(self, off_hours_window):
        verdict = evaluate(
            _context(method="POST", path="/api/v1/customers", local_hour=23)
        )
        assert verdict.decision is Decision.ALLOW

    def test_denylisted_ip_denied_regardless_of_time_or_path(self):
        verdict = evaluate(_context(ip_denylisted=True, local_hour=12))
        assert verdict.decision is Decision.DENY
        assert verdict.rule == "ip_reputation"

    def test_geo_blocklist_denies(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_GEO_BLOCKLIST", "KP, IR")
        verdict = evaluate(_context(geo=GeoPoint(country="KP")))
        assert verdict.decision is Decision.DENY
        assert verdict.rule == "geo_blocklist"

    def test_geo_blocklist_ignores_other_countries(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_GEO_BLOCKLIST", "KP")
        verdict = evaluate(_context(geo=GeoPoint(country="KE")))
        assert verdict.decision is Decision.ALLOW

    def test_restricted_write_without_geo_signal_challenges(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)
        verdict = evaluate(
            _context(
                method="POST",
                path="/api/v1/invoices/1/payments",
                geo=None,
            )
        )
        assert verdict.decision is Decision.CHALLENGE
        assert verdict.rule == "unknown_geo"

    def test_unknown_geo_silent_without_configured_signal(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", False)
        verdict = evaluate(
            _context(method="POST", path="/api/v1/invoices/1/payments", geo=None)
        )
        assert verdict.decision is Decision.ALLOW

    def test_default_is_allow(self):
        verdict = evaluate(_context())
        assert verdict.decision is Decision.ALLOW
        assert verdict.rule == "default"


class TestServicePrincipal:
    """Machine-to-machine callers can never answer an OTP challenge.

    The nightly scheduler (cron 02:00 — inside the default 22→6 window)
    drives CONFIDENTIAL-write internal endpoints with only the
    X-Internal-Secret header; a CHALLENGE for it is a permanent lockout,
    not a step-up. Its trust model is verify_internal_secret's
    constant-time comparison, which still runs. DENY rules still apply.
    """

    def test_service_writer_not_challenged_off_hours(self, off_hours_window):
        verdict = evaluate(
            _context(
                method="POST",
                path="/api/v1/invoices/internal/transition-overdue",
                local_hour=2,
                principal="service",
            )
        )
        assert verdict.decision is Decision.ALLOW

    def test_service_writer_not_challenged_for_unknown_geo(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)
        verdict = evaluate(
            _context(
                method="POST",
                path="/api/v1/invoices/1/payments",
                geo=None,
                principal="service",
            )
        )
        assert verdict.decision is Decision.ALLOW

    def test_service_caller_still_denied_by_ip_reputation(self, off_hours_window):
        verdict = evaluate(
            _context(ip_denylisted=True, local_hour=2, principal="service")
        )
        assert verdict.decision is Decision.DENY
        assert verdict.rule == "ip_reputation"


class TestAnonymousPrincipal:
    """An anonymous caller can never answer an OTP challenge either.

    A step-up asks the caller to prove control of the ACCOUNT's inbox — an
    unauthenticated request identifies no account, so a CHALLENGE against
    "anonymous" is a lockout wearing a badge, exactly like the service
    case. The engine stays silent and authentication owns the refusal
    (``get_current_user`` / ``verify_internal_secret`` still run and still
    401/403 the request). This is also what made the suite wall-clock
    dependent (pipeline 2767217667): actors installed via
    ``dependency_overrides[get_current_user]`` send no bearer token, so at
    night the off-hours rule challenged them as "anonymous". DENY rules
    still apply to anonymous callers.
    """

    def test_anonymous_not_challenged_off_hours(self, off_hours_window):
        verdict = evaluate(
            _context(
                method="GET",
                path="/api/v1/platform/owners",
                local_hour=23,
                principal="anonymous",
            )
        )
        # Engine-level ALLOW means "no contextual objection" only:
        # authentication still refuses the request downstream.
        assert verdict.decision is Decision.ALLOW

    def test_anonymous_not_challenged_for_unknown_geo(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)
        verdict = evaluate(
            _context(
                method="POST",
                path="/api/v1/invoices/1/payments",
                geo=None,
                principal="anonymous",
            )
        )
        assert verdict.decision is Decision.ALLOW

    def test_anonymous_still_denied_by_ip_reputation(self, off_hours_window):
        verdict = evaluate(
            _context(ip_denylisted=True, local_hour=23, principal="anonymous")
        )
        assert verdict.decision is Decision.DENY
        assert verdict.rule == "ip_reputation"

    def test_anonymous_still_denied_by_geo_blocklist(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_GEO_BLOCKLIST", "KP")
        verdict = evaluate(_context(geo=GeoPoint(country="KP"), principal="anonymous"))
        assert verdict.decision is Decision.DENY
        assert verdict.rule == "geo_blocklist"


class TestGeoBackstopConfidentialReads:
    """Missing-geo backstop for bulk CONFIDENTIAL reads (issue #84).

    Matrix: geo present/absent x read/write x sensitivity, plus the H1
    principal exemptions, the sua satisfiability leg, the volume
    threshold boundary, and the default-threshold composition (§8.1
    style). The volume window is read through
    ``risk.observed_class_volume``; these pure tests pin it via
    monkeypatch — the end-to-end legs live in test_geo_backstop.py.
    """

    RULE = "unknown_geo_confidential_read"

    @pytest.fixture(autouse=True)
    def _configured_signal(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)

    @pytest.fixture
    def bulk_volume(self, monkeypatch):
        """The session's window already holds bulk-read volume."""
        monkeypatch.setattr(
            "app.common.authz.risk.observed_class_volume",
            lambda session_id, sensitivity_class: 10_000,
        )

    @pytest.fixture
    def quiet_volume(self, monkeypatch):
        monkeypatch.setattr(
            "app.common.authz.risk.observed_class_volume",
            lambda session_id, sensitivity_class: 0,
        )

    def _ctx(self, **kwargs):
        kwargs.setdefault("session_id", uuid.uuid4())
        kwargs.setdefault("method", "GET")
        kwargs.setdefault("path", "/api/v1/invoices")
        kwargs.setdefault("geo", None)
        return _context(**kwargs)

    # --- The matrix -----------------------------------------------------

    def test_bulk_confidential_read_without_geo_challenges(self, bulk_volume):
        verdict = evaluate(self._ctx())
        assert verdict.decision is Decision.CHALLENGE
        assert verdict.rule == self.RULE

    def test_restricted_read_is_covered_too(self, bulk_volume):
        """The higher tier must never carry the weaker backstop."""
        verdict = evaluate(self._ctx(path="/api/v1/audit/events"))
        assert verdict.decision is Decision.CHALLENGE
        assert verdict.rule == self.RULE

    def test_geo_present_allows(self, bulk_volume):
        verdict = evaluate(self._ctx(geo=GeoPoint(country="KE")))
        assert verdict.decision is Decision.ALLOW

    def test_internal_read_not_covered(self, bulk_volume):
        verdict = evaluate(self._ctx(path="/api/v1/customers"))
        assert verdict.decision is Decision.ALLOW

    def test_confidential_write_not_covered_by_this_rule(self, bulk_volume):
        # Writes are the existing rules' territory (off-hours,
        # RESTRICTED-write unknown-geo); this rule is reads-only.
        verdict = evaluate(self._ctx(method="POST"))
        assert verdict.decision is Decision.ALLOW

    def test_single_read_does_not_challenge(self, quiet_volume):
        """Acceptance: a single geo-less read never steps up."""
        verdict = evaluate(self._ctx())
        assert verdict.decision is Decision.ALLOW

    # --- Exemptions and satisfiability -----------------------------------

    def test_service_principal_exempt(self, bulk_volume):
        """H1: machine callers have no inbox; a CHALLENGE is a lockout."""
        verdict = evaluate(self._ctx(principal="service"))
        assert verdict.decision is Decision.ALLOW

    def test_anonymous_principal_exempt(self, bulk_volume):
        verdict = evaluate(self._ctx(principal="anonymous"))
        assert verdict.decision is Decision.ALLOW

    def test_fresh_step_up_satisfies(self, bulk_volume):
        verdict = evaluate(
            self._ctx(stepped_up_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC))
        )
        assert verdict.decision is Decision.ALLOW

    def test_stale_step_up_does_not_satisfy(self, bulk_volume, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_STEP_UP_TTL_MINUTES", 30)
        verdict = evaluate(
            self._ctx(stepped_up_at=datetime(2026, 8, 14, 6, 0, tzinfo=UTC))
        )
        assert verdict.decision is Decision.CHALLENGE

    def test_legacy_token_without_session_is_silent(self, bulk_volume):
        verdict = evaluate(self._ctx(session_id=None))
        assert verdict.decision is Decision.ALLOW

    # --- Configuration gates ----------------------------------------------

    def test_silent_when_geo_signal_unconfigured(self, bulk_volume, monkeypatch):
        """Acceptance: with geo unconfigured, behaviour is unchanged."""
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", False)
        verdict = evaluate(self._ctx())
        assert verdict.decision is Decision.ALLOW

    def test_kill_switch_disables_the_backstop(self, bulk_volume, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_GEO_BACKSTOP_CONFIDENTIAL_READS", False)
        verdict = evaluate(self._ctx())
        assert verdict.decision is Decision.ALLOW

    def test_threshold_boundary(self, monkeypatch):
        monkeypatch.setattr(settings, "RISK_VOLUME_MAX_REQUESTS", 100)
        monkeypatch.setattr(settings, "ABAC_GEO_BACKSTOP_VOLUME_PERCENT", 50)
        observed = {"value": 49}
        monkeypatch.setattr(
            "app.common.authz.risk.observed_class_volume",
            lambda session_id, sensitivity_class: observed["value"],
        )
        assert evaluate(self._ctx()).decision is Decision.ALLOW
        observed["value"] = 50
        verdict = evaluate(self._ctx())
        assert verdict.decision is Decision.CHALLENGE

    # --- Default-threshold composition (§8.1 style) -----------------------

    def test_default_threshold_composition(self, monkeypatch):
        """Pins how the DEFAULT knobs compose (#67 line review §8.1 style).

        At the shipped defaults the backstop fires at 150 units/window —
        half the global mild ceiling (300) and a tenth of the
        exfiltration ceiling (1500) — so the geo-less exfiltration shape
        is CHALLENGED long before the volume evidence could terminate,
        and the decision is a CHALLENGE by construction: the
        CGNAT-friendly posture (impossible travel capped at a challenge)
        is not reintroduced through the back door for geo-less traffic.
        """
        assert settings.ABAC_GEO_BACKSTOP_CONFIDENTIAL_READS is True
        assert settings.ABAC_GEO_BACKSTOP_VOLUME_PERCENT == 50
        assert settings.RISK_VOLUME_MAX_REQUESTS == 300
        assert settings.RISK_VOLUME_EXFIL_MULTIPLIER == 5
        assert settings.RISK_IMPOSSIBLE_TRAVEL_MAX_ACTION == "challenge", (
            "the backstop must not disturb the CGNAT-market default"
        )

        threshold = (
            settings.RISK_VOLUME_MAX_REQUESTS
            * settings.ABAC_GEO_BACKSTOP_VOLUME_PERCENT
            // 100
        )
        exfil_ceiling = (
            settings.RISK_VOLUME_MAX_REQUESTS * settings.RISK_VOLUME_EXFIL_MULTIPLIER
        )
        assert threshold == 150
        assert threshold < exfil_ceiling, (
            "the challenge must precede any volume-based termination"
        )

        monkeypatch.setattr(
            "app.common.authz.risk.observed_class_volume",
            lambda session_id, sensitivity_class: threshold,
        )
        verdict = evaluate(self._ctx())
        assert verdict.decision is Decision.CHALLENGE, "never DENY/TERMINATE"
        assert verdict.decision is not Decision.TERMINATE
