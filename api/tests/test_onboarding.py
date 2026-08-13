"""Sales Desk onboarding module (issue #41).

Covers:
- auto-creation: closing a deal WON creates its onboarding checklist in
  the SAME transaction (flush-only, visible pre-commit), with the FK pair
  (deal + customer), the verbatim "{product} · {seats} seats" plan label,
  the design's 7 seeded tasks in exact order, and an audit row; closing
  LOST creates nothing; guards reject non-won deals and duplicates;
- task toggling: done flag + completion timestamps flip both ways, the
  updated record returns server-recomputed percent (43 / 86 per
  Onboarding.svg) and "N of 7" counts, checklist-level completed_at is
  set on the last task and cleared on re-open, unknown positions 404;
- mark complete: remaining steps stamped done, already-complete rejected;
- template versioning: editing the owner-settings template bumps the
  version and never mutates existing checklists; new checklists copy the
  new template; unchanged re-saves do not bump; blank labels rejected.
"""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.common.audit import AuditEvent
from app.common.dependencies import get_current_user
from app.common.exceptions import BadRequestException, NotFoundException
from app.common.pagination import PaginationParams
from app.constants.enums import (
    CustomerType,
    DealStatus,
    DealWonReason,
    UserRole,
)
from app.constants.settings_defaults import DEFAULT_ONBOARDING_TASKS
from app.main import app
from app.modules.auth.models import User
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import CustomerService
from app.modules.deals.schemas import DealCreate
from app.modules.deals.service import DealService
from app.modules.onboarding.models import Onboarding
from app.modules.onboarding.service import OnboardingService
from app.modules.owner.schemas import OwnerProfileUpdate
from app.modules.owner.service import OwnerService

API = "/api/v1/onboardings"

#: The design's 7 tasks — exact labels, exact order (Onboarding.svg).
DESIGN_TASKS = [
    "Kick-off meeting",
    "Tenant & domain setup",
    "Licenses assigned",
    "Data migration",
    "Security baseline",
    "User training",
    "Handover to support",
]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _make_owner(db, first="Tabitha", last="Kimani") -> User:
    user = User(
        email=f"{uuid4().hex[:10]}@sales.example.com",
        password_hash="x" * 60,
        first_name=first,
        last_name=last,
        role=UserRole.MEMBER,
    )
    db.add(user)
    db.flush()
    return user


def _make_customer(db, name="Acacia Insurance"):
    """Create a customer through the service so both billing profiles exist."""
    svc = CustomerService(db)
    return svc.create(
        CustomerCreate(
            customer_type=CustomerType.BUSINESS,
            company_name=name,
            first_name="Alice",
            last_name="Wanjiru",
            email=f"{uuid4().hex[:10]}@acacia.co.ke",
            phone="+254720114220",
            country="KE",
        )
    )


def _make_deal(db, owner, customer, **overrides):
    data = dict(
        customer_id=customer.id,
        owner_id=owner.id,
        product="Microsoft 365 E5",
        seats=200,
        value=Decimal("114000.00"),
        currency="USD",
    )
    data.update(overrides)
    return DealService(db, current_user=owner).create(
        DealCreate(**data), user_id=owner.id
    )


def _win(db, owner, deal):
    """Close a deal WON (which auto-creates its onboarding)."""
    DealService(db, current_user=owner).close(
        deal.id, "won", DealWonReason.PRODUCT_FIT.value, "Great fit."
    )
    return db.query(Onboarding).filter(Onboarding.deal_id == deal.id).one()


def _svc(db, owner) -> OnboardingService:
    return OnboardingService(db, current_user=owner)


def _audit_rows(db, onboarding_id, action):
    return (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "onboarding",
            AuditEvent.entity_id == onboarding_id,
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
# Seeded default template (settings defaults)
# --------------------------------------------------------------------------


@pytest.mark.no_db
def test_default_template_is_the_designs_7_tasks_verbatim_in_order():
    assert list(DEFAULT_ONBOARDING_TASKS) == DESIGN_TASKS


@pytest.mark.no_db
def test_percent_is_half_up_and_matches_design_values():
    # Onboarding.svg: 3 of 7 → 43%, 6 of 7 → 86%.
    assert OnboardingService._percent(3, 7) == 43
    assert OnboardingService._percent(6, 7) == 86
    assert OnboardingService._percent(0, 7) == 0
    assert OnboardingService._percent(7, 7) == 100
    # Half-up on exact halves; degenerate zero-total guards to 0.
    assert OnboardingService._percent(1, 8) == 13
    assert OnboardingService._percent(0, 0) == 0


# --------------------------------------------------------------------------
# Auto-creation on won deals
# --------------------------------------------------------------------------


def test_won_deal_creates_onboarding_in_same_transaction(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)

    DealService(db, current_user=owner).close(
        deal.id, "won", DealWonReason.PRODUCT_FIT.value, "Great fit."
    )

    # Visible pre-commit: the checklist was flushed inside the SAME open
    # transaction as the close — commit/rollback governs the pair.
    onboarding = db.query(Onboarding).filter(Onboarding.deal_id == deal.id).one()
    assert deal.status == DealStatus.WON
    assert onboarding.customer_id == customer.id
    assert onboarding.plan_label == "Microsoft 365 E5 · 200 seats"
    assert onboarding.completed_at is None
    assert onboarding.template_version == 1

    # The design's 7 tasks, verbatim, in exact order, all undone.
    assert [t.position for t in onboarding.tasks] == [1, 2, 3, 4, 5, 6, 7]
    assert [t.label for t in onboarding.tasks] == DESIGN_TASKS
    assert all(not t.done and t.completed_at is None for t in onboarding.tasks)

    # Audited in the same transaction (common/audit.py contract).
    rows = _audit_rows(db, onboarding.id, "created")
    assert len(rows) == 1
    assert rows[0].after["plan_label"] == "Microsoft 365 E5 · 200 seats"
    assert rows[0].after["tasks_total"] == 7


def test_lost_deal_creates_no_onboarding(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)

    DealService(db, current_user=owner).close(
        deal.id, "lost", "Price too high", "Too dear."
    )
    assert db.query(Onboarding).count() == 0


def test_create_for_won_deal_guards_non_won_and_duplicates(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    svc = _svc(db, owner)

    open_deal = _make_deal(db, owner, customer)
    with pytest.raises(BadRequestException) as exc:
        svc.create_for_won_deal(open_deal)
    assert "won" in exc.value.detail

    won = _make_deal(db, owner, customer)
    _win(db, owner, won)
    won_deal = won  # already has its checklist from the close flow
    with pytest.raises(BadRequestException) as exc:
        svc.create_for_won_deal(won_deal)
    assert "already has" in exc.value.detail
    assert db.query(Onboarding).filter(Onboarding.deal_id == won.id).count() == 1


def test_close_won_endpoint_returns_deal_and_creates_checklist(db, client):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    _install_actor(owner)

    resp = client.post(
        f"/api/v1/deals/{deal.id}/close",
        json={"result": "won", "reason": "Product fit", "note": "Great fit."},
    )
    assert resp.status_code == 200
    onboarding = db.query(Onboarding).filter(Onboarding.deal_id == deal.id).one()
    assert onboarding.plan_label == "Microsoft 365 E5 · 200 seats"


# --------------------------------------------------------------------------
# List endpoint (screen contract)
# --------------------------------------------------------------------------


def test_list_returns_screen_contract_fields(db, client):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    onboarding = _win(db, owner, deal)
    svc = _svc(db, owner)
    svc.toggle_task(onboarding.id, 1)
    svc.toggle_task(onboarding.id, 2)
    svc.toggle_task(onboarding.id, 3)
    _install_actor(owner)

    resp = client.get(API)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["company_name"] == "Acacia Insurance"
    assert item["plan_label"] == "Microsoft 365 E5 · 200 seats"
    assert item["percent_complete"] == 43
    assert item["tasks_completed"] == 3
    assert item["tasks_total"] == 7
    # Steps expose exactly {position, label, done, completed_at}.
    assert [s["position"] for s in item["steps"]] == [1, 2, 3, 4, 5, 6, 7]
    assert [s["label"] for s in item["steps"]] == DESIGN_TASKS
    assert item["steps"][0]["done"] is True
    assert item["steps"][0]["completed_at"] is not None
    assert item["steps"][6]["done"] is False
    assert item["steps"][6]["completed_at"] is None


def test_list_search_matches_company_and_plan_label(db, client):
    owner = _make_owner(db)
    acacia = _make_customer(db, name="Acacia Insurance")
    highlands = _make_customer(db, name="Highlands Coffee Exporters")
    _win(db, owner, _make_deal(db, owner, acacia))
    _win(
        db,
        owner,
        _make_deal(db, owner, highlands, product="Business Standard", seats=25),
    )
    _install_actor(owner)

    resp = client.get(API, params={"search": "Highlands"})
    assert [i["company_name"] for i in resp.json()["items"]] == [
        "Highlands Coffee Exporters"
    ]
    resp = client.get(API, params={"search": "Business Standard"})
    assert [i["plan_label"] for i in resp.json()["items"]] == [
        "Business Standard · 25 seats"
    ]


# --------------------------------------------------------------------------
# Task toggling
# --------------------------------------------------------------------------


def test_toggle_flips_done_and_completion_timestamps_both_ways(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    onboarding = _win(db, owner, _make_deal(db, owner, customer))
    svc = _svc(db, owner)

    svc.toggle_task(onboarding.id, 4)
    task = next(t for t in onboarding.tasks if t.position == 4)
    assert task.done is True
    assert task.completed_at is not None
    assert len(_audit_rows(db, onboarding.id, "task_toggled")) == 1

    svc.toggle_task(onboarding.id, 4)
    assert task.done is False
    assert task.completed_at is None
    assert len(_audit_rows(db, onboarding.id, "task_toggled")) == 2


def test_toggle_endpoint_returns_reconciliation_payload(db, client):
    """The optimistic UI reconciles against the server-recomputed record."""
    owner = _make_owner(db)
    customer = _make_customer(db)
    onboarding = _win(db, owner, _make_deal(db, owner, customer))
    _install_actor(owner)

    for position in (1, 2, 3):
        resp = client.post(f"{API}/{onboarding.id}/tasks/{position}/toggle")
        assert resp.status_code == 200
    body = resp.json()
    assert body["percent_complete"] == 43
    assert body["tasks_completed"] == 3
    assert body["tasks_total"] == 7
    assert body["is_complete"] is False

    for position in (4, 5, 6):
        body = client.post(f"{API}/{onboarding.id}/tasks/{position}/toggle").json()
    assert body["percent_complete"] == 86
    assert body["tasks_completed"] == 6

    # Un-toggling reconciles downwards too.
    body = client.post(f"{API}/{onboarding.id}/tasks/1/toggle").json()
    assert body["percent_complete"] == 71
    assert body["tasks_completed"] == 5
    assert body["steps"][0]["done"] is False
    assert body["steps"][0]["completed_at"] is None


def test_last_task_completes_checklist_and_reopen_clears_it(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    onboarding = _win(db, owner, _make_deal(db, owner, customer))
    svc = _svc(db, owner)

    for position in range(1, 8):
        svc.toggle_task(onboarding.id, position)
    assert onboarding.is_complete
    assert onboarding.completed_at is not None

    svc.toggle_task(onboarding.id, 7)
    assert not onboarding.is_complete
    assert onboarding.completed_at is None


def test_toggle_unknown_position_or_onboarding_is_404(db, client):
    owner = _make_owner(db)
    customer = _make_customer(db)
    onboarding = _win(db, owner, _make_deal(db, owner, customer))
    svc = _svc(db, owner)

    with pytest.raises(NotFoundException):
        svc.toggle_task(onboarding.id, 8)
    with pytest.raises(NotFoundException):
        svc.toggle_task(uuid4(), 1)

    _install_actor(owner)
    assert client.post(f"{API}/{onboarding.id}/tasks/99/toggle").status_code == 404
    assert client.post(f"{API}/{uuid4()}/tasks/1/toggle").status_code == 404


# --------------------------------------------------------------------------
# Mark complete
# --------------------------------------------------------------------------


def test_mark_complete_stamps_all_remaining_tasks(db, client):
    owner = _make_owner(db)
    customer = _make_customer(db)
    onboarding = _win(db, owner, _make_deal(db, owner, customer))
    svc = _svc(db, owner)
    svc.toggle_task(onboarding.id, 1)
    _install_actor(owner)

    resp = client.post(f"{API}/{onboarding.id}/complete")
    assert resp.status_code == 200
    body = resp.json()
    assert body["percent_complete"] == 100
    assert body["tasks_completed"] == 7
    assert body["is_complete"] is True
    assert body["completed_at"] is not None
    assert all(s["done"] and s["completed_at"] for s in body["steps"])
    assert len(_audit_rows(db, onboarding.id, "completed")) == 1

    # Already complete → 400.
    assert client.post(f"{API}/{onboarding.id}/complete").status_code == 400


# --------------------------------------------------------------------------
# Template versioning (owner settings)
# --------------------------------------------------------------------------

NEW_TEMPLATE = ["Kick-off meeting", "Provisioning", "Go-live review"]


def _set_template(db, tasks) -> None:
    OwnerService(db).update(
        OwnerProfileUpdate(full_name="Priori", onboarding_task_template=tasks)
    )


def test_changing_template_never_mutates_existing_checklists(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    existing = _win(db, owner, _make_deal(db, owner, customer))
    assert existing.template_version == 1

    _set_template(db, NEW_TEMPLATE)
    assert OwnerService(db).onboarding_template().version == 2

    # The existing checklist copied its tasks at create time — untouched.
    db.expire_all()
    assert [t.label for t in existing.tasks] == DESIGN_TASKS
    assert existing.template_version == 1

    # A new win copies the NEW template and snapshots the new version.
    fresh = _win(db, owner, _make_deal(db, owner, customer, seats=25))
    assert [t.label for t in fresh.tasks] == NEW_TEMPLATE
    assert [t.position for t in fresh.tasks] == [1, 2, 3]
    assert fresh.template_version == 2

    # Counts follow the checklist's own template, not the global default.
    response = _svc(db, owner).build_response(fresh)
    assert response.tasks_total == 3
    assert response.percent_complete == 0


def test_unchanged_template_resave_does_not_bump_version(db):
    _set_template(db, list(DEFAULT_ONBOARDING_TASKS))  # equals the default
    assert OwnerService(db).onboarding_template().version == 1

    _set_template(db, NEW_TEMPLATE)
    assert OwnerService(db).onboarding_template().version == 2
    _set_template(db, NEW_TEMPLATE)  # identical re-save
    assert OwnerService(db).onboarding_template().version == 2

    # Resetting to the built-in default is a real change again.
    _set_template(db, None)
    template = OwnerService(db).onboarding_template()
    assert template.version == 3
    assert template.tasks == DESIGN_TASKS


def test_owner_endpoint_exposes_resolved_template_and_validates(db, client):
    owner = _make_owner(db)
    _install_actor(owner)

    resp = client.get("/api/v1/owner")
    assert resp.status_code == 200
    assert resp.json()["onboardingTaskTemplate"] == DESIGN_TASKS
    assert resp.json()["onboardingTemplateVersion"] == 1

    resp = client.put(
        "/api/v1/owner",
        json={"fullName": "Priori", "onboardingTaskTemplate": NEW_TEMPLATE},
    )
    assert resp.status_code == 200
    assert resp.json()["onboardingTaskTemplate"] == NEW_TEMPLATE
    assert resp.json()["onboardingTemplateVersion"] == 2

    # Blank labels and empty templates are rejected.
    resp = client.put(
        "/api/v1/owner",
        json={"fullName": "Priori", "onboardingTaskTemplate": ["ok", "  "]},
    )
    assert resp.status_code == 422
    resp = client.put(
        "/api/v1/owner",
        json={"fullName": "Priori", "onboardingTaskTemplate": []},
    )
    assert resp.status_code == 422


def test_list_pagination_window(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    for seats in (10, 20, 30):
        _win(db, owner, _make_deal(db, owner, customer, seats=seats))

    svc = _svc(db, owner)
    page = svc.list_onboardings(PaginationParams(page=1, per_page=2))
    assert len(page.items) == 2
    assert page.metadata.has_next is True
    page2 = svc.list_onboardings(PaginationParams(page=2, per_page=2))
    assert len(page2.items) == 1
    assert page2.metadata.has_next is False
