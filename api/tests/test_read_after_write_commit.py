"""Regression tests for QA finding 04 — the systemic read-after-write race.

POST /deals returned 201 while its INSERT was still uncommitted: services
only flush, and the commit lived in ``get_db()``'s post-``yield`` teardown,
which runs after the response has entered the middleware stack. An
immediate GET of the returned id could 404 (~7% reproduced live), and every
write endpoint shared that session pattern.

The fix is ``CommitOnSuccessRoute`` (``app/common/routing.py``): commit the
request-scoped session after the endpoint runs but strictly BEFORE the
response is sent. These tests pin:

- structurally, that EVERY mounted API route uses the route class (a new
  router without ``route_class=CommitOnSuccessRoute`` fails here);
- behaviourally, that the commit happens before ``http.response.start`` is
  emitted (observed at the outermost ASGI layer, i.e. where the client
  sits);
- that the exception path never commits and still rolls back;
- end to end on the real app: create a deal through the REAL ``get_db``
  (fresh session per request), commit observed before the response starts,
  and the deal immediately readable by the next request's fresh session.
"""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.common.database as database_module
from app.common.dependencies import get_current_user
from app.common.routing import CommitOnSuccessRoute
from app.constants.enums import CustomerType, UserRole
from app.main import app as main_app
from app.modules.auth.models import User
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import CustomerService
from tests.conftest import TestingSessionLocal

# ---------------------------------------------------------------------------
# Shared probes
# ---------------------------------------------------------------------------


class _ResponseStartProbe:
    """Outermost ASGI wrapper recording when the response starts leaving.

    Sits OUTSIDE the whole middleware stack — the client's side of the
    wire — so asserting "commit before response_start" asserts exactly the
    durability contract the client relies on.
    """

    def __init__(self, app, events: list) -> None:
        self.app = app
        self.events = events

    async def __call__(self, scope, receive, send):
        async def probing_send(message):
            if message["type"] == "http.response.start":
                self.events.append("response_start")
            await send(message)

        await self.app(scope, receive, probing_send)


class _SpySession:
    def __init__(self, events: list) -> None:
        self._events = events

    def commit(self) -> None:
        self._events.append("commit")

    def rollback(self) -> None:
        self._events.append("rollback")

    def close(self) -> None:
        self._events.append("close")


def _build_spy_app(events: list) -> FastAPI:
    """Minimal app mirroring the production wiring (get_db-shaped dep)."""
    router = APIRouter(route_class=CommitOnSuccessRoute)

    def spy_get_db(request: Request):
        db = _SpySession(events)
        request.state.db = db
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    @router.post("/things", status_code=201)
    def create_thing(db=Depends(spy_get_db)):
        events.append("handler")
        return {"ok": True}

    @router.post("/broken", status_code=201)
    def broken_thing(db=Depends(spy_get_db)):
        events.append("handler")
        raise HTTPException(status_code=400, detail="nope")

    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Structural: the fix is systemic, not deals-specific
# ---------------------------------------------------------------------------


@pytest.mark.no_db
def test_every_mounted_route_uses_commit_on_success_route():
    """Every API route (all 16 routers) must commit before responding."""
    api_routes = [r for r in main_app.routes if isinstance(r, APIRoute)]
    assert len(api_routes) > 50  # sanity: the whole surface is mounted

    offenders = [
        route.path
        for route in api_routes
        if not isinstance(route, CommitOnSuccessRoute)
    ]
    assert offenders == [], (
        "These routes were mounted without route_class=CommitOnSuccessRoute "
        f"and can race read-after-write: {offenders}"
    )


# ---------------------------------------------------------------------------
# Behaviour: commit strictly before the response starts; never on exception
# ---------------------------------------------------------------------------


@pytest.mark.no_db
def test_commit_happens_before_response_start():
    events: list = []
    client = TestClient(_ResponseStartProbe(_build_spy_app(events), events))

    response = client.post("/things")

    assert response.status_code == 201
    assert "commit" in events and "response_start" in events
    assert events.index("commit") < events.index("response_start"), (
        f"session committed after the response started leaving: {events}"
    )
    assert events.index("handler") < events.index("commit")


@pytest.mark.no_db
def test_exception_path_never_commits_and_rolls_back():
    events: list = []
    client = TestClient(_ResponseStartProbe(_build_spy_app(events), events))

    response = client.post("/broken")

    assert response.status_code == 400
    assert "commit" not in events, f"partial writes were committed: {events}"
    assert "rollback" in events


@pytest.mark.no_db
def test_get_db_publishes_session_on_request_state(monkeypatch):
    """The route class discovers the session via request.state.db."""
    monkeypatch.setattr(database_module, "SessionLocal", TestingSessionLocal)
    request = SimpleNamespace(state=SimpleNamespace())

    generator = database_module.get_db(request)
    session = next(generator)
    try:
        assert request.state.db is session
    finally:
        generator.close()


# ---------------------------------------------------------------------------
# End to end on the real app through the REAL get_db
# ---------------------------------------------------------------------------


def _seed_owner_and_customer():
    """Create an owner + customer (both billing profiles) and COMMIT them."""
    session = TestingSessionLocal()
    try:
        owner = User(
            email=f"{uuid4().hex[:10]}@sales.test",
            password_hash="x",
            first_name="Tabitha",
            last_name="Kimani",
            role=UserRole.MEMBER,
        )
        session.add(owner)
        session.flush()
        customer = CustomerService(session).create(
            CustomerCreate(
                customer_type=CustomerType.BUSINESS,
                company_name="Baraka Logistics",
                first_name="Alice",
                last_name="Wanjiru",
                email=f"{uuid4().hex[:10]}@baraka.co.ke",
                phone="+254720114220",
                country="KE",
            )
        )
        session.commit()
        return owner.id, customer.id
    finally:
        session.close()


def test_create_deal_commits_before_response_and_is_immediately_readable(
    monkeypatch,
):
    """QA finding 04 repro path: POST /deals then GET the id right away.

    Runs the real app with the REAL ``get_db`` (one fresh session per
    request, no shared-session test override), so the GET only succeeds if
    the POST's transaction was actually committed — and the probe pins that
    the commit happened before the 201 started leaving the server.
    """
    owner_id, customer_id = _seed_owner_and_customer()

    events: list = []

    def recording_session_factory():
        session = TestingSessionLocal()
        original_commit = session.commit

        def commit():
            original_commit()
            events.append("commit")

        session.commit = commit
        return session

    # Point the real get_db at the test database, one fresh session per call.
    monkeypatch.setattr(database_module, "SessionLocal", recording_session_factory)

    actor = SimpleNamespace(id=owner_id, role=UserRole.ADMIN)
    main_app.dependency_overrides[get_current_user] = lambda: actor
    try:
        with TestClient(_ResponseStartProbe(main_app, events)) as client:
            created = client.post(
                "/api/v1/deals",
                json={
                    "customerId": str(customer_id),
                    "ownerId": str(owner_id),
                    "product": "Microsoft 365 Business Premium",
                    "seats": 45,
                    "value": str(Decimal("11880.00")),
                    "currency": "USD",
                    "note": "Read-after-write regression check.",
                },
            )
            assert created.status_code == 201, created.text
            deal_id = created.json()["id"]

            assert "commit" in events and "response_start" in events
            assert events.index("commit") < events.index("response_start"), (
                "the POST response started leaving before its transaction "
                f"was committed: {events}"
            )

            # The immediate follow-up read uses a brand-new session: it can
            # only see the deal if the write is durably committed.
            fetched = client.get(f"/api/v1/deals/{deal_id}")
            assert fetched.status_code == 200, fetched.text
            assert fetched.json()["id"] == deal_id
    finally:
        main_app.dependency_overrides.pop(get_current_user, None)
