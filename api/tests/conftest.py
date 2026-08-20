import os

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

# PostgreSQL is REQUIRED for every schema-building test (CI provides it via
# DATABASE_URL). The schema and several code paths are Postgres-only -
# gen_random_uuid(), char_length() CHECK constraints, partial/GIN indexes,
# with_for_update(), pg_advisory_xact_lock - so ``Base.metadata.create_all``
# cannot run on SQLite (first offender: sqlite3.OperationalError: no such
# function: char_length). Without a Postgres DATABASE_URL only the pure-logic
# tests marked ``no_db`` run; everything else is skipped at collection time
# (see ``pytest_collection_modifyitems`` below). The in-memory SQLite engine
# below exists solely so the session/app fixtures still construct for those
# ``no_db`` legs - it is never asked to build the schema.
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
            "to run only the no_db pure-logic tests."
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


_REQUIRES_POSTGRES_SKIP = pytest.mark.skip(
    reason=(
        "requires PostgreSQL: the schema uses Postgres-only constructs "
        "(char_length CHECK constraints, gen_random_uuid, partial/GIN "
        "indexes) that SQLite cannot build. Point DATABASE_URL at a "
        "postgresql:// test database (name must contain 'test', e.g. "
        "prioritech_test); only tests marked no_db run without one."
    )
)


def pytest_collection_modifyitems(config, items):
    """Skip every schema-building test when no Postgres database is configured.

    ``setup_db`` builds the full schema for any test not marked ``no_db``,
    and ``Base.metadata.create_all`` fails on SQLite (see the engine comment
    above), which used to surface as an error wall (802 setup errors) instead
    of a useful run. Skipping at collection keeps the no-Postgres outcome to
    passes + skips only: the ``no_db`` pure-logic legs still run and report.
    """
    if USING_POSTGRES:
        return
    for item in items:
        if item.get_closest_marker("no_db") is None:
            item.add_marker(_REQUIRES_POSTGRES_SKIP)


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
