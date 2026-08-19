"""Edge authentication for trust-context headers (issue #83).

Covers the three layers the issue names:

- **Edge HMAC**: with ``ABAC_EDGE_HMAC_KEY`` configured, the ``X-Geo-*``
  headers are honoured ONLY with a valid, fresh ``X-Geo-Signature`` —
  spoofed geo from a non-edge source is ignored (fail-safe: it degrades
  to "no geo signal", never a lockout), and key rotation via ``_NEXT``
  works with zero downtime.
- **Edge CIDR allowlist**: with ``ABAC_EDGE_CIDRS`` configured, ALL
  context headers are honoured only when the DIRECT peer is a known
  edge.
- **Corroborating-only client fingerprints**: a self-attested
  ``client:`` fingerprint match can no longer suppress the new-device
  signal on its own — the server-derived fingerprint must corroborate —
  and a passed step-up absorbs BOTH forms.
"""

import hashlib
import hmac as hmac_mod
from datetime import timedelta

import pytest
from fastapi import Request

from app.common.audit import AuditEvent
from app.common.authz import baselines, edge
from app.common.authz.context import _device_fingerprints
from app.common.security import create_access_token, hash_password
from app.constants.enums import SessionStatus, UserRole
from app.lib.config import Settings, settings
from app.modules.auth.models import User, UserBaseline, UserSession
from tests.conftest import ABAC_EVALUATION_TIME

EDGE_KEY = "edge-test-key-0000000000000000000000000000"
NEXT_KEY = "edge-test-key-next-00000000000000000000000"

KE_GEO = {"X-Geo-Country": "KE", "X-Geo-Lat": "-1.286", "X-Geo-Lon": "36.817"}

#: The derived fingerprint every TestClient request carries (User-Agent
#: "testclient", no Accept-Language) — mirrors context._derived_fingerprint.
TESTCLIENT_DERIVED_FP = "derived:" + hashlib.sha256(b"testclient\n").hexdigest()[:32]

#: The unix minute of the suite's pinned evaluation instant: the gate
#: verifies signatures against the frozen clock, not the wall clock.
EVAL_MINUTE = int(ABAC_EVALUATION_TIME.timestamp()) // 60


def _sign(
    key: str,
    country: str = "",
    lat: str = "",
    lon: str = "",
    minute: int | None = None,
) -> str:
    """Independent re-implementation of the edge's signature.

    Deliberately NOT ``edge.compute_geo_signature``: computing the
    canonical string here pins its exact layout
    (``country|lat|lon|unix_minute``, raw values, missing as empty)
    against drift in the module under test.
    """
    minute = EVAL_MINUTE if minute is None else minute
    message = f"{country}|{lat}|{lon}|{minute}".encode()
    return "v1=" + hmac_mod.new(key.encode(), message, hashlib.sha256).hexdigest()


def _signed_geo(key: str, minute: int | None = None) -> dict:
    return {
        **KE_GEO,
        "X-Geo-Signature": _sign(
            key,
            country=KE_GEO["X-Geo-Country"],
            lat=KE_GEO["X-Geo-Lat"],
            lon=KE_GEO["X-Geo-Lon"],
            minute=minute,
        ),
    }


def _request(headers: dict | None = None, client_host: str = "203.0.113.9") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/customers",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
        "client": (client_host, 40000),
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


@pytest.fixture
def trusted_ctx(monkeypatch):
    monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)


@pytest.fixture
def hmac_key(monkeypatch):
    monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY", EDGE_KEY)


def _seed_user(db, email: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("Str0ng!pass"),
        first_name="Edge",
        last_name="Tester",
        role=UserRole.MEMBER.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _craft_session(db, user: User) -> tuple[UserSession, dict]:
    """A live session + bearer headers minted WITHOUT the OTP flow."""
    session = UserSession(user_id=user.id, status=SessionStatus.ACTIVE.value)
    db.add(session)
    db.commit()
    token = create_access_token(
        subject=str(user.id),
        extra={
            "sid": str(session.id),
            "sua": int(ABAC_EVALUATION_TIME.timestamp()),
        },
    )
    return session, {"Authorization": f"Bearer {token}"}


class TestConfigDefaults:
    """Every new knob pinned at its shipped default."""

    pytestmark = pytest.mark.no_db

    def test_defaults(self):
        fields = Settings.model_fields
        assert fields["ABAC_EDGE_HMAC_KEY"].default == ""
        assert fields["ABAC_EDGE_HMAC_KEY_NEXT"].default == ""
        assert fields["ABAC_EDGE_HMAC_SKEW_SECONDS"].default == 120
        assert fields["ABAC_EDGE_CIDRS"].default == ""


class TestSignatureVerification:
    pytestmark = pytest.mark.no_db

    def test_valid_signature_verifies(self, hmac_key):
        request = _request(_signed_geo(EDGE_KEY))
        assert edge.verify_geo_signature(request, ABAC_EVALUATION_TIME) is True

    def test_missing_or_garbage_signature_fails(self, hmac_key):
        assert not edge.verify_geo_signature(_request(KE_GEO), ABAC_EVALUATION_TIME)
        assert not edge.verify_geo_signature(
            _request({**KE_GEO, "X-Geo-Signature": "nonsense"}),
            ABAC_EVALUATION_TIME,
        )
        assert not edge.verify_geo_signature(
            _request({**KE_GEO, "X-Geo-Signature": "v2=" + "0" * 64}),
            ABAC_EVALUATION_TIME,
        )

    def test_tampered_values_fail(self, hmac_key):
        headers = _signed_geo(EDGE_KEY)
        headers["X-Geo-Country"] = "US"  # signature covers "KE"
        assert not edge.verify_geo_signature(_request(headers), ABAC_EVALUATION_TIME)

    def test_wrong_key_fails(self, hmac_key):
        request = _request(_signed_geo("some-other-key-000000000000000000000000"))
        assert not edge.verify_geo_signature(request, ABAC_EVALUATION_TIME)

    def test_skew_window_bounds_freshness(self, hmac_key, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_SKEW_SECONDS", 120)
        fresh = _request(_signed_geo(EDGE_KEY, minute=EVAL_MINUTE - 2))
        assert edge.verify_geo_signature(fresh, ABAC_EVALUATION_TIME) is True
        stale = _request(_signed_geo(EDGE_KEY, minute=EVAL_MINUTE - 3))
        assert not edge.verify_geo_signature(stale, ABAC_EVALUATION_TIME)
        # A captured signature is useless minutes later: replay at +3h.
        later = ABAC_EVALUATION_TIME + timedelta(hours=3)
        assert not edge.verify_geo_signature(_request(_signed_geo(EDGE_KEY)), later), (
            "a replayed signature must go stale"
        )

    def test_zero_skew_accepts_only_current_minute(self, hmac_key, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_SKEW_SECONDS", 0)
        assert edge.verify_geo_signature(
            _request(_signed_geo(EDGE_KEY)), ABAC_EVALUATION_TIME
        )
        assert not edge.verify_geo_signature(
            _request(_signed_geo(EDGE_KEY, minute=EVAL_MINUTE - 1)),
            ABAC_EVALUATION_TIME,
        )

    def test_next_key_rotation_overlap(self, monkeypatch):
        """Zero-downtime rotation: both slots verify during the overlap."""
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY", EDGE_KEY)
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY_NEXT", NEXT_KEY)
        assert edge.verify_geo_signature(
            _request(_signed_geo(EDGE_KEY)), ABAC_EVALUATION_TIME
        )
        assert edge.verify_geo_signature(
            _request(_signed_geo(NEXT_KEY)), ABAC_EVALUATION_TIME
        )
        # After promotion the old key stops verifying.
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY", NEXT_KEY)
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY_NEXT", "")
        assert not edge.verify_geo_signature(
            _request(_signed_geo(EDGE_KEY)), ABAC_EVALUATION_TIME
        )
        assert edge.verify_geo_signature(
            _request(_signed_geo(NEXT_KEY)), ABAC_EVALUATION_TIME
        )

    def test_unconfigured_keys_never_verify(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY", "")
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY_NEXT", "")
        # An empty key must never match — even a signature computed WITH
        # the empty key (fail closed, mirroring matches_internal_secret).
        assert not edge.verify_geo_signature(
            _request(_signed_geo("")), ABAC_EVALUATION_TIME
        )


class TestEdgeCidrs:
    pytestmark = pytest.mark.no_db

    def test_peer_matching(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_EDGE_CIDRS", "10.0.0.0/8, testclient")
        assert edge.peer_is_edge(_request(client_host="10.4.5.6"))
        assert edge.peer_is_edge(_request(client_host="testclient"))
        assert not edge.peer_is_edge(_request(client_host="203.0.113.9"))
        assert not edge.peer_is_edge(_request(client_host="otherclient"))

    def test_unconfigured_cidrs_do_not_gate(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_EDGE_CIDRS", "")
        assert edge.peer_is_edge(_request(client_host="203.0.113.9"))


class TestTrustModes:
    pytestmark = pytest.mark.no_db

    @pytest.mark.parametrize(
        ("trust", "key", "cidrs", "expected"),
        [
            (False, "", "", "disabled"),
            (False, EDGE_KEY, "10.0.0.0/8", "disabled"),
            (True, "", "", "unauthenticated"),
            (True, EDGE_KEY, "", "hmac"),
            (True, "", "10.0.0.0/8", "cidr"),
            (True, EDGE_KEY, "10.0.0.0/8", "hmac+cidr"),
        ],
    )
    def test_edge_trust_mode(self, monkeypatch, trust, key, cidrs, expected):
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", trust)
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY", key)
        monkeypatch.setattr(settings, "ABAC_EDGE_CIDRS", cidrs)
        assert edge.edge_trust_mode() == expected

    def test_next_key_alone_counts_as_hmac(self, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY", "")
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY_NEXT", NEXT_KEY)
        monkeypatch.setattr(settings, "ABAC_EDGE_CIDRS", "")
        assert edge.edge_trust_mode() == "hmac"

    def test_unauthenticated_mode_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY", "")
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY_NEXT", "")
        monkeypatch.setattr(settings, "ABAC_EDGE_CIDRS", "")
        with caplog.at_level("WARNING", logger="app.common.authz.edge"):
            edge.log_edge_trust_mode()
        assert any(
            "ABAC_EDGE_UNAUTHENTICATED" in record.message for record in caplog.records
        )

    def test_authenticated_mode_does_not_warn(self, monkeypatch, caplog):
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY", EDGE_KEY)
        with caplog.at_level("WARNING", logger="app.common.authz.edge"):
            edge.log_edge_trust_mode()
        assert not caplog.records


class TestGeoHeadersRequireEdgeAuth:
    """Acceptance: spoofed geo/device headers from a non-edge source are
    ignored. Observed through the geo blocklist — a DENY rule that fires
    only when the geo signal is actually honoured. An unauthenticated
    request is used on purpose: the gate runs before authentication, so
    403 means "geo was honoured (blocklisted)" and 401 means "geo was
    dropped and auth refused as usual"."""

    @pytest.fixture(autouse=True)
    def _blocklist(self, monkeypatch, trusted_ctx):
        monkeypatch.setattr(settings, "ABAC_GEO_BLOCKLIST", "KE")

    def test_unsigned_geo_is_ignored_when_hmac_configured(self, client, hmac_key):
        spoofed = client.get("/api/v1/customers", headers=KE_GEO)
        assert spoofed.status_code == 401, (
            "unsigned geo from a non-edge source must be dropped, not honoured"
        )

    def test_garbage_signature_is_ignored(self, client, hmac_key):
        resp = client.get(
            "/api/v1/customers",
            headers={**KE_GEO, "X-Geo-Signature": "v1=" + "0" * 64},
        )
        assert resp.status_code == 401

    def test_signed_geo_is_honoured(self, client, hmac_key):
        resp = client.get("/api/v1/customers", headers=_signed_geo(EDGE_KEY))
        assert resp.status_code == 403, "a validly signed geo context is honoured"
        assert resp.json()["error_code"] == "ForbiddenException"

    def test_next_key_signed_geo_is_honoured(self, client, hmac_key, monkeypatch):
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY_NEXT", NEXT_KEY)
        resp = client.get("/api/v1/customers", headers=_signed_geo(NEXT_KEY))
        assert resp.status_code == 403

    def test_without_edge_auth_behaviour_is_unchanged(self, client, monkeypatch):
        """Empty edge knobs preserve the pre-#83 (unauthenticated) mode."""
        monkeypatch.setattr(settings, "ABAC_EDGE_HMAC_KEY", "")
        monkeypatch.setattr(settings, "ABAC_EDGE_CIDRS", "")
        resp = client.get("/api/v1/customers", headers=KE_GEO)
        assert resp.status_code == 403

    def test_cidr_gates_geo_headers(self, client, monkeypatch):
        # TestClient's direct peer is "testclient": off-range peer first.
        monkeypatch.setattr(settings, "ABAC_EDGE_CIDRS", "10.0.0.0/8")
        assert client.get("/api/v1/customers", headers=KE_GEO).status_code == 401
        # The edge itself (allowlisted peer) is honoured.
        monkeypatch.setattr(settings, "ABAC_EDGE_CIDRS", "10.0.0.0/8, testclient")
        assert client.get("/api/v1/customers", headers=KE_GEO).status_code == 403

    def test_hmac_and_cidr_compose(self, client, hmac_key, monkeypatch):
        """Defence in depth: BOTH layers must pass when both are set."""
        monkeypatch.setattr(settings, "ABAC_EDGE_CIDRS", "10.0.0.0/8")
        signed = _signed_geo(EDGE_KEY)
        assert client.get("/api/v1/customers", headers=signed).status_code == 401, (
            "a valid signature from an off-range peer must still be dropped"
        )
        monkeypatch.setattr(settings, "ABAC_EDGE_CIDRS", "testclient")
        assert client.get("/api/v1/customers", headers=signed).status_code == 403


class TestClientFingerprintCorroboratingOnly:
    """A ``client:`` fingerprint may fire signals but never raise trust
    on its own (issue #83): suppressing the new-device signal requires
    the server-derived fingerprint to corroborate."""

    def _seed_baseline(self, db, user, devices: tuple) -> UserBaseline:
        baseline = UserBaseline(
            user_id=user.id,
            known_devices=list(devices),
            known_countries=["KE"],
            hour_counts={str(h): 10 for h in range(24)},
            hour_observations=240,
            volume_baselines={},
        )
        db.add(baseline)
        db.commit()
        return baseline

    def test_uncorroborated_client_match_fires_new_device(
        self, client, db, trusted_ctx
    ):
        """Replaying the victim's fingerprint no longer silences the signal."""
        user = _seed_user(db, "victim@mail.com")
        # Only the self-attested form is known — the presenting browser's
        # DERIVED fingerprint has never passed a step-up.
        self._seed_baseline(db, user, devices=("client:known-laptop",))

        session, headers = _craft_session(db, user)
        resp = client.get(
            "/api/v1/customers",
            headers={**headers, "X-Device-Fingerprint": "known-laptop"},
        )
        assert resp.status_code == 200, "one soft signal still allows (+ logs)"
        db.refresh(session)
        assert session.risk_score == settings.RISK_SCORE_NEW_DEVICE

        events = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "session",
                AuditEvent.action == "soft_signal_new_device",
            )
            .all()
        )
        assert events
        assert events[-1].after["reason"] == "uncorroborated_client_match"
        assert events[-1].after["derived_fingerprint"] == TESTCLIENT_DERIVED_FP

    def test_corroborated_client_match_suppresses(self, client, db, trusted_ctx):
        """Both forms known (a passed step-up absorbed them): no signal."""
        user = _seed_user(db, "genuine@mail.com")
        self._seed_baseline(
            db, user, devices=("client:known-laptop", TESTCLIENT_DERIVED_FP)
        )

        session, headers = _craft_session(db, user)
        resp = client.get(
            "/api/v1/customers",
            headers={**headers, "X-Device-Fingerprint": "known-laptop"},
        )
        assert resp.status_code == 200
        db.refresh(session)
        assert session.risk_score == 0

    def test_derived_mode_fingerprints_unaffected(self, client, db, trusted_ctx):
        """No client header: primary == derived, no corroboration step."""
        user = _seed_user(db, "plain@mail.com")
        self._seed_baseline(db, user, devices=(TESTCLIENT_DERIVED_FP,))

        session, headers = _craft_session(db, user)
        resp = client.get("/api/v1/customers", headers=headers)
        assert resp.status_code == 200
        db.refresh(session)
        assert session.risk_score == 0

    def test_absorption_stores_both_fingerprint_forms(self, db, trusted_ctx):
        """A passed step-up absorbs the client AND derived forms."""
        from app.common.authz.context import build_access_context

        user = _seed_user(db, "absorbed@mail.com")
        request = _request(
            {
                "X-Device-Fingerprint": "known-laptop",
                "User-Agent": "testclient",
            }
        )
        context = build_access_context(request)
        assert context.device_fingerprint == "client:known-laptop"
        assert context.derived_device_fingerprint == TESTCLIENT_DERIVED_FP

        baselines.absorb_context(db, user.id, context)
        db.commit()
        baseline = db.get(UserBaseline, user.id)
        values = baselines.entry_values(baseline.known_devices)
        assert "client:known-laptop" in values
        assert TESTCLIENT_DERIVED_FP in values

    def test_client_header_needs_edge_peer_when_cidrs_configured(self, monkeypatch):
        """Off-range peers get no say in their own device identity."""
        monkeypatch.setattr(settings, "ABAC_TRUST_CONTEXT_HEADERS", True)
        monkeypatch.setattr(settings, "ABAC_EDGE_CIDRS", "10.0.0.0/8")
        request = _request(
            {"X-Device-Fingerprint": "spoofed", "User-Agent": "testclient"},
            client_host="203.0.113.9",
        )
        primary, derived = _device_fingerprints(request)
        assert primary == derived == TESTCLIENT_DERIVED_FP

        onrange = _request(
            {"X-Device-Fingerprint": "spoofed", "User-Agent": "testclient"},
            client_host="10.1.2.3",
        )
        primary, derived = _device_fingerprints(onrange)
        assert primary == "client:spoofed"
        assert derived == TESTCLIENT_DERIVED_FP
