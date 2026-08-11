"""Sales Desk deals module (issue #39).

Covers:
- stage enum calibration (labels, order, probabilities from Pipeline.svg);
- create: first stage record, audit row, currency validated against the
  customer's billing-profile currencies (#43), inactive customer rejected;
- transition validation: one stage at a time, no advancing closed/parked
  deals, final open stage must close;
- the mandatory-note contract: advance/close/log/park without a non-empty
  note are rejected with 422 (schema AND service level);
- close: enumerated won/lost reasons, close note/reason/closed_at
  persisted, "Closed — Won/Lost" record, audit row;
- park/resume: future re-engage date, resume_stage bookkeeping, records;
- derived values: age_days / idle_days from the first/latest record,
  weighted_value = value x stage probability (null once closed),
  latest_record + full stage_history in responses;
- list filters: tabs, owner, show_closed, hygiene buckets, search;
- update: closed read-only, optimistic-lock 409, currency re-validated;
- delete: open deleted (audit), closed rejected;
- status counts.
"""

from datetime import timedelta
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
    ValidationException,
)
from app.common.pagination import PaginationParams
from app.common.reporting_time import reporting_date
from app.constants.enums import (
    DEAL_PIPELINE_TOTAL_STEPS,
    CustomerStatus,
    CustomerType,
    DealHygieneBucket,
    DealLostReason,
    DealStage,
    DealStatus,
    DealTab,
    DealWonReason,
    UserRole,
)
from app.main import app
from app.modules.auth.models import User
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import CustomerService
from app.modules.deals.models import Deal, DealActivity
from app.modules.deals.schemas import DealCreate, DealFilterParams, DealUpdate
from app.modules.deals.service import DealService

API = "/api/v1/deals"


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


def _make_customer(db, name="Baraka Logistics"):
    """Create a customer through the service so both billing profiles exist."""
    svc = CustomerService(db)
    return svc.create(
        CustomerCreate(
            customer_type=CustomerType.BUSINESS,
            company_name=name,
            first_name="Alice",
            last_name="Wanjiru",
            email=f"{uuid4().hex[:10]}@customer.test",
            phone="+254720114220",
            country="KE",
        )
    )


def _svc(db, owner: User) -> DealService:
    return DealService(db, current_user=owner)


def _make_deal(db, owner, customer, **overrides) -> Deal:
    data = dict(
        customer_id=customer.id,
        owner_id=owner.id,
        product="Microsoft 365 Business Premium",
        seats=45,
        value=Decimal("11880.00"),
        currency="USD",
    )
    data.update(overrides)
    return _svc(db, owner).create(DealCreate(**data), user_id=owner.id)


def _audit_rows(db, deal_id, action):
    return (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "deal",
            AuditEvent.entity_id == deal_id,
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
# Stage enum calibration (Pipeline.svg "Weighted" column)
# --------------------------------------------------------------------------


@pytest.mark.no_db
def test_stage_order_labels_and_indexes():
    assert [s.value for s in DealStage] == [
        "activation",
        "qualification",
        "proposal_quote",
        "negotiation",
    ]
    assert DealStage.ACTIVATION.pipeline_index == 1
    assert DealStage.NEGOTIATION.pipeline_index == 4
    assert DEAL_PIPELINE_TOTAL_STEPS == 5
    assert DealStage.PROPOSAL_QUOTE.label == "Proposal & Quote"
    assert DealStage.ACTIVATION.next_stage is DealStage.QUALIFICATION
    assert DealStage.NEGOTIATION.next_stage is None


@pytest.mark.no_db
def test_stage_probabilities_match_design_weighted_column():
    # Pipeline.svg: $11,880→$5,940 · $51,840→$38,880 · $2,700→$270 ·
    # $5,760→$1,440.
    cases = [
        (DealStage.PROPOSAL_QUOTE, Decimal("11880"), Decimal("5940.00")),
        (DealStage.NEGOTIATION, Decimal("51840"), Decimal("38880.00")),
        (DealStage.ACTIVATION, Decimal("2700"), Decimal("270.00")),
        (DealStage.QUALIFICATION, Decimal("5760"), Decimal("1440.00")),
    ]
    for stage, value, weighted in cases:
        assert (value * stage.probability).quantize(Decimal("0.01")) == weighted


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------


def test_create_deal_writes_first_record_and_audit(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer, note="Walk-in enquiry.")

    assert deal.stage == DealStage.ACTIVATION
    assert deal.status == DealStatus.OPEN
    assert len(deal.activities) == 1
    record = deal.activities[0]
    assert record.stage_label == "Activation"
    assert record.note == "Walk-in enquiry."
    assert record.activity_date == reporting_date()
    assert record.author_id == owner.id
    assert len(_audit_rows(db, deal.id, "created")) == 1


def test_create_deal_defaults_first_note(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    assert deal.activities[0].note == "Deal created."


def test_create_deal_rejects_currency_without_billing_profile(db):
    """The deal currency must be one of the customer's billing-profile

    currencies. Service-created customers carry USD + KES; strip one and
    the currency must be refused.
    """
    owner = _make_owner(db)
    customer = _make_customer(db)
    usd_profile = next(p for p in customer.billing_profiles if p.currency == "USD")
    db.delete(usd_profile)
    db.flush()
    db.expire(customer, ["billing_profiles"])

    with pytest.raises(BadRequestException) as exc:
        _make_deal(db, owner, customer, currency="USD")
    assert "billing-profile" in exc.value.detail
    # KES profile still present → KES accepted.
    deal = _make_deal(db, owner, customer, currency="KES")
    assert deal.currency == "KES"


def test_create_deal_rejects_inactive_customer_and_missing_refs(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    customer.status = CustomerStatus.INACTIVE
    db.flush()

    with pytest.raises(BadRequestException):
        _make_deal(db, owner, customer)

    with pytest.raises(NotFoundException):
        _make_deal(db, owner, SimpleNamespace(id=uuid4()))

    active = _make_customer(db, name="Savannah Microfinance")
    with pytest.raises(NotFoundException):
        _svc(db, owner).create(
            DealCreate(
                customer_id=active.id,
                owner_id=uuid4(),
                product="Microsoft 365 E3",
                seats=120,
                value=Decimal("51840.00"),
                currency="USD",
            )
        )


# --------------------------------------------------------------------------
# Stage transitions
# --------------------------------------------------------------------------


def test_advance_moves_exactly_one_stage_and_records(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    svc = _svc(db, owner)

    svc.advance_stage(deal.id, "Discovery call done.")
    assert deal.stage == DealStage.QUALIFICATION
    assert deal.activities[-1].stage_label == "Qualification"
    assert deal.activities[-1].note == "Discovery call done."
    assert len(_audit_rows(db, deal.id, "stage_advanced")) == 1

    svc.advance_stage(deal.id, "Quote Q-2031 sent.")
    svc.advance_stage(deal.id, "Asked for 10% off.")
    assert deal.stage == DealStage.NEGOTIATION

    # Final open stage: advancing further must fail (close instead).
    with pytest.raises(BadRequestException) as exc:
        svc.advance_stage(deal.id, "One more push.")
    assert "final open stage" in exc.value.detail


def test_advance_requires_non_empty_note_422(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    svc = _svc(db, owner)

    for bad_note in ("", "   ", None):
        with pytest.raises(ValidationException) as exc:
            svc.advance_stage(deal.id, bad_note)
        assert exc.value.status_code == 422
    # Nothing advanced, nothing recorded.
    assert deal.stage == DealStage.ACTIVATION
    assert len(deal.activities) == 1


def test_advance_endpoint_note_missing_or_blank_is_422(db, client):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    _install_actor(owner)

    assert client.post(f"{API}/{deal.id}/advance", json={}).status_code == 422
    assert (
        client.post(f"{API}/{deal.id}/advance", json={"note": "   "}).status_code
        == 422
    )
    ok = client.post(f"{API}/{deal.id}/advance", json={"note": "Qualified."})
    assert ok.status_code == 200
    assert ok.json()["stage"] == "qualification"


def test_closed_and_parked_deals_cannot_advance(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    svc = _svc(db, owner)

    won = _make_deal(db, owner, customer)
    svc.close(won.id, "won", DealWonReason.PRODUCT_FIT.value, "Great fit.")
    with pytest.raises(BadRequestException):
        svc.advance_stage(won.id, "Should not work.")

    parked = _make_deal(db, owner, customer)
    svc.park(parked.id, reporting_date() + timedelta(days=30), "On hold.")
    with pytest.raises(BadRequestException):
        svc.advance_stage(parked.id, "Should not work either.")


# --------------------------------------------------------------------------
# Close
# --------------------------------------------------------------------------


def test_close_won_persists_reason_note_and_record(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    svc = _svc(db, owner)

    svc.close(
        deal.id,
        "won",
        DealWonReason.MIGRATION_SUPPORT_OFFER.value,
        "Won on white-glove migration plan.",
    )
    assert deal.status == DealStatus.WON
    assert deal.close_reason == "Migration & support offer"
    assert deal.close_note == "Won on white-glove migration plan."
    assert deal.closed_at is not None
    record = deal.activities[-1]
    assert record.stage_label == "Closed — Won"
    assert record.note == (
        "Migration & support offer — Won on white-glove migration plan."
    )
    assert len(_audit_rows(db, deal.id, "closed")) == 1


def test_close_lost_and_terminal_state(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    svc = _svc(db, owner)

    svc.close(
        deal.id,
        "lost",
        DealLostReason.PRICE_TOO_HIGH.value,
        "Board chose a cheaper local email host.",
    )
    assert deal.status == DealStatus.LOST
    assert deal.activities[-1].stage_label == "Closed — Lost"

    # Terminal: cannot close again nor park.
    with pytest.raises(BadRequestException):
        svc.close(deal.id, "won", DealWonReason.PRODUCT_FIT.value, "Nope.")
    with pytest.raises(BadRequestException):
        svc.park(deal.id, reporting_date() + timedelta(days=10), "Nope.")


def test_close_requires_note_and_matching_reason(db, client):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    svc = _svc(db, owner)

    with pytest.raises(ValidationException):
        svc.close(deal.id, "won", DealWonReason.PRODUCT_FIT.value, "  ")
    # A lost reason on a won close is rejected.
    with pytest.raises(BadRequestException):
        svc.close(deal.id, "won", DealLostReason.PRICE_TOO_HIGH.value, "Note.")

    _install_actor(owner)
    # Schema-level: reason must match the result's enumerated list (422).
    resp = client.post(
        f"{API}/{deal.id}/close",
        json={"result": "lost", "reason": "Product fit", "note": "x"},
    )
    assert resp.status_code == 422
    # Missing note → 422.
    resp = client.post(
        f"{API}/{deal.id}/close",
        json={"result": "lost", "reason": "Price too high"},
    )
    assert resp.status_code == 422
    resp = client.post(
        f"{API}/{deal.id}/close",
        json={"result": "lost", "reason": "Price too high", "note": "Too dear."},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "lost"
    assert resp.json()["close_reason"] == "Price too high"


# --------------------------------------------------------------------------
# Log activity
# --------------------------------------------------------------------------


def test_log_activity_appends_record_at_current_stage(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    svc = _svc(db, owner)

    svc.log_activity(deal.id, "Called procurement; demo booked.")
    assert len(deal.activities) == 2
    assert deal.activities[-1].stage_label == "Activation"
    assert deal.stage == DealStage.ACTIVATION  # unchanged

    with pytest.raises(ValidationException):
        svc.log_activity(deal.id, "   ")

    svc.close(deal.id, "won", DealWonReason.PRODUCT_FIT.value, "Done.")
    with pytest.raises(BadRequestException):
        svc.log_activity(deal.id, "Closed deals keep no new records.")


# --------------------------------------------------------------------------
# Park / resume
# --------------------------------------------------------------------------


def test_park_and_resume_roundtrip(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    svc = _svc(db, owner)
    svc.advance_stage(deal.id, "Qualified.")

    until = reporting_date() + timedelta(days=45)
    svc.park(deal.id, until, "Mid-contract with competitor until November.")
    assert deal.status == DealStatus.PARKED
    assert deal.parked_until == until
    assert deal.resume_stage == DealStage.QUALIFICATION
    record = deal.activities[-1]
    assert record.stage_label == "Moved to future pipeline"
    assert record.note.endswith(f"— re-engage on {until.isoformat()}")
    assert len(_audit_rows(db, deal.id, "parked")) == 1

    svc.resume(deal.id)
    assert deal.status == DealStatus.OPEN
    assert deal.stage == DealStage.QUALIFICATION
    assert deal.parked_until is None
    assert deal.resume_stage is None
    assert deal.activities[-1].note == "Re-engaged from the future pipeline."
    assert len(_audit_rows(db, deal.id, "resumed")) == 1


def test_park_requires_future_date_and_open_status(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    svc = _svc(db, owner)

    with pytest.raises(BadRequestException):
        svc.park(deal.id, reporting_date(), "Today is not the future.")
    with pytest.raises(ValidationException):
        svc.park(deal.id, reporting_date() + timedelta(days=5), "")

    svc.park(deal.id, reporting_date() + timedelta(days=5), "Parked.")
    with pytest.raises(BadRequestException):
        svc.park(deal.id, reporting_date() + timedelta(days=9), "Again.")

    svc.resume(deal.id)
    with pytest.raises(BadRequestException):
        svc.resume(deal.id)  # only parked deals can resume


# --------------------------------------------------------------------------
# Derived values (server-computed; the UI never derives them)
# --------------------------------------------------------------------------


def _backdate(db, activity: DealActivity, days: int):
    activity.activity_date = reporting_date() - timedelta(days=days)
    db.flush()


def test_age_idle_and_weighted_value_in_response(db):
    owner = _make_owner(db, first="Michael", last="Wafula")
    customer = _make_customer(db, name="Savannah Microfinance")
    deal = _make_deal(
        db,
        owner,
        customer,
        product="Microsoft 365 E3",
        seats=120,
        value=Decimal("51840.00"),
        currency="KES",
    )
    svc = _svc(db, owner)
    svc.advance_stage(deal.id, "Qualified.")
    svc.advance_stage(deal.id, "Quote sent.")
    svc.advance_stage(deal.id, "Asked for 10% off + free migration.")

    # First record 40 days ago, latest 3 days ago (design row).
    _backdate(db, deal.activities[0], 40)
    _backdate(db, deal.activities[1], 20)
    _backdate(db, deal.activities[2], 10)
    _backdate(db, deal.activities[3], 3)

    response = svc.build_response(deal)
    assert response.age_days == 40
    assert response.idle_days == 3
    assert response.weighted_value == Decimal("38880.00")  # 51840 x 0.75
    assert response.stage_index == 4
    assert response.stage_total == 5
    assert response.stage_label == "Negotiation"
    assert response.company_name == "Savannah Microfinance"
    assert response.owner.name == "Michael Wafula"
    assert response.owner.initials == "MW"
    assert response.latest_record is not None
    assert response.latest_record.note == "Asked for 10% off + free migration."
    assert response.latest_record.stage_label == "Negotiation"
    assert [r.stage_label for r in response.stage_history] == [
        "Activation",
        "Qualification",
        "Proposal & Quote",
        "Negotiation",
    ]
    # Billing line joined from the KES billing profile (#43).
    assert response.billing_profile is not None
    assert response.billing_profile.code.endswith("-KES")
    assert response.billing_profile.payment_terms == "14 days"
    assert response.billing_profile.tax_treatment == "VAT 16%"
    assert response.billing_profile.synced is False


def test_weighted_value_null_once_closed(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    svc = _svc(db, owner)

    assert svc.build_response(deal).weighted_value == Decimal("1188.00")
    svc.close(deal.id, "won", DealWonReason.PRODUCT_FIT.value, "Signed.")
    assert svc.build_response(deal).weighted_value is None


def test_detail_endpoint_carries_screen_contract_fields(db, client):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer, note="Walk-in enquiry.")
    _install_actor(owner)

    body = client.get(f"{API}/{deal.id}").json()
    for field in (
        "id",
        "customer_id",
        "company_name",
        "owner",
        "product",
        "seats",
        "value",
        "currency",
        "stage",
        "stage_index",
        "stage_total",
        "stage_label",
        "status",
        "parked_until",
        "resume_stage",
        "close_reason",
        "close_note",
        "closed_at",
        "age_days",
        "idle_days",
        "weighted_value",
        "latest_record",
        "stage_history",
        "billing_profile",
        "version",
    ):
        assert field in body, f"screen-contract field '{field}' missing"
    assert body["owner"].keys() >= {"id", "name", "initials"}
    assert body["latest_record"]["note"] == "Walk-in enquiry."
    assert body["stage_history"][0]["stage_label"] == "Activation"
    assert body["billing_profile"]["code"].endswith("-USD")


# --------------------------------------------------------------------------
# List filters
# --------------------------------------------------------------------------


def _pipeline_fixture(db):
    """Two owners; open (activation), open (negotiation), won, lost, parked."""
    tabitha = _make_owner(db, "Tabitha", "Kimani")
    joy = _make_owner(db, "Joy", "Nduta")
    svc = _svc(db, tabitha)

    dental = _make_customer(db, name="Nairobi Dental Group")
    open_fresh = _make_deal(
        db,
        tabitha,
        dental,
        product="Microsoft 365 Business Standard",
        seats=18,
        value=Decimal("2700.00"),
        currency="KES",
    )

    savannah = _make_customer(db, name="Savannah Microfinance")
    open_negotiation = _make_deal(
        db,
        tabitha,
        savannah,
        product="Microsoft 365 E3",
        seats=120,
        value=Decimal("51840.00"),
    )
    for note in ("Qualified.", "Quote sent.", "Negotiating."):
        svc.advance_stage(open_negotiation.id, note)

    acacia = _make_customer(db, name="Acacia Insurance")
    won = _make_deal(
        db,
        joy,
        acacia,
        product="Microsoft 365 E5",
        seats=200,
        value=Decimal("136800.00"),
    )
    svc.close(won.id, "won", DealWonReason.MIGRATION_SUPPORT_OFFER.value, "Won.")

    tumaini = _make_customer(db, name="Tumaini SACCO")
    lost = _make_deal(
        db,
        joy,
        tumaini,
        product="Microsoft 365 Business Standard",
        seats=35,
        value=Decimal("5250.00"),
    )
    svc.close(lost.id, "lost", DealLostReason.PRICE_TOO_HIGH.value, "Too dear.")

    kilifi = _make_customer(db, name="Kilifi Beach Resorts")
    parked = _make_deal(
        db,
        joy,
        kilifi,
        product="Teams Phone Standard",
        seats=60,
        value=Decimal("5760.00"),
    )
    svc.park(parked.id, reporting_date() + timedelta(days=30), "Parked.")

    return SimpleNamespace(
        svc=svc,
        tabitha=tabitha,
        joy=joy,
        open_fresh=open_fresh,
        open_negotiation=open_negotiation,
        won=won,
        lost=lost,
        parked=parked,
    )


def _ids(page):
    return {str(item.id) for item in page.items}


def test_list_tabs_owner_and_show_closed(db):
    fx = _pipeline_fixture(db)
    params = PaginationParams(page=1, per_page=50)

    all_page = fx.svc.list_deals(params, DealFilterParams(tab=DealTab.ALL))
    assert _ids(all_page) == {
        str(fx.open_fresh.id),
        str(fx.open_negotiation.id),
        str(fx.won.id),
        str(fx.lost.id),
        str(fx.parked.id),
    }

    open_page = fx.svc.list_deals(params, DealFilterParams(tab=DealTab.OPEN))
    assert _ids(open_page) == {str(fx.open_fresh.id), str(fx.open_negotiation.id)}

    won_page = fx.svc.list_deals(params, DealFilterParams(tab=DealTab.WON))
    assert _ids(won_page) == {str(fx.won.id)}

    lost_page = fx.svc.list_deals(params, DealFilterParams(tab=DealTab.LOST))
    assert _ids(lost_page) == {str(fx.lost.id)}

    # ALL + show_closed=False keeps open + parked only.
    visible = fx.svc.list_deals(
        params, DealFilterParams(tab=DealTab.ALL, show_closed=False)
    )
    assert _ids(visible) == {
        str(fx.open_fresh.id),
        str(fx.open_negotiation.id),
        str(fx.parked.id),
    }

    tabitha_page = fx.svc.list_deals(params, DealFilterParams(owner_id=fx.tabitha.id))
    assert _ids(tabitha_page) == {
        str(fx.open_fresh.id),
        str(fx.open_negotiation.id),
    }


def test_list_hygiene_buckets(db):
    fx = _pipeline_fixture(db)
    params = PaginationParams(page=1, per_page=50)

    # open_fresh: first + latest record today → active_week.
    # open_negotiation: first 50 days ago, latest 10 days ago →
    # quiet_8_30 + open_45.
    _backdate(db, fx.open_negotiation.activities[0], 50)
    for activity in fx.open_negotiation.activities[1:]:
        _backdate(db, activity, 10)

    active = fx.svc.list_deals(
        params, DealFilterParams(hygiene=DealHygieneBucket.ACTIVE_WEEK)
    )
    assert _ids(active) == {str(fx.open_fresh.id)}

    quiet = fx.svc.list_deals(
        params, DealFilterParams(hygiene=DealHygieneBucket.QUIET_8_30)
    )
    assert _ids(quiet) == {str(fx.open_negotiation.id)}

    old = fx.svc.list_deals(params, DealFilterParams(hygiene=DealHygieneBucket.OPEN_45))
    assert _ids(old) == {str(fx.open_negotiation.id)}

    # No open deal is >30d idle; closed/parked never qualify.
    cold = fx.svc.list_deals(
        params, DealFilterParams(hygiene=DealHygieneBucket.NO_ACTIVITY_30)
    )
    assert _ids(cold) == set()

    # 31 days idle → cold, and no longer quiet_8_30.
    for activity in fx.open_negotiation.activities[1:]:
        _backdate(db, activity, 31)
    cold = fx.svc.list_deals(
        params, DealFilterParams(hygiene=DealHygieneBucket.NO_ACTIVITY_30)
    )
    assert _ids(cold) == {str(fx.open_negotiation.id)}


def test_list_search_company_and_product(db):
    fx = _pipeline_fixture(db)
    params = PaginationParams(page=1, per_page=50)

    by_company = fx.svc.list_deals(params, DealFilterParams(search="Savannah"))
    assert _ids(by_company) == {str(fx.open_negotiation.id)}

    by_product = fx.svc.list_deals(params, DealFilterParams(search="Teams Phone"))
    assert _ids(by_product) == {str(fx.parked.id)}

    none = fx.svc.list_deals(params, DealFilterParams(search="zzz-no-match"))
    assert _ids(none) == set()


def test_list_endpoint_pagination_metadata(db, client):
    fx = _pipeline_fixture(db)
    _install_actor(fx.tabitha)

    resp = client.get(API, params={"per_page": 2, "withTotal": True})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["metadata"]["total"] == 5
    assert body["metadata"]["total_pages"] == 3
    assert body["metadata"]["has_next"] is True

    resp = client.get(API, params={"tab": "open"})
    assert {item["status"] for item in resp.json()["items"]} == {"open"}


def test_status_counts(db):
    fx = _pipeline_fixture(db)
    counts = fx.svc.get_status_counts()
    assert counts.all == 5
    assert counts.open == 2
    assert counts.won == 1
    assert counts.lost == 1
    assert counts.parked == 1

    joy_counts = fx.svc.get_status_counts(owner_id=fx.joy.id)
    assert joy_counts.all == 3
    assert joy_counts.open == 0


# --------------------------------------------------------------------------
# Update / delete
# --------------------------------------------------------------------------


def test_update_open_deal_and_optimistic_locking(db):
    owner = _make_owner(db)
    other = _make_owner(db, "Joy", "Nduta")
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    svc = _svc(db, owner)

    updated = svc.update(
        deal.id,
        DealUpdate(seats=60, value=Decimal("15840.00"), owner_id=other.id),
        expected_version=deal.version,
    )
    assert updated.seats == 60
    assert updated.owner_id == other.id
    assert updated.version == 2

    with pytest.raises(ConflictException):
        svc.update(deal.id, DealUpdate(seats=61), expected_version=1)


def test_update_currency_revalidated_and_closed_read_only(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer, currency="USD")
    svc = _svc(db, owner)

    kes_profile = next(p for p in customer.billing_profiles if p.currency == "KES")
    db.delete(kes_profile)
    db.flush()
    db.expire(customer, ["billing_profiles"])

    with pytest.raises(BadRequestException):
        svc.update(deal.id, DealUpdate(currency="KES"), expected_version=deal.version)

    svc.close(deal.id, "won", DealWonReason.PRODUCT_FIT.value, "Signed.")
    with pytest.raises(BadRequestException):
        svc.update(deal.id, DealUpdate(seats=99), expected_version=deal.version)


def test_delete_open_deal_audited_closed_rejected(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    svc = _svc(db, owner)

    open_deal = _make_deal(db, owner, customer)
    open_id = open_deal.id
    svc.delete(open_id)
    assert db.query(Deal).filter(Deal.id == open_id).first() is None
    assert (
        db.query(DealActivity).filter(DealActivity.deal_id == open_id).count()
        == 0
    )
    assert len(_audit_rows(db, open_id, "hard_deleted")) == 1

    closed = _make_deal(db, owner, customer)
    svc.close(closed.id, "won", DealWonReason.PRODUCT_FIT.value, "Signed.")
    with pytest.raises(BadRequestException):
        svc.delete(closed.id)


# --------------------------------------------------------------------------
# End-to-end endpoint flow
# --------------------------------------------------------------------------


def test_endpoint_full_lifecycle(db, client):
    owner = _make_owner(db)
    customer = _make_customer(db)
    _install_actor(owner)

    created = client.post(
        API,
        json={
            "customerId": str(customer.id),
            "ownerId": str(owner.id),
            "product": "Microsoft 365 Business Premium",
            "seats": 45,
            "value": "11880.00",
            "currency": "USD",
            "note": "Walk-in enquiry.",
        },
    )
    assert created.status_code == 201
    deal_id = created.json()["id"]
    assert Decimal(str(created.json()["weighted_value"])) == Decimal("1188.00")

    parked = client.post(
        f"{API}/{deal_id}/park",
        json={
            "parkedUntil": (reporting_date() + timedelta(days=21)).isoformat(),
            "note": "Engage before Term 1 planning.",
        },
    )
    assert parked.status_code == 200
    assert parked.json()["status"] == "parked"
    assert parked.json()["resume_stage"] == "activation"

    resumed = client.post(f"{API}/{deal_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "open"

    logged = client.post(f"{API}/{deal_id}/activities", json={"note": "Demo booked."})
    assert logged.status_code == 201

    advanced = client.post(
        f"{API}/{deal_id}/advance", json={"note": "Site survey done."}
    )
    assert advanced.status_code == 200
    assert advanced.json()["stage_index"] == 2

    closed = client.post(
        f"{API}/{deal_id}/close",
        json={
            "result": "won",
            "reason": "Migration & support offer",
            "note": "Won on migration plan.",
        },
    )
    assert closed.status_code == 200
    body = closed.json()
    assert body["status"] == "won"
    assert body["weighted_value"] is None
    assert body["close_reason"] == "Migration & support offer"
    labels = [r["stage_label"] for r in body["stage_history"]]
    assert labels == [
        "Activation",
        "Moved to future pipeline",
        "Activation",
        "Activation",
        "Qualification",
        "Closed — Won",
    ]

    missing = client.get(f"{API}/{uuid4()}")
    assert missing.status_code == 404
