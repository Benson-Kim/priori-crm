"""Sales Desk deals module regression tests (issue #39).

Covers:
- create: currency follows the customer's primary_currency billing profile
  (USD|KES, !38's merged enum — never a third currency taxonomy), opening
  stage record, audit row, USD-equivalent value;
- the note gate: advancing/closing/logging/parking without a non-blank note
  is rejected with HTTP 422 ('A note is required to advance or close.');
- stage transition validation: one stage at a time in fixed order, no
  advancing past Negotiation, no advancing/closing/parking closed or
  parked deals;
- derived metrics: age (since first record), idle days (since last record),
  time-in-pipeline (frozen for closed deals) and 'Stage N of 5' progress
  where 5 is the closed end-cap;
- park/resume lifecycle with resume_stage restoration;
- enumerated close reasons (won/lost lists from the prototype) + close note;
- list pagination/search/filters and per-stage / per-rep aggregates;
- optimistic locking on update; privileged-only delete; audit actions.

The Alembic migration itself (upgrade from empty + downgrade/re-upgrade of
e6f7a8b9c0d1) is exercised by the api:test CI job's migration steps, the
same pattern !38 introduced — the suite here builds its schema via
create_all.
"""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.common.audit import AuditEvent
from app.common.dependencies import get_current_user
from app.common.reporting_time import reporting_date
from app.constants.enums import (
    DealLostReason,
    DealStage,
    DealStatus,
    DealWonReason,
    UserRole,
)
from app.main import app
from app.modules.auth.models import User
from app.modules.customers.models import Customer
from app.modules.deals.models import Deal, DealActivity
from app.modules.deals.service import (
    NOTE_REQUIRED_MESSAGE,
    DealService,
    usd_equivalent,
)

API = "/api/v1/deals"


def _user(db, role: UserRole = UserRole.MEMBER, first="Terry", last="Kim") -> User:
    user = User(
        email=f"{uuid4().hex[:10]}@reps.test",
        password_hash="x",
        first_name=first,
        last_name=last,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _customer(db, primary_currency: str | None = "KES") -> Customer:
    customer = Customer(
        customer_type="business",
        company_name=f"Baraka Logistics {uuid4().hex[:6]}",
        first_name="Alice",
        last_name="Wanjiru",
        email=f"{uuid4().hex[:10]}@baraka.co.ke",
        phone="+254720114220",
        primary_currency=primary_currency,
    )
    db.add(customer)
    db.flush()
    return customer


def _install_actor(db, role: UserRole = UserRole.ADMIN) -> User:
    """Authenticate as a REAL user row (activity author_id is an FK)."""
    actor = _user(db, role=role, first="Rep", last="Actor")
    app.dependency_overrides[get_current_user] = lambda: actor
    return actor


@pytest.fixture(autouse=True)
def _cleanup_actor():
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _create_deal(client, customer, owner, **overrides):
    payload = {
        "customerId": str(customer.id),
        "ownerId": str(owner.id),
        "product": "Microsoft 365 E5",
        "seats": 45,
        "value": "12012.00",
    }
    payload.update(overrides)
    response = client.post(API, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _backdate_first_activity(db, deal_id, days: int) -> None:
    """Shift a deal's opening record back in time to age the deal."""
    first = (
        db.query(DealActivity)
        .filter(DealActivity.deal_id == deal_id)
        .order_by(DealActivity.activity_date.asc(), DealActivity.created_at.asc())
        .first()
    )
    first.activity_date = reporting_date() - timedelta(days=days)
    db.flush()


def _audit_actions(db, deal_id) -> list[str]:
    rows = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "deal",
            AuditEvent.entity_id == deal_id,
        )
        .order_by(AuditEvent.created_at.asc())
        .all()
    )
    return [r.action for r in rows]


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------


def test_create_follows_customer_primary_currency(client, db):
    _install_actor(db)
    owner = _user(db)
    customer = _customer(db, primary_currency="KES")

    body = _create_deal(client, customer, owner, value="129500.00")

    assert body["currency"] == "KES"
    # USD equivalent at the fixed 129.50 KES/USD table, exact Decimal math.
    assert Decimal(body["value_usd"]) == Decimal("1000.00")
    assert body["status"] == "open"
    assert body["stage"] == "activation"
    assert body["stage_progress"] == "Stage 1 of 5"
    assert body["age_days"] == 0 and body["idle_days"] == 0
    # Opening stage record was written.
    assert len(body["activities"]) == 1
    assert body["activities"][0]["stage_label"] == "Activation"
    assert body["activities"][0]["note"] == "Deal created"


def test_create_currency_override_and_usd_default(client, db):
    _install_actor(db)
    owner = _user(db)

    no_primary = _customer(db, primary_currency=None)
    body = _create_deal(client, no_primary, owner)
    assert body["currency"] == "USD"
    assert Decimal(body["value_usd"]) == Decimal("12012.00")

    kes_primary = _customer(db, primary_currency="KES")
    body = _create_deal(client, kes_primary, owner, currency="USD")
    assert body["currency"] == "USD"


def test_create_rejects_unknown_customer_and_owner(client, db):
    _install_actor(db)
    owner = _user(db)
    customer = _customer(db)

    missing_customer = client.post(
        API,
        json={
            "customerId": str(uuid4()),
            "ownerId": str(owner.id),
            "product": "Microsoft 365 E3",
            "seats": 10,
            "value": "100.00",
        },
    )
    assert missing_customer.status_code == 404

    missing_owner = client.post(
        API,
        json={
            "customerId": str(customer.id),
            "ownerId": str(uuid4()),
            "product": "Microsoft 365 E3",
            "seats": 10,
            "value": "100.00",
        },
    )
    assert missing_owner.status_code == 404


def test_create_writes_audit_row(client, db):
    _install_actor(db)
    owner = _user(db)
    body = _create_deal(client, _customer(db), owner)
    assert _audit_actions(db, body["id"]) == ["created"]


# --------------------------------------------------------------------------
# The note gate — 422 with the design copy
# --------------------------------------------------------------------------


@pytest.mark.parametrize("note", ["", "   ", "\n\t "])
def test_advance_without_note_is_422(client, db, note):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))

    response = client.post(f"{API}/{deal['id']}/advance", json={"note": note})

    assert response.status_code == 422
    assert response.json()["error"] == NOTE_REQUIRED_MESSAGE
    # And the deal did not move.
    assert client.get(f"{API}/{deal['id']}").json()["stage"] == "activation"


def test_close_without_note_is_422(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))

    response = client.post(
        f"{API}/{deal['id']}/close",
        json={
            "result": "won",
            "reason": DealWonReason.MIGRATION_SUPPORT_OFFER.value,
            "note": "  ",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"] == NOTE_REQUIRED_MESSAGE
    assert client.get(f"{API}/{deal['id']}").json()["status"] == "open"


def test_log_activity_without_note_is_422(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))

    response = client.post(f"{API}/{deal['id']}/activities", json={"note": " "})

    assert response.status_code == 422


def test_park_without_note_is_422(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))
    until = (reporting_date() + timedelta(days=90)).isoformat()

    response = client.post(
        f"{API}/{deal['id']}/park", json={"parkedUntil": until, "note": ""}
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Stage transitions
# --------------------------------------------------------------------------


def test_advance_walks_stages_in_order(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))

    expected = [
        ("qualification", "Qualification", 2),
        ("proposal_quote", "Proposal & Quote", 3),
        ("negotiation", "Negotiation", 4),
    ]
    for stage, label, position in expected:
        response = client.post(
            f"{API}/{deal['id']}/advance", json={"note": f"moved to {stage}"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stage"] == stage
        assert body["stage_label"] == label
        assert body["stage_position"] == position
        assert body["stage_progress"] == f"Stage {position} of 5"
        assert body["activities"][-1]["stage_label"] == label
        assert body["activities"][-1]["note"] == f"moved to {stage}"

    # Negotiation is final: the only way forward is closing.
    blocked = client.post(f"{API}/{deal['id']}/advance", json={"note": "again"})
    assert blocked.status_code == 400
    assert _audit_actions(db, deal["id"]) == [
        "created",
        "stage_advanced",
        "stage_advanced",
        "stage_advanced",
    ]


def test_no_advancing_closed_or_parked_deals(client, db):
    _install_actor(db)
    owner = _user(db)

    closed = _create_deal(client, _customer(db), owner)
    won = client.post(
        f"{API}/{closed['id']}/close",
        json={
            "result": "won",
            "reason": DealWonReason.PRODUCT_FIT.value,
            "note": "signed",
        },
    )
    assert won.status_code == 200
    assert (
        client.post(f"{API}/{closed['id']}/advance", json={"note": "x"}).status_code
        == 400
    )
    assert (
        client.post(f"{API}/{closed['id']}/activities", json={"note": "x"}).status_code
        == 400
    )

    parked = _create_deal(client, _customer(db), owner)
    until = (reporting_date() + timedelta(days=30)).isoformat()
    assert (
        client.post(
            f"{API}/{parked['id']}/park",
            json={"parkedUntil": until, "note": "later"},
        ).status_code
        == 200
    )
    assert (
        client.post(f"{API}/{parked['id']}/advance", json={"note": "x"}).status_code
        == 400
    )
    assert (
        client.post(
            f"{API}/{parked['id']}/close",
            json={
                "result": "lost",
                "reason": DealLostReason.BAD_TIMING.value,
                "note": "x",
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{API}/{parked['id']}/park",
            json={"parkedUntil": until, "note": "again"},
        ).status_code
        == 400
    )


def test_log_activity_keeps_stage(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))

    response = client.post(
        f"{API}/{deal['id']}/activities", json={"note": "Called the CTO"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["stage"] == "activation"
    assert body["activities"][-1]["note"] == "Called the CTO"
    assert body["activities"][-1]["stage_label"] == "Activation"


# --------------------------------------------------------------------------
# Closing
# --------------------------------------------------------------------------


def test_close_won_records_reason_note_and_progress(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))

    response = client.post(
        f"{API}/{deal['id']}/close",
        json={
            "result": "won",
            "reason": "Migration & support offer",
            "note": "Beat the incumbent on onboarding",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "won"
    assert body["close_reason"] == "Migration & support offer"
    assert body["close_note"] == "Beat the incumbent on onboarding"
    assert body["stage_position"] == 5
    assert body["stage_progress"] == "Stage 5 of 5"
    assert body["activities"][-1]["stage_label"] == "Closed — Won"
    assert (
        body["activities"][-1]["note"]
        == "Migration & support offer — Beat the incumbent on onboarding"
    )
    assert _audit_actions(db, deal["id"]) == ["created", "closed_won"]


def test_close_lost_uses_lost_reason_list(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))

    response = client.post(
        f"{API}/{deal['id']}/close",
        json={"result": "lost", "reason": "Price too high", "note": "Budget cut"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "lost"
    assert body["close_reason"] == "Price too high"
    assert body["activities"][-1]["stage_label"] == "Closed — Lost"
    assert _audit_actions(db, deal["id"]) == ["created", "closed_lost"]


def test_close_rejects_reason_from_wrong_list(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))

    # A LOST reason is not a valid WON reason (and vice versa).
    response = client.post(
        f"{API}/{deal['id']}/close",
        json={"result": "won", "reason": "Price too high", "note": "x"},
    )
    assert response.status_code == 422

    response = client.post(
        f"{API}/{deal['id']}/close",
        json={"result": "lost", "reason": "Product fit", "note": "x"},
    )
    assert response.status_code == 422

    # Free-text reasons are rejected outright.
    response = client.post(
        f"{API}/{deal['id']}/close",
        json={"result": "won", "reason": "They liked us", "note": "x"},
    )
    assert response.status_code == 422
    assert client.get(f"{API}/{deal['id']}").json()["status"] == "open"


# --------------------------------------------------------------------------
# Park / resume
# --------------------------------------------------------------------------


def test_park_and_resume_restore_recorded_stage(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))
    assert (
        client.post(
            f"{API}/{deal['id']}/advance", json={"note": "qualified"}
        ).status_code
        == 200
    )

    until = reporting_date() + timedelta(days=90)
    parked = client.post(
        f"{API}/{deal['id']}/park",
        json={"parkedUntil": until.isoformat(), "note": "Renewal is next year"},
    )
    assert parked.status_code == 200
    body = parked.json()
    assert body["status"] == "parked"
    assert body["parked_until"] == until.isoformat()
    assert body["resume_stage"] == "qualification"
    assert body["activities"][-1]["stage_label"] == "Moved to future pipeline"
    assert "Renewal is next year" in body["activities"][-1]["note"]

    resumed = client.post(f"{API}/{deal['id']}/resume")
    assert resumed.status_code == 200
    body = resumed.json()
    assert body["status"] == "open"
    assert body["stage"] == "qualification"
    assert body["parked_until"] is None
    assert body["resume_stage"] is None
    assert body["activities"][-1]["note"] == "Re-engaged from the future pipeline."
    assert _audit_actions(db, deal["id"]) == [
        "created",
        "stage_advanced",
        "parked",
        "resumed",
    ]


def test_resume_requires_parked_status(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))
    assert client.post(f"{API}/{deal['id']}/resume").status_code == 400


def test_park_requires_future_date(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))

    response = client.post(
        f"{API}/{deal['id']}/park",
        json={"parkedUntil": reporting_date().isoformat(), "note": "why"},
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------
# Derived metrics
# --------------------------------------------------------------------------


def test_age_and_idle_days_derive_from_stage_record(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))
    _backdate_first_activity(db, deal["id"], days=10)

    body = client.get(f"{API}/{deal['id']}").json()
    assert body["age_days"] == 10
    assert body["idle_days"] == 10
    assert body["days_in_pipeline"] == 10

    # A fresh activity resets idleness but not the deal's age.
    assert (
        client.post(
            f"{API}/{deal['id']}/activities", json={"note": "pinged them"}
        ).status_code
        == 201
    )
    body = client.get(f"{API}/{deal['id']}").json()
    assert body["age_days"] == 10
    assert body["idle_days"] == 0
    assert body["days_in_pipeline"] == 10


def test_closed_deal_freezes_time_in_pipeline(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))
    _backdate_first_activity(db, deal["id"], days=7)

    assert (
        client.post(
            f"{API}/{deal['id']}/close",
            json={"result": "won", "reason": "Product fit", "note": "done"},
        ).status_code
        == 200
    )

    body = client.get(f"{API}/{deal['id']}").json()
    # First record 7 days ago, close record today: a 7-day total run.
    assert body["days_in_pipeline"] == 7
    assert body["idle_days"] == 0


# --------------------------------------------------------------------------
# List, search, aggregates
# --------------------------------------------------------------------------


def test_list_filters_search_and_pagination(client, db):
    _install_actor(db)
    owner = _user(db)
    acme = _customer(db, primary_currency="USD")
    acme.company_name = "Acme Insurance"
    baraka = _customer(db, primary_currency="KES")
    db.flush()

    open_deal = _create_deal(client, acme, owner, product="Copilot", value="900.00")
    kes_deal = _create_deal(client, baraka, owner, value="129500.00")
    lost = _create_deal(client, acme, owner, product="Teams Phone")
    client.post(
        f"{API}/{lost['id']}/close",
        json={"result": "lost", "reason": "No budget", "note": "frozen"},
    )

    # Status filter
    response = client.get(API, params={"status": "open", "withTotal": True})
    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["total"] == 2
    ids = {item["id"] for item in payload["items"]}
    assert ids == {open_deal["id"], kes_deal["id"]}
    # Open deals expose USD equivalents for aggregation.
    by_id = {item["id"]: item for item in payload["items"]}
    assert Decimal(by_id[kes_deal["id"]]["value_usd"]) == Decimal("1000.00")

    # Search by product and by company name (LIKE-safe helper).
    assert {
        i["id"] for i in client.get(API, params={"search": "Copilot"}).json()["items"]
    } == {open_deal["id"]}
    found = {
        i["id"] for i in client.get(API, params={"search": "Acme"}).json()["items"]
    }
    assert found == {open_deal["id"], lost["id"]}

    # Pagination window: 3 deals, page size 2 -> has_next, then page 2.
    page1 = client.get(API, params={"per_page": 2}).json()
    assert len(page1["items"]) == 2
    assert page1["metadata"]["has_next"] is True
    page2 = client.get(API, params={"per_page": 2, "page": 2}).json()
    assert len(page2["items"]) == 1
    assert page2["metadata"]["has_next"] is False

    # Closed deals sort last; the latest record is surfaced for the list UI.
    everything = client.get(API, params={"per_page": 10}).json()["items"]
    assert everything[-1]["id"] == lost["id"]
    assert everything[-1]["latest_stage_label"] == "Closed — Lost"
    assert everything[-1]["latest_note"] == "No budget — frozen"


def test_aggregates_per_stage_and_per_rep(client, db):
    _install_actor(db)
    rep_a = _user(db, first="Terry", last="Kamau")
    rep_b = _user(db, first="Mary", last="Wafula")
    customer = _customer(db, primary_currency="USD")

    _create_deal(client, customer, rep_a, value="1000.00")
    advanced = _create_deal(client, customer, rep_a, value="500.00")
    client.post(f"{API}/{advanced['id']}/advance", json={"note": "q"})
    won = _create_deal(client, customer, rep_b, value="750.00")
    client.post(
        f"{API}/{won['id']}/close",
        json={"result": "won", "reason": "Product fit", "note": "y"},
    )

    response = client.get(f"{API}/aggregates")
    assert response.status_code == 200
    body = response.json()

    stages = {s["stage"]: s for s in body["stages"]}
    assert set(stages) == {
        "activation",
        "qualification",
        "proposal_quote",
        "negotiation",
    }
    assert stages["activation"]["count"] == 1
    assert Decimal(stages["activation"]["value_usd"]) == Decimal("1000.00")
    assert stages["qualification"]["count"] == 1
    assert stages["proposal_quote"]["count"] == 0

    owners = {o["owner_id"]: o for o in body["owners"]}
    assert owners[str(rep_a.id)]["open_count"] == 2
    assert Decimal(owners[str(rep_a.id)]["open_value_usd"]) == Decimal("1500.00")
    assert owners[str(rep_b.id)]["won_count"] == 1
    assert owners[str(rep_b.id)]["open_count"] == 0


# --------------------------------------------------------------------------
# Update / delete
# --------------------------------------------------------------------------


def test_update_open_deal_with_optimistic_lock(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))

    ok = client.put(
        f"{API}/{deal['id']}",
        params={"expectedVersion": deal["version"]},
        json={"seats": 60, "value": "16016.00"},
    )
    assert ok.status_code == 200
    assert ok.json()["seats"] == 60

    stale = client.put(
        f"{API}/{deal['id']}",
        params={"expectedVersion": deal["version"]},
        json={"seats": 61},
    )
    assert stale.status_code == 409


def test_update_rejected_for_closed_deal(client, db):
    _install_actor(db)
    deal = _create_deal(client, _customer(db), _user(db))
    client.post(
        f"{API}/{deal['id']}/close",
        json={"result": "won", "reason": "Product fit", "note": "z"},
    )

    response = client.put(
        f"{API}/{deal['id']}",
        params={"expectedVersion": 2},
        json={"seats": 99},
    )
    assert response.status_code == 400


def test_delete_is_privileged_and_audited(client, db):
    _install_actor(db, role=UserRole.MEMBER)
    deal = _create_deal(client, _customer(db), _user(db))

    forbidden = client.delete(f"{API}/{deal['id']}")
    assert forbidden.status_code == 403

    _install_actor(db, role=UserRole.ADMIN)
    ok = client.delete(f"{API}/{deal['id']}")
    assert ok.status_code == 200
    assert db.query(Deal).filter(Deal.id == deal["id"]).first() is None
    assert (
        db.query(DealActivity).filter(DealActivity.deal_id == deal["id"]).count() == 0
    )
    assert _audit_actions(db, deal["id"])[-1] == "hard_deleted"
    assert client.get(f"{API}/{deal['id']}").status_code == 404


# --------------------------------------------------------------------------
# Service-level unit checks
# --------------------------------------------------------------------------


def test_usd_equivalent_is_exact_decimal():
    assert usd_equivalent(Decimal("129500.00"), "KES") == Decimal("1000.00")
    assert usd_equivalent(Decimal("12012.00"), "USD") == Decimal("12012.00")
    # Rounded half-up to cents, never floats.
    assert usd_equivalent(Decimal("100.00"), "KES") == Decimal("0.77")


def test_service_note_gate_message_is_design_copy():
    assert NOTE_REQUIRED_MESSAGE == "A note is required to advance or close."


def test_stage_enums_match_prototype():
    assert [s.value for s in DealStage] == [
        "activation",
        "qualification",
        "proposal_quote",
        "negotiation",
    ]
    assert DealStage.PROPOSAL_QUOTE.label == "Proposal & Quote"
    assert [s.value for s in DealStatus] == ["open", "won", "lost", "parked"]
    assert "Migration & support offer" in {r.value for r in DealWonReason}
    assert "Price too high" in {r.value for r in DealLostReason}


def test_service_transition_guards_directly(db):
    """Belt-and-braces: guards hold when called without the HTTP layer."""
    from app.common.exceptions import BadRequestException, ValidationException
    from app.modules.deals.schemas import DealCreate

    actor = _user(db, role=UserRole.ADMIN)
    service = DealService(db, current_user=actor)
    customer = _customer(db)
    deal = service.create(
        DealCreate(
            customer_id=customer.id,
            owner_id=actor.id,
            product="Microsoft 365 E3",
            seats=10,
            value=Decimal("100.00"),
        )
    )

    with pytest.raises(ValidationException):
        service.advance_stage(deal.id, "   ")
    with pytest.raises(ValidationException):
        service.close(deal.id, result="won", reason="Product fit", note=None)

    service.park(
        deal.id,
        parked_until=reporting_date() + timedelta(days=30),
        note="not now",
    )
    with pytest.raises(BadRequestException):
        service.advance_stage(deal.id, "note")
    service.resume(deal.id)
    assert deal.status == DealStatus.OPEN
