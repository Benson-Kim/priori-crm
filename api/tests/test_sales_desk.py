"""Sales Desk analytics backend (issue #45).

Covers:
- dashboard empty state (zeroed KPIs, 12 zero-filled months, empty panels);
- KPI / stage-panel math against the Dashboard.svg design numbers;
- parked deals excluded from ALL open aggregates (dashboard + overview);
- won/lost this period (current org-local quarter) + rep-vs-quota panel
  using the NEW quarterly-target owner setting;
- stage probabilities configurable via owner settings (and consistent with
  the deal responses' weighted_value);
- bookings rolling 12-month window: labels, org-local close-date grouping,
  USD conversion;
- owner filtering across dashboard and notifications;
- notifications: grouped counts and bell total == sum of groups;
- pipeline workspace aggregates (App.svg): rep scoreboard, team card,
  stage strip, closed column, hygiene buckets, open_pipeline_total;
- CSV exports: columns, active filters honoured, server-side escaping
  (formula-injection neutralised), preflight row limits, temp-file cleanup
  on success AND on every failure path, fail-closed same-session audit
  (audit failure blocks the response and removes the file);
- the read-snapshot dependency (REPEATABLE READ + READ ONLY on PostgreSQL)
  and snapshot consistency under concurrent writes (PostgreSQL-marked).
"""

import csv
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.modules.sales_desk.audit as sales_desk_audit
import app.modules.sales_desk.router as sales_desk_router
from app.common.audit import AuditEvent
from app.common.dependencies import get_current_user, get_sales_desk_service
from app.common.fx import usd_equivalent
from app.common.reporting_time import reporting_date, reporting_timezone
from app.constants.enums import (
    CustomerType,
    DealLostReason,
    DealStage,
    DealStatus,
    DealWonReason,
    UserRole,
)
from app.lib.config import settings
from app.main import app
from app.modules.auth.models import User
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import CustomerService
from app.modules.deals.schemas import DealCreate
from app.modules.deals.service import DealService
from app.modules.nurture.schemas import NurtureProspectCreate
from app.modules.nurture.service import NurtureService
from app.modules.owner.schemas import OwnerProfileUpdate
from app.modules.owner.service import OwnerService
from app.modules.sales_desk.service import (
    PIPELINE_EXPORT_COLUMNS,
    SalesDeskService,
)

API = "/api/v1/sales-desk"


# Helpers


def _make_owner(db, first="Tabitha", last="Kimani") -> User:
    user = User(
        email=f"{uuid4().hex[:10]}@sales.test",
        password_hash="x",
        first_name=first,
        last_name=last,
        role=UserRole.MEMBER,
    )
    db.add(user)
    db.flush()
    return user


def _make_customer(db, name="Baraka Logistics", owner=None):
    """Create a customer through the service so both billing profiles exist."""
    svc = CustomerService(db)
    customer = svc.create(
        CustomerCreate(
            customer_type=CustomerType.BUSINESS,
            company_name=name,
            first_name="Alice",
            last_name="Wanjiru",
            email=f"{uuid4().hex[:10]}@baraka.co.ke",
            phone="+254720114220",
            country="KE",
        )
    )
    if owner is not None:
        customer.owner_id = owner.id
        db.flush()
    return customer


def _deal_svc(db, owner) -> DealService:
    return DealService(db, current_user=owner)


def _make_deal(db, owner, customer, **overrides):
    data = dict(
        customer_id=customer.id,
        owner_id=owner.id,
        product="Microsoft 365 Business Premium",
        seats=45,
        value=Decimal("11880.00"),
        currency="USD",
    )
    data.update(overrides)
    return _deal_svc(db, owner).create(DealCreate(**data), user_id=owner.id)


def _advance_to(db, owner, deal, target: DealStage):
    svc = _deal_svc(db, owner)
    while DealStage(deal.stage) != target:
        deal = svc.advance_stage(deal.id, f"Advance toward {target.value}.")
    return deal


def _set_activity_days_ago(db, deal, days: int) -> None:
    """Backdate every activity record of a deal (age/idle fixtures)."""
    target = reporting_date() - timedelta(days=days)
    for activity in deal.activities:
        activity.activity_date = target
    db.flush()


def _mark_synced(db, customer) -> None:
    for profile in customer.billing_profiles:
        profile.synced = True
    db.flush()


def _svc(db) -> SalesDeskService:
    return SalesDeskService(db)


def _install_actor(user) -> None:
    actor = SimpleNamespace(id=user.id, role=UserRole.ADMIN)
    app.dependency_overrides[get_current_user] = lambda: actor


def _clear_actor() -> None:
    app.dependency_overrides.pop(get_current_user, None)


def _parse_csv(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


# Dashboard


def test_dashboard_empty_state(db):
    dashboard = _svc(db).get_dashboard()

    assert dashboard.currency == "USD"
    kpis = dashboard.kpis
    assert kpis.pipeline_weighted.value == Decimal("0.00")
    assert kpis.pipeline_weighted.open_deal_count == 0
    assert kpis.total_arr_pipeline.value == Decimal("0.00")
    assert kpis.won_this_period.value == Decimal("0.00")
    assert kpis.won_this_period.deal_count == 0
    assert kpis.lost_this_period.value == Decimal("0.00")
    assert kpis.lost_this_period.deal_count == 0

    assert len(dashboard.bookings_12_months) == 12
    assert all(
        month.won_value == Decimal("0.00") and month.lost_value == Decimal("0.00")
        for month in dashboard.bookings_12_months
    )

    assert len(dashboard.pipeline_by_stage) == 4
    assert all(
        line.value == Decimal("0.00") and line.deal_count == 0 and line.share == 0.0
        for line in dashboard.pipeline_by_stage
    )
    assert dashboard.rep_quota == []
    assert dashboard.recent_companies == []


def test_dashboard_kpis_match_design_numbers(db):
    """Dashboard.svg: $46,530 weighted / $72,180 total over the 4 stages."""
    owner = _make_owner(db)
    customer = _make_customer(db)

    _make_deal(db, owner, customer, value=Decimal("2700.00"))
    q = _make_deal(db, owner, customer, value=Decimal("5760.00"))
    _advance_to(db, owner, q, DealStage.QUALIFICATION)
    p = _make_deal(db, owner, customer, value=Decimal("11880.00"))
    _advance_to(db, owner, p, DealStage.PROPOSAL_QUOTE)
    n = _make_deal(db, owner, customer, value=Decimal("51840.00"))
    _advance_to(db, owner, n, DealStage.NEGOTIATION)

    dashboard = _svc(db).get_dashboard()

    assert dashboard.kpis.pipeline_weighted.value == Decimal("46530.00")
    assert dashboard.kpis.pipeline_weighted.open_deal_count == 4
    assert dashboard.kpis.total_arr_pipeline.value == Decimal("72180.00")

    by_stage = {line.stage: line for line in dashboard.pipeline_by_stage}
    assert by_stage[DealStage.ACTIVATION].value == Decimal("2700.00")
    assert by_stage[DealStage.ACTIVATION].deal_count == 1
    assert by_stage[DealStage.ACTIVATION].stage_label == "Activation"
    assert by_stage[DealStage.NEGOTIATION].value == Decimal("51840.00")
    assert by_stage[DealStage.NEGOTIATION].share == pytest.approx(
        51840 / 72180, abs=1e-4
    )
    assert sum(line.deal_count for line in dashboard.pipeline_by_stage) == 4


def test_dashboard_converts_kes_values_to_usd(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    _make_deal(db, owner, customer, value=Decimal("129500.00"), currency="KES")

    dashboard = _svc(db).get_dashboard()

    assert dashboard.kpis.total_arr_pipeline.value == Decimal("1000.00")
    assert dashboard.kpis.pipeline_weighted.value == Decimal("100.00")


def test_parked_deals_excluded_from_open_aggregates(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    _make_deal(db, owner, customer, value=Decimal("1000.00"))
    parked = _make_deal(db, owner, customer, value=Decimal("9000.00"))
    _deal_svc(db, owner).park(
        parked.id, reporting_date() + timedelta(days=30), "Budget frozen."
    )

    service = _svc(db)
    dashboard = service.get_dashboard()
    assert dashboard.kpis.pipeline_weighted.open_deal_count == 1
    assert dashboard.kpis.total_arr_pipeline.value == Decimal("1000.00")
    assert dashboard.kpis.pipeline_weighted.value == Decimal("100.00")
    assert sum(line.deal_count for line in dashboard.pipeline_by_stage) == 1

    overview = service.get_pipeline_overview()
    assert overview.open_pipeline_total == Decimal("1000.00")
    assert overview.team.open_count == 1
    assert sum(column.count for column in overview.stages) == 1
    # Parked still counts toward the 'All deals' chip (any status).
    assert overview.hygiene.all == 2


def test_won_lost_this_period_and_rep_quota(db):
    owner = _make_owner(db)
    other = _make_owner(db, first="Michael", last="Wafula")
    customer = _make_customer(db)

    won = _make_deal(db, owner, customer, value=Decimal("136800.00"))
    _advance_to(db, owner, won, DealStage.NEGOTIATION)
    _deal_svc(db, owner).close(
        won.id, "won", DealWonReason.PRODUCT_FIT.value, "Signed."
    )
    lost = _make_deal(db, other, customer, value=Decimal("5250.00"))
    _deal_svc(db, other).close(
        lost.id, "lost", DealLostReason.PRICE_TOO_HIGH.value, "Too expensive."
    )

    # Configure a quarterly target for `owner`; `other` uses the default.
    OwnerService(db).update(
        OwnerProfileUpdate(
            full_name="Priori Technologies",
            rep_quarterly_targets={str(owner.id): "90000.00"},
        )
    )

    dashboard = _svc(db).get_dashboard()
    assert dashboard.kpis.won_this_period.value == Decimal("136800.00")
    assert dashboard.kpis.won_this_period.deal_count == 1
    assert dashboard.kpis.lost_this_period.value == Decimal("5250.00")
    assert dashboard.kpis.lost_this_period.deal_count == 1

    lines = {line.user.id: line for line in dashboard.rep_quota}
    assert set(lines) == {owner.id, other.id}
    assert lines[owner.id].won_value == Decimal("136800.00")
    assert lines[owner.id].quota == Decimal("90000.00")
    assert lines[owner.id].percent == pytest.approx(136800 / 90000, abs=1e-4)
    assert lines[other.id].won_value == Decimal("0.00")
    assert lines[other.id].quota == Decimal("90000.00")  # built-in default
    assert lines[other.id].percent == 0.0
    assert lines[owner.id].user.initials == "TK"


def test_stage_probabilities_setting_drives_weighted_values(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer, value=Decimal("1000.00"))

    OwnerService(db).update(
        OwnerProfileUpdate(
            full_name="Priori Technologies",
            deal_stage_probabilities={
                "activation": "0.20",
                "qualification": "0.40",
                "proposal_quote": "0.60",
                "negotiation": "0.80",
            },
        )
    )

    dashboard = _svc(db).get_dashboard()
    assert dashboard.kpis.pipeline_weighted.value == Decimal("200.00")

    # The deal response derives its weighted_value from the SAME setting.
    response = _deal_svc(db, owner).build_response(deal)
    assert response.weighted_value == Decimal("200.00")


def test_bookings_rolling_window_labels_and_close_date_grouping(db):
    owner = _make_owner(db)
    customer = _make_customer(db)

    recent = _make_deal(db, owner, customer, value=Decimal("1295.00"), currency="KES")
    _deal_svc(db, owner).close(
        recent.id, "won", DealWonReason.PRODUCT_FIT.value, "Signed."
    )
    older = _make_deal(db, owner, customer, value=Decimal("500.00"))
    _deal_svc(db, owner).close(
        older.id, "lost", DealLostReason.PRICE_TOO_HIGH.value, "Too expensive."
    )
    older_closed_at = datetime.now(UTC) - timedelta(days=62)
    older.closed_at = older_closed_at
    db.flush()

    dashboard = _svc(db).get_dashboard()
    months = dashboard.bookings_12_months
    assert len(months) == 12

    today = reporting_date()
    assert months[-1].label == today.strftime("%b")
    assert months[-1].won_value == usd_equivalent(Decimal("1295.00"), "KES")
    assert months[-1].lost_value == Decimal("0.00")

    older_local = older_closed_at.astimezone(reporting_timezone()).date()
    older_line = next(m for m in months if m.label == older_local.strftime("%b"))
    assert older_line.lost_value == Decimal("500.00")

    total_lost = sum((m.lost_value for m in months), Decimal("0"))
    assert total_lost == Decimal("500.00")


def test_dashboard_owner_filter(db):
    owner = _make_owner(db)
    other = _make_owner(db, first="Joy", last="Nduta")
    customer = _make_customer(db, owner=owner)
    _make_deal(db, owner, customer, value=Decimal("1000.00"))
    _make_deal(db, other, customer, value=Decimal("4000.00"))

    dashboard = _svc(db).get_dashboard(owner.id)
    assert dashboard.kpis.total_arr_pipeline.value == Decimal("1000.00")
    assert dashboard.kpis.pipeline_weighted.open_deal_count == 1
    assert [line.user.id for line in dashboard.rep_quota] == [owner.id]
    # Companies are owner-filtered through the account owner.
    assert [c.name for c in dashboard.recent_companies] == [customer.company_name]


def test_recent_companies_panel(db):
    older = _make_customer(db, name="Savannah Microfinance")
    older.created_at = datetime.now(UTC) - timedelta(days=30)
    db.flush()
    newest = _make_customer(db, name="Baraka Logistics")

    companies = _svc(db).get_dashboard().recent_companies
    assert [c.name for c in companies] == ["Baraka Logistics", "Savannah Microfinance"]
    assert companies[0].contact == "Alice Wanjiru"
    assert companies[0].currency == (newest.primary_currency or newest.currency)
    assert companies[0].created_date == reporting_date()


# Notifications


def test_notifications_empty_state(db):
    counts = _svc(db).get_notifications()
    assert counts.total == 0
    assert counts.companies_unsynced == 0
    assert counts.future_pipeline_due == 0
    assert counts.upcoming == 0
    assert counts.stale_deals == 0


def test_notifications_groups_and_bell_total(db):
    owner = _make_owner(db)
    # Two companies: one left unsynced (both profiles start unsynced), one
    # fully synced — companies_unsynced counts companies, not profiles.
    unsynced_customer = _make_customer(db, name="Unsynced Co")
    synced_customer = _make_customer(db, name="Synced Co")
    _mark_synced(db, synced_customer)

    nurture = NurtureService(db)
    due_prospect = nurture.create(
        NurtureProspectCreate(company="Due Nurture Ltd", owner_id=owner.id)
    )
    due_prospect.engage_on = reporting_date()
    upcoming_prospect = nurture.create(
        NurtureProspectCreate(company="Upcoming Nurture Ltd", owner_id=owner.id)
    )
    upcoming_prospect.engage_on = reporting_date() + timedelta(days=7)
    db.flush()

    parked_due = _make_deal(db, owner, unsynced_customer, value=Decimal("100.00"))
    _deal_svc(db, owner).park(
        parked_due.id, reporting_date() + timedelta(days=30), "Wait for budget."
    )
    parked_due.parked_until = reporting_date() - timedelta(days=1)
    db.flush()

    stale = _make_deal(db, owner, unsynced_customer, value=Decimal("200.00"))
    _set_activity_days_ago(db, stale, 40)
    fresh = _make_deal(db, owner, unsynced_customer, value=Decimal("300.00"))
    assert fresh.status == DealStatus.OPEN

    counts = _svc(db).get_notifications()
    assert counts.companies_unsynced == 1
    assert counts.future_pipeline_due == 2  # due nurture + due parked
    assert counts.upcoming == 1
    assert counts.stale_deals == 1
    assert counts.total == 5  # Dashboard.svg bell badge
    assert counts.total == (
        counts.companies_unsynced
        + counts.future_pipeline_due
        + counts.upcoming
        + counts.stale_deals
    )


def test_notifications_owner_filter(db):
    owner = _make_owner(db)
    other = _make_owner(db, first="Joy", last="Nduta")
    mine = _make_customer(db, name="Mine Co", owner=owner)
    _make_customer(db, name="Theirs Co", owner=other)

    stale_mine = _make_deal(db, owner, mine, value=Decimal("100.00"))
    _set_activity_days_ago(db, stale_mine, 40)
    stale_theirs = _make_deal(db, other, mine, value=Decimal("100.00"))
    _set_activity_days_ago(db, stale_theirs, 40)

    counts = _svc(db).get_notifications(owner.id)
    assert counts.companies_unsynced == 1
    assert counts.stale_deals == 1
    assert counts.total == 2


# Pipeline workspace aggregates (App.svg)


def test_pipeline_overview_empty_state(db):
    overview = _svc(db).get_pipeline_overview()
    assert overview.reps == []
    assert overview.team.open_count == 0
    assert overview.team.win_rate is None
    assert overview.closed.count == 0
    assert overview.closed.win_rate is None
    assert overview.open_pipeline_total == Decimal("0.00")
    assert overview.hygiene.all == 0


def test_pipeline_overview_aggregates(db):
    owner = _make_owner(db)  # Tabitha Kimani
    other = _make_owner(db, first="Joy", last="Nduta")
    customer = _make_customer(db)

    fresh = _make_deal(db, owner, customer, value=Decimal("2700.00"))
    assert DealStage(fresh.stage) == DealStage.ACTIVATION
    quiet = _make_deal(db, owner, customer, value=Decimal("5760.00"))
    _advance_to(db, owner, quiet, DealStage.QUALIFICATION)
    _set_activity_days_ago(db, quiet, 10)
    cold_old = _make_deal(db, other, customer, value=Decimal("11880.00"))
    _advance_to(db, other, cold_old, DealStage.PROPOSAL_QUOTE)
    _set_activity_days_ago(db, cold_old, 50)

    won = _make_deal(db, owner, customer, value=Decimal("136800.00"))
    _deal_svc(db, owner).close(
        won.id, "won", DealWonReason.PRODUCT_FIT.value, "Signed."
    )
    lost = _make_deal(db, other, customer, value=Decimal("5250.00"))
    _deal_svc(db, other).close(
        lost.id, "lost", DealLostReason.PRICE_TOO_HIGH.value, "Too expensive."
    )

    overview = _svc(db).get_pipeline_overview()

    cards = {card.user.id: card for card in overview.reps}
    assert set(cards) == {owner.id, other.id}
    assert cards[owner.id].open_count == 2
    assert cards[owner.id].open_value == Decimal("8460.00")
    assert cards[owner.id].win_rate == 1.0
    assert cards[owner.id].cold_count == 0
    assert cards[other.id].open_count == 1
    assert cards[other.id].win_rate == 0.0
    assert cards[other.id].cold_count == 1

    assert overview.team.open_count == 3
    assert overview.team.open_value == Decimal("20340.00")
    assert overview.team.win_rate == 0.5
    assert overview.team.cold_count == 1

    strip = {column.stage: column for column in overview.stages}
    assert strip[DealStage.ACTIVATION].count == 1
    assert strip[DealStage.ACTIVATION].avg_days_in_stage == 0
    assert strip[DealStage.QUALIFICATION].count == 1
    assert strip[DealStage.QUALIFICATION].avg_days_in_stage == 10
    assert strip[DealStage.PROPOSAL_QUOTE].avg_days_in_stage == 50
    assert strip[DealStage.NEGOTIATION].count == 0

    assert overview.closed.count == 2
    assert overview.closed.won_count == 1
    assert overview.closed.lost_count == 1
    assert overview.closed.win_rate == 0.5

    assert overview.hygiene.all == 5
    assert overview.hygiene.active_week == 1
    assert overview.hygiene.quiet_8_30 == 1
    assert overview.hygiene.no_activity_30 == 1
    assert overview.hygiene.open_45 == 1

    assert overview.open_pipeline_total == Decimal("20340.00")


def test_pipeline_overview_owner_scoping(db):
    """Rep/team cards stay team-wide; strip + totals honour the filter."""
    owner = _make_owner(db)
    other = _make_owner(db, first="Joy", last="Nduta")
    customer = _make_customer(db)
    _make_deal(db, owner, customer, value=Decimal("1000.00"))
    _make_deal(db, other, customer, value=Decimal("4000.00"))

    overview = _svc(db).get_pipeline_overview(owner.id)
    assert {card.user.id for card in overview.reps} == {owner.id, other.id}
    assert overview.team.open_count == 2
    assert overview.open_pipeline_total == Decimal("1000.00")
    assert overview.hygiene.all == 1
    assert sum(column.count for column in overview.stages) == 1


# Read-snapshot dependency


class _FakeDialect:
    name = "postgresql"


class _FakeBind:
    dialect = _FakeDialect()


class _FakeSession:
    def __init__(self) -> None:
        self.transaction_active = True
        self.commits = 0
        self.connection_options: list[dict] = []
        self.statements: list[str] = []

    def in_transaction(self) -> bool:
        return self.transaction_active

    def commit(self) -> None:
        self.commits += 1
        self.transaction_active = False

    def get_bind(self):
        return _FakeBind()

    def connection(self, *, execution_options: dict):
        self.connection_options.append(execution_options)
        self.transaction_active = True
        return self

    def execute(self, statement):
        self.statements.append(str(statement))
        return None


def test_sales_desk_service_starts_postgres_repeatable_read_snapshot():
    db = _FakeSession()
    service = get_sales_desk_service(db, SimpleNamespace())

    assert service.db is db
    assert db.commits == 1
    assert db.connection_options == [{"isolation_level": "REPEATABLE READ"}]
    assert db.statements == ["SET TRANSACTION READ ONLY"]


# CSV exports (route level)


def _export_artifact(monkeypatch, tmp_path, name: str):
    artifact = tmp_path / name
    monkeypatch.setattr(sales_desk_router, "_temporary_csv_path", lambda: str(artifact))
    return artifact


def test_pipeline_export_csv_success(client, db, monkeypatch, tmp_path):
    artifact = _export_artifact(monkeypatch, tmp_path, "pipeline.csv")
    owner = _make_owner(db)
    customer = _make_customer(db)
    open_deal = _make_deal(db, owner, customer, value=Decimal("11880.00"))
    closed = _make_deal(db, owner, customer, value=Decimal("500.00"))
    _deal_svc(db, owner).close(
        closed.id, "lost", DealLostReason.PRICE_TOO_HIGH.value, "Too expensive."
    )

    _install_actor(owner)
    try:
        response = client.get(f"{API}/exports/pipeline", params={"tab": "open"})
    finally:
        _clear_actor()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert not artifact.exists()  # background cleanup ran

    rows = _parse_csv(response.text)
    assert rows[0] == PIPELINE_EXPORT_COLUMNS
    assert len(rows) == 2  # header + the single OPEN deal (filter honoured)
    data = dict(zip(PIPELINE_EXPORT_COLUMNS, rows[1], strict=True))
    assert data["Company"] == customer.company_name
    assert data["Contact"] == "Alice Wanjiru"
    assert data["Owner"] == "Tabitha Kimani"
    assert data["Seats"] == "45"
    assert data["Currency"] == "USD"
    assert data["Annual value"] == str(open_deal.value)
    assert data["Status"] == "open"
    assert data["Stage / Parked until"] == "Activation"
    assert data["Days in pipeline"] == "0"
    assert data["Days idle"] == "0"
    assert data["Close reason"] == ""
    assert data["Latest note"] == "Deal created."

    event = db.query(AuditEvent).filter(AuditEvent.action == "sales_desk_export").one()
    assert event.after["success"] is True
    assert event.after["export_type"] == "pipeline"
    assert event.after["filters"] == {"tab": "open"}
    assert event.after["export_metrics"] == {"exported_data_row_count": 1}


def test_pipeline_export_parked_until_column(client, db, monkeypatch, tmp_path):
    _export_artifact(monkeypatch, tmp_path, "parked.csv")
    owner = _make_owner(db)
    customer = _make_customer(db)
    parked = _make_deal(db, owner, customer, value=Decimal("100.00"))
    parked_until = reporting_date() + timedelta(days=30)
    _deal_svc(db, owner).park(parked.id, parked_until, "Budget frozen.")

    _install_actor(owner)
    try:
        response = client.get(f"{API}/exports/pipeline")
    finally:
        _clear_actor()

    rows = _parse_csv(response.text)
    data = dict(zip(PIPELINE_EXPORT_COLUMNS, rows[1], strict=True))
    assert data["Status"] == "parked"
    assert data["Stage / Parked until"] == f"Parked until {parked_until.isoformat()}"


def test_pipeline_export_escapes_csv_and_formula_injection(
    client, db, monkeypatch, tmp_path
):
    _export_artifact(monkeypatch, tmp_path, "escaped.csv")
    owner = _make_owner(db)
    customer = _make_customer(db, name='=HYPERLINK("http://evil.example")')
    deal = _make_deal(db, owner, customer, value=Decimal("100.00"))
    _deal_svc(db, owner).log_activity(
        deal.id, 'He said "call back", then hung up\nSecond line'
    )

    _install_actor(owner)
    try:
        response = client.get(f"{API}/exports/pipeline")
    finally:
        _clear_actor()

    rows = _parse_csv(response.text)
    data = dict(zip(PIPELINE_EXPORT_COLUMNS, rows[1], strict=True))
    # Formula neutralised with a leading apostrophe; structure fully intact.
    assert data["Company"] == "'=HYPERLINK(\"http://evil.example\")"
    assert data["Latest note"] == 'He said "call back", then hung up\nSecond line'


def test_pipeline_export_preflight_row_limit(client, db, monkeypatch, tmp_path):
    artifact = _export_artifact(monkeypatch, tmp_path, "limited.csv")
    monkeypatch.setattr(settings, "REPORT_EXPORT_MAX_ROWS", 1)
    owner = _make_owner(db)
    customer = _make_customer(db)
    _make_deal(db, owner, customer, value=Decimal("100.00"))
    _make_deal(db, owner, customer, value=Decimal("200.00"))

    _install_actor(owner)
    try:
        response = client.get(f"{API}/exports/pipeline")
    finally:
        _clear_actor()

    assert response.status_code == 400
    assert "more than 1 rows" in response.text
    assert not artifact.exists()  # cleaned up on the failure path

    event = db.query(AuditEvent).filter(AuditEvent.action == "sales_desk_export").one()
    assert event.after["success"] is False
    assert event.after["failure"] == "BadRequestException"


def test_export_audit_failure_blocks_response_and_removes_file(
    client, db, monkeypatch, tmp_path
):
    artifact = _export_artifact(monkeypatch, tmp_path, "audit-failed.csv")

    def fail_audit(*args, **kwargs):
        raise OSError("audit down")

    monkeypatch.setattr(sales_desk_audit, "_append_audit", fail_audit)
    owner = _make_owner(db)
    customer = _make_customer(db)
    _make_deal(db, owner, customer, value=Decimal("100.00"))

    _install_actor(owner)
    try:
        with pytest.raises(OSError, match="audit down"):
            client.get(f"{API}/exports/pipeline")
    finally:
        _clear_actor()

    assert not artifact.exists()
    # Fail-closed: nothing half-written survives in the audit trail.
    assert (
        db.query(AuditEvent).filter(AuditEvent.action == "sales_desk_export").count()
        == 0
    )


def test_dashboard_export_csv(client, db, monkeypatch, tmp_path):
    artifact = _export_artifact(monkeypatch, tmp_path, "dashboard.csv")
    owner = _make_owner(db)
    customer = _make_customer(db)
    _make_deal(db, owner, customer, value=Decimal("2700.00"))

    _install_actor(owner)
    try:
        response = client.get(f"{API}/exports/dashboard")
    finally:
        _clear_actor()

    assert response.status_code == 200
    assert not artifact.exists()

    rows = _parse_csv(response.text)
    sections = [row[0] for row in rows if row]
    assert "KPIs" in sections
    assert "Bookings — 12 months" in sections
    assert "Active pipeline by stage" in sections
    assert "Rep pipeline vs. quota" in sections
    assert "Recently added companies" in sections
    kpi_row = next(row for row in rows if row and row[0] == "Pipeline (weighted)")
    assert kpi_row[1] == "270.00"
    assert kpi_row[2] == "1"

    event = db.query(AuditEvent).filter(AuditEvent.action == "sales_desk_export").one()
    assert event.after["success"] is True
    assert event.after["export_type"] == "dashboard"
    assert event.after["export_metrics"] == {"exported_data_row_count": len(rows)}


# Endpoint smoke (routing + response models)


def test_dashboard_and_notifications_endpoints(client, db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    _make_deal(db, owner, customer, value=Decimal("2700.00"))

    _install_actor(owner)
    try:
        dashboard = client.get(f"{API}/dashboard")
        filtered = client.get(f"{API}/dashboard", params={"owner": str(owner.id)})
        notifications = client.get(f"{API}/notifications")
        overview = client.get(f"{API}/pipeline/overview")
    finally:
        _clear_actor()

    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["currency"] == "USD"
    weighted = body["kpis"]["pipeline_weighted"]
    assert Decimal(str(weighted["value"])) == Decimal("270.00")
    assert body["kpis"]["pipeline_weighted"]["open_deal_count"] == 1
    assert len(body["bookings_12_months"]) == 12
    assert filtered.status_code == 200

    assert notifications.status_code == 200
    counts = notifications.json()
    assert counts["total"] == (
        counts["companies_unsynced"]
        + counts["future_pipeline_due"]
        + counts["upcoming"]
        + counts["stale_deals"]
    )

    assert overview.status_code == 200
    total = overview.json()["open_pipeline_total"]
    assert Decimal(str(total)) == Decimal("2700.00")
