import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.common.database import Base, get_db
from app.lib.config import settings
from app.main import app

# The whole suite drives the shared `app` through one unauthenticated
# TestClient, so every request lands in a single rate-limit bucket
# ("testclient"). With the production limiter active (60 req/min) the suite
# trips 429s once it makes >60 requests inside a sliding minute — a
# timing-dependent flake, not a product signal. Rate-limit behaviour has its
# own dedicated coverage (test_rate_limit.py builds an isolated app and
# re-enables the limiter via monkeypatch; test_rate_limiter.py mocks
# settings), so it stays fully tested. The middleware reads this setting on
# every dispatch, so flipping it here is sufficient.
settings.RATE_LIMIT_ENABLED = False

# The ABAC off-hours window (#67) stays LIVE for the whole suite, at its
# production 22h -> 6h setting.
#
# It was previously pinned to 0/0 (disabled) here because a nightly CI run
# saw step-up challenges. That was a symptom, not a test problem: the rule
# read only the wall clock and the path, so its challenge could not be
# answered by authenticating — it was an unconditional lockout for the
# window, and disabling it in CI hid exactly the defect worth catching.
#
# Now that a completed OTP stamps the `sua` claim and the rule honours it,
# a normally-authenticated caller passes at any hour. `auth_headers` below
# mints that claim by default, so business tests are time-of-day
# independent *because the product is*, not because the rule is off.
settings.ABAC_OFF_HOURS_START = 22
settings.ABAC_OFF_HOURS_END = 6

# Prefer the real PostgreSQL database (provided by the CI service via
# DATABASE_URL) so Postgres-only constructs - gen_random_uuid(),
# partial/GIN indexes, with_for_update(), pg_advisory_xact_lock,
# case/extract/cast - are actually exercised. Fall back to in-memory
# SQLite for local pure-logic runs when no database is configured.
DATABASE_URL = os.getenv("DATABASE_URL")
USING_POSTGRES = bool(DATABASE_URL) and DATABASE_URL.startswith("postgresql")


def _require_test_database(url: str) -> None:
    """Fail closed before the suite can drop_all a non-test database.

    ``setup_db`` runs ``Base.metadata.drop_all`` against whatever
    ``DATABASE_URL`` points at. Running the suite with a dev or prod URL
    exported would silently wipe that database, so we refuse to build an
    engine at all unless the database *name* clearly marks it as a test
    database (CI uses ``prioritech_test``).
    """
    db_name = make_url(url).database or ""
    if "test" not in db_name.lower():
        raise RuntimeError(
            "Refusing to run the test suite against DATABASE_URL database "
            f"{db_name!r}: the suite drops and recreates every table, and the "
            "database name does not contain 'test'. Point DATABASE_URL at a "
            "dedicated test database (e.g. 'prioritech_test'), or unset it "
            "to run against in-memory SQLite."
        )


if USING_POSTGRES:
    _require_test_database(DATABASE_URL)
    engine = create_engine(DATABASE_URL, future=True)
else:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_db: test is a pure in-memory check and must skip schema building",
    )


@pytest.fixture(autouse=True)
def setup_db(request):
    """Provide each test a clean schema.

    Drop first so a schema left behind by a previously aborted run cannot
    cause cross-test contamination or partial-state failures; then create,
    then drop on teardown.

    Tests marked ``no_db`` are pure in-memory checks (e.g. schema-metadata
    invariants) and skip schema building entirely, so they still run and
    report even when create_all itself would fail.
    """
    if request.node.get_closest_marker("no_db"):
        yield
        return
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def reset_auth_throttle():
    """Reset the process-level auth throttle store between tests.

    Both the throttle counter and the refresh-token denylist are lru_cache
    singletons shared across the whole process. Without clearing them, attempt
    counts and revoked jtis would leak between tests that reuse the same email
    or token and could trip limits / reject tokens spuriously.
    """
    from app.modules.auth.service import (
        _auth_throttle_store,
        _refresh_token_denylist,
    )

    _auth_throttle_store.cache_clear()
    _refresh_token_denylist.cache_clear()
    yield
    _auth_throttle_store.cache_clear()
    _refresh_token_denylist.cache_clear()


@pytest.fixture(autouse=True)
def mute_new_device_alerts():
    """Mute the new-device absorption alert (#67 H13) suite-wide.

    Every FIRST login in a test absorbs a never-seen (derived) device
    fingerprint, which triggers the owner-notification email from deep
    inside verify_otp. The OTP mail is explicitly patched per test; this
    one would otherwise reach the real SES client (ENVIRONMENT here is
    "test", not "development", so the dev-mode skip does not apply) and
    stall the suite on credential resolution. Tests asserting the alert
    patch AuthService._send_new_device_alert explicitly.
    """
    with patch(
        "app.lib.email.EmailService.send_new_device_alert",
        return_value={"MessageId": "muted-in-tests"},
    ):
        yield


@pytest.fixture(autouse=True)
def reset_volume_store():
    """Reset the session data-access volume counter between tests.

    It is an lru_cache'd process-wide store (Redis in production, in-memory
    here), so without clearing it one test's request burst would count
    toward another's window.
    """
    from app.common.authz.risk import _volume_store

    _volume_store.cache_clear()
    yield
    _volume_store.cache_clear()


def auth_headers(
    user,
    *,
    stepped_up: bool = True,
    stepped_up_at: datetime | None = None,
    session_id=None,
) -> dict:
    """Bearer headers for ``user``, stepped-up by default.

    The default models a NORMALLY authenticated caller: real users reach the
    API by completing login → OTP, which stamps `sua`. Minting tokens
    without it would make every test look like a stale session and put the
    whole suite back at the mercy of the wall clock.

    ``stepped_up=False`` models a stale or pre-`sua` token — the shape the
    off-hours and unknown-geo challenges exist to catch.

    ``stepped_up_at`` pins the claim to an explicit instant. Tests that
    freeze the gate's clock MUST pass it: otherwise the claim is stamped at
    real wall-clock time while the request is evaluated at the frozen one,
    and whether the token reads as fresh depends on which way the two
    happen to drift — a coin flip, not a test.
    """
    from app.common.security import create_access_token

    claims: dict = {}
    if session_id is not None:
        claims["sid"] = str(session_id)
    if stepped_up_at is not None:
        claims["sua"] = int(stepped_up_at.timestamp())
    elif stepped_up:
        claims["sua"] = int(datetime.now(UTC).timestamp())
    return {
        "Authorization": (
            f"Bearer {create_access_token(subject=str(user.id), extra=claims or None)}"
        )
    }


@pytest.fixture
def db():
    """Provide a test database session."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    """Provide a test HTTP client with overridden DB dependency."""

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
