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
) -> AccessContext:
    """Build a context by hand so every attribute is test-controlled."""
    return AccessContext(
        principal=principal,  # type: ignore[arg-type]
        user_id=uuid.uuid4() if principal == "user" else None,
        session_id=None,
        ip=ip,
        ip_denylisted=ip_denylisted,
        geo=geo,
        device_fingerprint="derived:abc123",
        requested_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
        local_hour=local_hour,
        method=method,
        path=path,
        sensitivity=sensitivity if sensitivity is not None else classify_path(path),
    )


@pytest.fixture
def off_hours_window(monkeypatch):
    """Enable the 22h → 6h off-hours window (conftest disables it suite-wide)."""
    monkeypatch.setattr(settings, "ABAC_OFF_HOURS_START", 22)
    monkeypatch.setattr(settings, "ABAC_OFF_HOURS_END", 6)


class TestSensitivityClassification:
    """The classification the issue names, pinned per path."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            # Probes: exempt from evaluation entirely.
            ("/api/v1/health", SensitivityLevel.PUBLIC),
            ("/api/v1/health/detailed", SensitivityLevel.PUBLIC),
            ("/api/v1/ping", SensitivityLevel.PUBLIC),
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
