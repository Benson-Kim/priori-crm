"""Sales Desk future pipeline module (issue #40).

Covers:
- due_state date-boundary behaviour: overdue (< today) / today (== today)
  / in_days: N (> today) with the chip labels rendered verbatim, all
  against the org-local accounting date boundary (the UI never does date
  math), including a boundary flip driven purely by the reporting date;
- create: engage_on defaults to +90 days server-side, owner validated,
  audit row written;
- list: soonest-due ordering, search and owner filters, screen-contract
  payload (company, contact, note, engage_on, est_arr, owner, due_state,
  dot_state);
- summary: header aggregates (count, total est. ARR, due-now) over the
  same filtered set as the list;
- notification counts (#45): due nurture (engage_on <= today), upcoming
  (within 14 days, exclusive of today) and due parked deals — with both
  window boundaries pinned;
- the transactional engage flow: customer + open deal at Activation +
  prospect removal succeed or fail as one unit (no orphan customer when
  deal creation fails), duplicate-email 409 carrying the existing
  customer's id (re-submit-linking option), the customer_id link path,
  and the owner/value fallbacks.
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.common.audit import AuditEvent
from app.common.dependencies import get_current_user
from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)
from app.common.pagination import PaginationParams
from app.common.reporting_time import reporting_date
from app.constants.enums import (
    BillingCurrency,
    CustomerType,
    DealStage,
    DealStatus,
    UserRole,
)
from app.main import app
from app.modules.auth.models import User
from app.modules.customers.models import Customer
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import CustomerService
from app.modules.deals.schemas import DealCreate
from app.modules.deals.service import DealService
from app.modules.nurture.models import NurtureProspect
from app.modules.nurture.schemas import (
    NurtureEngageRequest,
    NurtureProspectCreate,
)
from app.modules.nurture.service import (
    DEFAULT_ENGAGE_IN_DAYS,
    UPCOMING_WINDOW_DAYS,
    NurtureService,
)

API = "/api/v1/nurture"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _make_owner(db, first="Tabitha", last="Kimani") -> User:
    user = User(
        email=f"{uuid4().hex[:10]}@sales.test",
        password_hash="not-a-real-hash",
        first_name=first,
        last_name=last,
        role=UserRole.MEMBER,
    )
    db.add(user)
    db.flush()
    return user


def _svc(db, user) -> NurtureService:
    return NurtureService(db, current_user=user)


def _make_prospect(db, owner, **overrides) -> NurtureProspect:
    data = dict(
        company="Rift Valley Tea Co.",
        contact="Daniel Kiprop",
        owner_id=owner.id if owner else None,
        engage_on=reporting_date() + timedelta(days=45),
        est_arr=Decimal("21120.00"),
        note="Budget opens new FY. Wants Business Premium for 80 users.",
    )
    data.update(overrides)
    return _svc(db, owner).create(
        NurtureProspectCreate(**data), user_id=owner.id if owner else None
    )


def _customer_payload(email=None, company="Coast Freight Ltd.") -> CustomerCreate:
    return CustomerCreate(
        customer_type=CustomerType.BUSINESS,
        company_name=company,
        first_name="Mary",
        last_name="Achieng",
        email=email or f"{uuid4().hex[:10]}@customer.test",
        phone="+254720114220",
        country="KE",
    )


def _engage_request(**overrides) -> NurtureEngageRequest:
    data = dict(
        customer=_customer_payload(),
        product="Microsoft 365 Business Premium",
        seats=45,
        currency=BillingCurrency.USD,
    )
    data.update(overrides)
    return NurtureEngageRequest(**data)


def _audit_rows(db, entity_id, action):
    return (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "nurture_prospect",
            AuditEvent.entity_id == entity_id,
            AuditEvent.action == action,
        )
        .all()
    )


def _install_actor(user: User):
    actor = SimpleNamespace(id=user.id, role=UserRole.ADMIN)
    app.dependency_overrides[get_current_user] = lambda: actor
    return actor


def _remove_actor():
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def _cleanup_actor():
    yield
    _remove_actor()


# --------------------------------------------------------------------------
# due_state date-boundary behaviour (org-local boundary, UI never does math)
# --------------------------------------------------------------------------


@pytest.mark.no_db
def test_due_state_boundaries():
    today = date(2026, 8, 12)
    overdue = NurtureService.compute_due_state(today - timedelta(days=1), today)
    assert (overdue.state, overdue.in_days) == ("overdue", None)
    assert overdue.label == "Overdue"

    due_today = NurtureService.compute_due_state(today, today)
    assert (due_today.state, due_today.in_days) == ("today", None)
    assert due_today.label == "Today"

    tomorrow = NurtureService.compute_due_state(today + timedelta(days=1), today)
    assert (tomorrow.state, tomorrow.in_days, tomorrow.label) == ("in_days", 1, "1d")

    chip_45 = NurtureService.compute_due_state(today + timedelta(days=45), today)
    assert (chip_45.state, chip_45.in_days, chip_45.label) == ("in_days", 45, "45d")


def test_due_state_flips_with_the_org_local_reporting_date(db, monkeypatch):
    """The same stored engage_on renders as planned/today/overdue purely as

    the org-local reporting date moves across it — proving the badge is
    computed server-side against the accounting boundary, not stored.
    """
    import app.modules.nurture.service as nurture_service_module

    owner = _make_owner(db)
    engage_on = date(2026, 8, 12)
    prospect = _make_prospect(db, owner, engage_on=engage_on)
    svc = _svc(db, owner)

    monkeypatch.setattr(
        nurture_service_module, "reporting_date", lambda: date(2026, 8, 11)
    )
    resp = svc.build_response(prospect)
    assert resp.due_state.state == "in_days"
    assert resp.due_state.in_days == 1
    assert resp.dot_state == "planned"

    monkeypatch.setattr(
        nurture_service_module, "reporting_date", lambda: date(2026, 8, 12)
    )
    resp = svc.build_response(prospect)
    assert resp.due_state.state == "today"
    assert resp.dot_state == "due"

    monkeypatch.setattr(
        nurture_service_module, "reporting_date", lambda: date(2026, 8, 13)
    )
    resp = svc.build_response(prospect)
    assert resp.due_state.state == "overdue"
    assert resp.due_state.label == "Overdue"
    assert resp.dot_state == "due"


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------


def test_create_prospect_persists_fields_and_audit(db):
    owner = _make_owner(db)
    prospect = _make_prospect(db, owner)

    assert prospect.company_name == "Rift Valley Tea Co."
    assert prospect.contact == "Daniel Kiprop"
    assert prospect.est_annual_value == Decimal("21120.00")
    assert prospect.owner_id == owner.id
    assert prospect.created_by == owner.id
    assert len(_audit_rows(db, prospect.id, "created")) == 1


def test_create_prospect_defaults_engage_on_to_plus_90_days(db):
    owner = _make_owner(db)
    prospect = _make_prospect(db, owner, engage_on=None)
    assert prospect.engage_on == reporting_date() + timedelta(
        days=DEFAULT_ENGAGE_IN_DAYS
    )


def test_create_prospect_rejects_unknown_owner(db):
    owner = _make_owner(db)
    with pytest.raises(NotFoundException):
        _svc(db, owner).create(
            NurtureProspectCreate(company="Uhuru Academy", owner_id=uuid4())
        )


def test_create_prospect_endpoint_returns_screen_contract(db, client):
    owner = _make_owner(db)
    _install_actor(owner)

    resp = client.post(
        API,
        json={
            "company": "Lakeside Hospital",
            "contact": "Dr. Rose Adhiambo",
            "ownerId": str(owner.id),
            "engageOn": (reporting_date() + timedelta(days=83)).isoformat(),
            "estArr": "30000.00",
            "note": "New CIO joining in ~3 months. Warm intro promised.",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["company"] == "Lakeside Hospital"
    assert body["contact"] == "Dr. Rose Adhiambo"
    assert Decimal(str(body["est_arr"])) == Decimal("30000.00")
    assert body["engage_on"] == (reporting_date() + timedelta(days=83)).isoformat()
    assert body["owner"]["id"] == str(owner.id)
    assert body["owner"]["initials"] == "TK"
    assert body["due_state"] == {"state": "in_days", "in_days": 83, "label": "83d"}
    assert body["dot_state"] == "planned"

    # Company is required.
    assert client.post(API, json={"contact": "Nobody"}).status_code == 422


# --------------------------------------------------------------------------
# List / summary (screen contract)
# --------------------------------------------------------------------------


def test_list_orders_soonest_due_first_and_filters(db):
    owner_a = _make_owner(db)
    owner_b = _make_owner(db, first="Joy", last="Nduta")
    today = reporting_date()
    in_45 = today + timedelta(days=45)
    in_85 = today + timedelta(days=85)
    _make_prospect(db, owner_a, company="Uhuru Academy", engage_on=in_45)
    _make_prospect(db, owner_b, company="Coast Freight Ltd.", engage_on=today)
    _make_prospect(db, owner_a, company="Lakeside Hospital", engage_on=in_85)

    svc = _svc(db, owner_a)
    page = svc.list_prospects(PaginationParams(page=1, per_page=10))
    assert [i.company for i in page.items] == [
        "Coast Freight Ltd.",
        "Uhuru Academy",
        "Lakeside Hospital",
    ]

    searched = svc.list_prospects(PaginationParams(), search="coast")
    assert [i.company for i in searched.items] == ["Coast Freight Ltd."]

    owned = svc.list_prospects(PaginationParams(), owner_id=owner_b.id)
    assert [i.company for i in owned.items] == ["Coast Freight Ltd."]
    assert owned.items[0].owner.initials == "JN"


def test_summary_counts_and_total_est_arr(db):
    owner = _make_owner(db)
    today = reporting_date()
    rows = [
        ("Rift Valley Tea Co.", today - timedelta(days=2), "21120.00"),
        ("Coast Freight Ltd.", today, "9600.00"),
        ("Uhuru Academy", today + timedelta(days=45), "6300.00"),
        ("Lakeside Hospital", today + timedelta(days=85), "30000.00"),
    ]
    for company, engage_on, est in rows:
        _make_prospect(
            db, owner, company=company, engage_on=engage_on, est_arr=Decimal(est)
        )

    summary = _svc(db, owner).get_summary()
    # "Nurture list — 4 companies · est. $67,020 potential ARR"
    assert summary.count == 4
    assert summary.total_est_arr == Decimal("67020.00")
    assert summary.due_now == 2  # overdue + today

    filtered = _svc(db, owner).get_summary(search="uhuru")
    assert filtered.count == 1
    assert filtered.total_est_arr == Decimal("6300.00")
    assert filtered.due_now == 0


def test_summary_treats_missing_est_arr_as_zero(db):
    owner = _make_owner(db)
    _make_prospect(db, owner, est_arr=None)
    summary = _svc(db, owner).get_summary()
    assert summary.count == 1
    assert summary.total_est_arr == Decimal("0")


# --------------------------------------------------------------------------
# Notification counts (#45): due / upcoming / due parked
# --------------------------------------------------------------------------


def _make_parked_deal(db, owner, parked_until):
    """Create a parked deal and pin parked_until (park() requires future)."""
    customer = CustomerService(db).create(_customer_payload())
    deal = DealService(db, current_user=owner).create(
        DealCreate(
            customer_id=customer.id,
            owner_id=owner.id,
            product="Microsoft 365 E3",
            seats=10,
            value=Decimal("5760.00"),
            currency="USD",
        ),
        user_id=owner.id,
    )
    DealService(db, current_user=owner).park(
        deal.id, reporting_date() + timedelta(days=30), "Wait for budget."
    )
    deal.parked_until = parked_until
    db.flush()
    return deal


def test_notification_counts_pin_both_window_boundaries(db):
    owner = _make_owner(db)
    today = reporting_date()

    _make_prospect(db, owner, engage_on=today - timedelta(days=3))  # due
    _make_prospect(db, owner, engage_on=today)  # due (boundary)
    _make_prospect(db, owner, engage_on=today + timedelta(days=1))  # upcoming
    _make_prospect(
        db, owner, engage_on=today + timedelta(days=UPCOMING_WINDOW_DAYS)
    )  # upcoming (boundary: exactly 14 days out)
    _make_prospect(
        db, owner, engage_on=today + timedelta(days=UPCOMING_WINDOW_DAYS + 1)
    )  # beyond the window

    _make_parked_deal(db, owner, parked_until=today)  # due parked
    _make_parked_deal(db, owner, parked_until=today + timedelta(days=5))  # not due

    counts = _svc(db, owner).get_notification_counts()
    assert counts.due_nurture == 2
    assert counts.upcoming_nurture == 2
    assert counts.due_parked == 1


def test_counts_endpoint(db, client):
    owner = _make_owner(db)
    _install_actor(owner)
    _make_prospect(db, owner, engage_on=reporting_date())

    resp = client.get(f"{API}/counts")
    assert resp.status_code == 200
    assert resp.json() == {
        "due_nurture": 1,
        "upcoming_nurture": 0,
        "due_parked": 0,
    }


# --------------------------------------------------------------------------
# Engage: transactional customer + deal at Activation + prospect removal
# --------------------------------------------------------------------------


def test_engage_creates_customer_and_deal_and_removes_prospect(db):
    owner = _make_owner(db)
    prospect = _make_prospect(db, owner)
    prospect_id = prospect.id
    svc = _svc(db, owner)

    request = _engage_request(value=Decimal("21120.00"))
    deal = svc.engage(prospect_id, request)

    # Deal opens at Activation, owned by the prospect's owner.
    assert deal.stage == DealStage.ACTIVATION
    assert deal.status == DealStatus.OPEN
    assert deal.owner_id == owner.id
    assert deal.value == Decimal("21120.00")

    # Customer created through the customers service: billing profiles exist.
    customer = db.query(Customer).filter(Customer.id == deal.customer_id).one()
    assert {p.currency for p in customer.billing_profiles} == {"USD", "KES"}

    # Prospect is gone; the engage is audited.
    assert db.query(NurtureProspect).filter_by(id=prospect_id).first() is None
    rows = _audit_rows(db, prospect_id, "engaged")
    assert len(rows) == 1
    assert rows[0].after["deal_id"] == str(deal.id)


def test_engage_defaults_value_note_and_owner_from_prospect(db):
    owner = _make_owner(db)
    prospect = _make_prospect(db, owner)  # est_arr 21120.00, note set
    svc = _svc(db, owner)

    deal = svc.engage(prospect.id, _engage_request())
    assert deal.value == Decimal("21120.00")  # falls back to est. ARR
    assert deal.owner_id == owner.id  # falls back to prospect owner
    # First stage record carries the prospect's note.
    assert deal.activities[0].note == (
        "Budget opens new FY. Wants Business Premium for 80 users."
    )


def test_engage_requires_an_owner_and_a_value(db):
    owner = _make_owner(db)
    svc = _svc(db, owner)

    unowned = _make_prospect(db, None, company="Unowned Co.")
    with pytest.raises(BadRequestException) as exc:
        svc.engage(unowned.id, _engage_request())
    assert exc.value.extra["field"] == "owner_id"

    valueless = _make_prospect(db, owner, company="Valueless Co.", est_arr=None)
    with pytest.raises(BadRequestException) as exc:
        svc.engage(valueless.id, _engage_request())
    assert exc.value.extra["field"] == "value"


def test_engage_is_transactional_no_orphan_customer_on_deal_failure(db):
    """Acceptance criterion: if deal creation fails, the customer created

    in the same engage call must not survive — the SAVEPOINT rolls back
    the whole unit and the prospect stays on the nurture list.
    """
    owner = _make_owner(db)
    prospect = _make_prospect(db, owner)
    svc = _svc(db, owner)

    email = f"{uuid4().hex[:10]}@customer.test"
    # Unknown deal owner: DealService raises AFTER the customer insert.
    new_customer = _customer_payload(email=email)
    request = _engage_request(customer=new_customer, owner_id=uuid4())
    with pytest.raises(NotFoundException):
        svc.engage(prospect.id, request)

    # No orphan customer, prospect untouched.
    assert db.query(Customer).filter(Customer.email == email).first() is None
    assert db.query(NurtureProspect).filter_by(id=prospect.id).first() is not None


def test_engage_duplicate_email_409_offers_customer_id_resubmit(db):
    owner = _make_owner(db)
    prospect = _make_prospect(db, owner)
    svc = _svc(db, owner)

    email = f"{uuid4().hex[:10]}@customer.test"
    existing = CustomerService(db).create(_customer_payload(email=email))

    # Case-insensitive dedup: the uppercased variant still conflicts.
    request = _engage_request(customer=_customer_payload(email=email.upper()))
    with pytest.raises(ConflictException) as exc:
        svc.engage(prospect.id, request)
    assert exc.value.extra["field"] == "email"
    assert exc.value.extra["customer_id"] == str(existing.id)

    # Nothing engaged: prospect intact, only the pre-existing customer.
    assert db.query(NurtureProspect).filter_by(id=prospect.id).first() is not None
    assert db.query(Customer).filter(Customer.email == email).count() == 1

    # Re-submit linking customer_id (the 409's option) succeeds.
    deal = svc.engage(
        prospect.id, _engage_request(customer=None, customer_id=existing.id)
    )
    assert deal.customer_id == existing.id
    assert db.query(NurtureProspect).filter_by(id=prospect.id).first() is None
    assert db.query(Customer).count() == 1  # no second customer was created


def test_engage_endpoint_409_and_resubmit_link_path(db, client):
    owner = _make_owner(db)
    _install_actor(owner)
    prospect = _make_prospect(db, owner)

    email = f"{uuid4().hex[:10]}@customer.test"
    existing = CustomerService(db).create(_customer_payload(email=email))

    payload = {
        "customer": {
            "customerType": "business",
            "companyName": "Coast Freight Ltd.",
            "firstName": "Mary",
            "lastName": "Achieng",
            "email": email,
            "phone": "+254720114220",
            "country": "KE",
        },
        "product": "Microsoft 365 Business Premium",
        "seats": 45,
        "value": "9600.00",
        "currency": "USD",
    }
    resp = client.post(f"{API}/{prospect.id}/engage", json=payload)
    assert resp.status_code == 409
    assert resp.json()["details"]["customer_id"] == str(existing.id)

    # Re-submit linking the existing customer.
    resp = client.post(
        f"{API}/{prospect.id}/engage",
        json={
            "customerId": str(existing.id),
            "product": "Microsoft 365 Business Premium",
            "seats": 45,
            "value": "9600.00",
            "currency": "USD",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["customer_id"] == str(existing.id)
    assert body["stage"] == "activation"
    assert body["status"] == "open"


def test_engage_requires_exactly_one_customer_source(db, client):
    owner = _make_owner(db)
    _install_actor(owner)
    prospect = _make_prospect(db, owner)

    base = {"product": "M365", "seats": 1, "value": "100.00", "currency": "USD"}
    # Neither source → 422.
    assert client.post(f"{API}/{prospect.id}/engage", json=base).status_code == 422
    # Both sources → 422.
    both = {
        **base,
        "customerId": str(uuid4()),
        "customer": {
            "customerType": "business",
            "companyName": "X Ltd.",
            "firstName": "A",
            "lastName": "B",
            "email": "x@x.test",
            "phone": "+254720114220",
            "country": "KE",
        },
    }
    assert client.post(f"{API}/{prospect.id}/engage", json=both).status_code == 422


def test_engage_linked_customer_must_exist(db):
    owner = _make_owner(db)
    prospect = _make_prospect(db, owner)
    with pytest.raises(NotFoundException):
        _svc(db, owner).engage(
            prospect.id, _engage_request(customer=None, customer_id=uuid4())
        )
    assert db.query(NurtureProspect).filter_by(id=prospect.id).first() is not None


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------


def test_delete_prospect_removes_row_and_audits(db):
    owner = _make_owner(db)
    prospect = _make_prospect(db, owner)
    prospect_id = prospect.id

    _svc(db, owner).delete(prospect_id)
    assert db.query(NurtureProspect).filter_by(id=prospect_id).first() is None
    assert len(_audit_rows(db, prospect_id, "hard_deleted")) == 1


# --------------------------------------------------------------------------
# Park/resume interplay (fields live on Deal from #39)
# --------------------------------------------------------------------------


def test_parked_deal_resume_clears_due_parked_count(db):
    owner = _make_owner(db)
    today = reporting_date()
    deal = _make_parked_deal(db, owner, parked_until=today)

    svc = _svc(db, owner)
    assert svc.get_notification_counts().due_parked == 1

    resumed = DealService(db, current_user=owner).resume(deal.id)
    assert resumed.status == DealStatus.OPEN
    assert resumed.parked_until is None
    assert svc.get_notification_counts().due_parked == 0
