import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import sessionmaker

from app.common.database import Base, get_db
from app.main import app

# Prefer the real PostgreSQL database (provided by the CI service via
# DATABASE_URL) so Postgres-only constructs - gen_random_uuid(),
# partial/GIN indexes, with_for_update(), pg_advisory_xact_lock,
# case/extract/cast - are actually exercised. Fall back to in-memory
# SQLite for local pure-logic runs when no database is configured.
DATABASE_URL = os.getenv("DATABASE_URL")
USING_POSTGRES = bool(DATABASE_URL) and DATABASE_URL.startswith("postgresql")

if USING_POSTGRES:
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
