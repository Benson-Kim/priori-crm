"""Dual USD/KES customer billing profiles (issue #43).

Covers:
- seed defaults (settings_defaults parity with the deal-desk prototype);
- atomic creation of BOTH profiles on customer create, with collision-proof
  codes (slug + monotonic reference_sequences suffix);
- the one-off backfill helper the migration runs (defaults, idempotency,
  high-water-mark advancement);
- edit-resets-sync semantics with the profile's own optimistic-lock version;
- both sync endpoints (per-currency and sync-all) end to end;
- duplicate-currency rejection (UNIQUE(customer_id, currency));
- the ?unsynced=true hygiene list filter;
- audit trail rows for every profile mutation.
"""

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.common.audit import AuditEvent
from app.common.dependencies import get_current_user
from app.common.exceptions import ConflictException, NotFoundException
from app.common.pagination import PaginationParams
from app.common.reference_sequence import ReferenceSequence
from app.constants.enums import (
    BillingCurrency,
    CustomerType,
    TaxTreatment,
    TaxType,
    UserRole,
)
from app.constants.settings_defaults import DEFAULT_BILLING_PROFILE_DEFAULTS
from app.main import app
from app.modules.customers.models import Customer, CustomerBillingProfile
from app.modules.customers.profile_backfill import (
    PROFILE_CODE_SCOPE_KEY,
    backfill_billing_profiles,
)
from app.modules.customers.schemas import (
    BillingProfileUpdate,
    CustomerCreate,
)
from app.modules.customers.service import CustomerService

API = "/api/v1/customers"


def _create_payload(**overrides) -> CustomerCreate:
    base = dict(
        customer_type=CustomerType.BUSINESS,
        company_name="Baraka Logistics",
        first_name="Alice",
        last_name="Wanjiru",
        email=f"{uuid4().hex[:10]}@baraka.co.ke",
        phone="+254720114220",
        country="KE",
    )
    base.update(overrides)
    return CustomerCreate(**base)


def _raw_customer(db, company_name: str) -> Customer:
    """Insert a customer directly (no service) — i.e. no billing profiles."""
    customer = Customer(
        customer_type=CustomerType.BUSINESS,
        company_name=company_name,
        first_name="Ada",
        last_name="Lovelace",
        email=f"{uuid4().hex[:10]}@legacy.test",
        phone="+254700000000",
    )
    db.add(customer)
    db.flush()
    return customer


def _profiles_of(db, customer_id) -> dict[str, CustomerBillingProfile]:
    rows = (
        db.query(CustomerBillingProfile)
        .filter(CustomerBillingProfile.customer_id == customer_id)
        .all()
    )
    return {p.currency: p for p in rows}


def _install_actor():
    actor = SimpleNamespace(id=uuid4(), role=UserRole.ADMIN)
    app.dependency_overrides[get_current_user] = lambda: actor
    return actor


def _remove_actor():
    app.dependency_overrides.pop(get_current_user, None)


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------


def test_seed_defaults_match_prototype_and_avoid_floats():
    usd = DEFAULT_BILLING_PROFILE_DEFAULTS["USD"]
    kes = DEFAULT_BILLING_PROFILE_DEFAULTS["KES"]
    assert usd == {
        "payment_terms": "30 days",
        "tax_treatment": "Zero-rated (export)",
        "credit_limit": "25000.00",
    }
    assert kes == {
        "payment_terms": "14 days",
        "tax_treatment": "VAT 16%",
        "credit_limit": "10000.00",
    }
    # Decimal strings, never floats: exact construction must round-trip.
    assert Decimal(usd["credit_limit"]) == Decimal("25000.00")
    assert Decimal(kes["credit_limit"]) == Decimal("10000.00")


def test_tax_treatments_map_onto_existing_tax_types():
    assert TaxTreatment.VAT_16.tax_type is TaxType.VAT_16
    assert TaxTreatment.ZERO_RATED_EXPORT.tax_type is TaxType.VAT_0
    assert TaxTreatment.EXEMPT.tax_type is TaxType.EXEMPT


def test_create_customer_creates_both_profiles_with_defaults(db):
    svc = CustomerService(db)
    customer = svc.create(_create_payload())

    profiles = _profiles_of(db, customer.id)
    assert set(profiles) == {"USD", "KES"}

    usd, kes = profiles["USD"], profiles["KES"]
    assert usd.payment_terms == "30 days"
    assert usd.tax_treatment == "Zero-rated (export)"
    assert usd.credit_limit == Decimal("25000.00")
    assert kes.payment_terms == "14 days"
    assert kes.tax_treatment == "VAT 16%"
    assert kes.credit_limit == Decimal("10000.00")

    for profile in (usd, kes):
        assert profile.synced is False
        assert profile.synced_at is None
        assert profile.version == 1

    # Codes share one suffix and differ only in the currency segment.
    assert usd.code.startswith("BAR-")
    assert usd.code.endswith("-USD")
    assert kes.code == usd.code.replace("-USD", "-KES")

    # Profile creation is audited.
    audited = {
        e.entity_id
        for e in db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "customer_billing_profile",
            AuditEvent.action == "created",
        )
        .all()
    }
    assert {usd.id, kes.id} <= audited


def test_profile_codes_do_not_collide_for_same_slug(db):
    """'Acme Ltd' and 'Acme Group' both slug to ACM — codes must differ."""
    svc = CustomerService(db)
    first = svc.create(_create_payload(company_name="Acme Ltd"))
    second = svc.create(_create_payload(company_name="Acme Group"))

    first_usd = _profiles_of(db, first.id)["USD"]
    second_usd = _profiles_of(db, second.id)["USD"]
    assert first_usd.code.startswith("ACM-")
    assert second_usd.code.startswith("ACM-")
    assert first_usd.code != second_usd.code


def test_customer_create_persists_desk_fields(db):
    svc = CustomerService(db)
    customer = svc.create(
        _create_payload(
            industry="Logistics & transport",
            tenant_domain="barakalogistics.onmicrosoft.com",
            primary_currency=BillingCurrency.USD,
        )
    )
    assert customer.industry == "Logistics & transport"
    assert customer.tenant_domain == "barakalogistics.onmicrosoft.com"
    assert customer.primary_currency == "USD"


# --------------------------------------------------------------------------
# Duplicate currency rejection
# --------------------------------------------------------------------------


def test_duplicate_currency_profile_rejected(db):
    svc = CustomerService(db)
    customer = svc.create(_create_payload())

    duplicate = CustomerBillingProfile(
        customer_id=customer.id,
        currency="USD",
        code="DUP-9999-USD",
        payment_terms="30 days",
        tax_treatment="Exempt",
        credit_limit=Decimal("1.00"),
        synced=False,
    )
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# --------------------------------------------------------------------------
# Backfill (what the migration runs)
# --------------------------------------------------------------------------


def test_backfill_creates_both_profiles_for_existing_customers(db):
    legacy_one = _raw_customer(db, "Savannah Microfinance")
    legacy_two = _raw_customer(db, "Nairobi Dental Group")
    assert _profiles_of(db, legacy_one.id) == {}

    backfilled = backfill_billing_profiles(db.connection())
    assert backfilled == 2

    for legacy in (legacy_one, legacy_two):
        profiles = _profiles_of(db, legacy.id)
        assert set(profiles) == {"USD", "KES"}
        assert profiles["USD"].payment_terms == "30 days"
        assert profiles["USD"].tax_treatment == "Zero-rated (export)"
        assert profiles["USD"].credit_limit == Decimal("25000.00")
        assert profiles["KES"].payment_terms == "14 days"
        assert profiles["KES"].tax_treatment == "VAT 16%"
        assert profiles["KES"].credit_limit == Decimal("10000.00")
        assert profiles["USD"].synced is False

    codes = [
        p.code
        for legacy in (legacy_one, legacy_two)
        for p in _profiles_of(db, legacy.id).values()
    ]
    assert len(codes) == len(set(codes))

    # High-water mark advanced: one suffix per backfilled customer.
    sequence = db.get(ReferenceSequence, PROFILE_CODE_SCOPE_KEY)
    assert sequence is not None
    assert sequence.last_value == 2


def test_backfill_is_idempotent_and_skips_profiled_customers(db):
    svc = CustomerService(db)
    svc.create(_create_payload())  # already owns both profiles
    _raw_customer(db, "Legacy Co")

    assert backfill_billing_profiles(db.connection()) == 1
    assert backfill_billing_profiles(db.connection()) == 0
    assert db.query(CustomerBillingProfile).count() == 4


def test_live_generation_continues_after_backfill_without_reuse(db):
    _raw_customer(db, "Legacy Co")
    backfill_billing_profiles(db.connection())

    svc = CustomerService(db)
    fresh = svc.create(_create_payload(company_name="Legacy Co"))
    fresh_codes = {p.code for p in _profiles_of(db, fresh.id).values()}

    all_codes = [c for (c,) in db.query(CustomerBillingProfile.code).all()]
    assert len(all_codes) == len(set(all_codes))
    # The live suffix continues past the backfilled one (0001 -> 0002).
    assert fresh_codes == {"LEG-0002-USD", "LEG-0002-KES"}


# --------------------------------------------------------------------------
# Edit resets sync + optimistic locking
# --------------------------------------------------------------------------


def test_edit_resets_sync_and_bumps_version(db):
    svc = CustomerService(db)
    customer = svc.create(_create_payload())
    synced = svc.sync_profile(customer.id, "USD")
    assert synced.synced is True
    version_after_sync = synced.version

    updated = svc.update_profile(
        customer.id,
        "USD",
        BillingProfileUpdate(credit_limit=Decimal("30000.00")),
        expected_version=version_after_sync,
    )
    assert updated.synced is False
    assert updated.credit_limit == Decimal("30000.00")
    assert updated.version == version_after_sync + 1
    # synced_at is evidence of the last push and survives the edit.
    assert updated.synced_at is not None

    events = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "customer_billing_profile",
            AuditEvent.entity_id == updated.id,
            AuditEvent.action == "updated",
        )
        .all()
    )
    assert len(events) == 1
    assert events[0].before["synced"] is True
    assert events[0].after["synced"] is False


def test_stale_profile_version_rejected(db):
    svc = CustomerService(db)
    customer = svc.create(_create_payload())
    profile = svc.update_profile(
        customer.id,
        "KES",
        BillingProfileUpdate(payment_terms="30 days"),
        expected_version=1,
    )
    assert profile.version == 2

    with pytest.raises(ConflictException):
        svc.update_profile(
            customer.id,
            "KES",
            BillingProfileUpdate(payment_terms="45 days"),
            expected_version=1,  # stale
        )


def test_unknown_profile_currency_raises_not_found(db):
    svc = CustomerService(db)
    customer = svc.create(_create_payload())
    # EUR is a reference currency only — no profile row exists for it.
    with pytest.raises(NotFoundException):
        svc.sync_profile(customer.id, "EUR")


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


def test_patch_profile_endpoint_edits_and_resets_sync(client, db):
    _install_actor()
    try:
        svc = CustomerService(db)
        customer = svc.create(_create_payload())
        svc.sync_profile(customer.id, "USD")
        db.commit()

        response = client.patch(
            f"{API}/{customer.id}/profiles/USD",
            params={"expectedVersion": 2},
            json={"paymentTerms": "60 days"},
        )
    finally:
        _remove_actor()

    assert response.status_code == 200
    body = response.json()
    assert body["payment_terms"] == "60 days"
    assert body["synced"] is False
    assert body["version"] == 3


def test_patch_profile_endpoint_rejects_invalid_values(client, db):
    _install_actor()
    try:
        svc = CustomerService(db)
        customer = svc.create(_create_payload())
        db.commit()

        bad_terms = client.patch(
            f"{API}/{customer.id}/profiles/USD",
            params={"expectedVersion": 1},
            json={"paymentTerms": "90 days"},
        )
        bad_currency = client.patch(
            f"{API}/{customer.id}/profiles/EUR",
            params={"expectedVersion": 1},
            json={"paymentTerms": "30 days"},
        )
    finally:
        _remove_actor()

    assert bad_terms.status_code == 422
    assert bad_currency.status_code == 422


def test_sync_profile_endpoint_marks_profile_synced(client, db):
    _install_actor()
    try:
        svc = CustomerService(db)
        customer = svc.create(_create_payload())
        db.commit()

        response = client.post(f"{API}/{customer.id}/profiles/KES/sync")
    finally:
        _remove_actor()

    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "KES"
    assert body["synced"] is True
    assert body["synced_at"] is not None

    profile = _profiles_of(db, customer.id)["KES"]
    db.refresh(profile)
    assert profile.synced is True

    sync_events = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_type == "customer_billing_profile",
            AuditEvent.entity_id == profile.id,
            AuditEvent.action == "synced",
        )
        .count()
    )
    assert sync_events == 1


def test_sync_all_endpoint_marks_both_profiles_synced(client, db):
    _install_actor()
    try:
        svc = CustomerService(db)
        customer = svc.create(_create_payload())
        db.commit()

        response = client.post(f"{API}/{customer.id}/profiles/sync-all")
    finally:
        _remove_actor()

    assert response.status_code == 200
    body = response.json()
    assert {p["currency"] for p in body} == {"USD", "KES"}
    assert all(p["synced"] for p in body)
    assert all(p["synced_at"] is not None for p in body)


def test_sync_endpoints_404_for_missing_customer(client):
    _install_actor()
    try:
        missing = uuid4()
        single = client.post(f"{API}/{missing}/profiles/USD/sync")
        both = client.post(f"{API}/{missing}/profiles/sync-all")
    finally:
        _remove_actor()

    assert single.status_code == 404
    assert both.status_code == 404


# --------------------------------------------------------------------------
# Unsynced hygiene filter
# --------------------------------------------------------------------------


def test_unsynced_filter_lists_only_customers_with_unsynced_profiles(db):
    svc = CustomerService(db)
    clean = svc.create(_create_payload(company_name="Fully Synced Ltd"))
    dirty = svc.create(_create_payload(company_name="Half Synced Ltd"))
    svc.sync_all_profiles(clean.id)
    svc.sync_profile(dirty.id, "USD")  # KES profile remains unsynced

    result = svc.list_customers(PaginationParams(page=1, per_page=50), unsynced=True)
    ids = {item.id for item in result.items}
    assert dirty.id in ids
    assert clean.id not in ids

    # Default listing is unaffected.
    everyone = svc.list_customers(PaginationParams(page=1, per_page=50))
    assert {clean.id, dirty.id} <= {item.id for item in everyone.items}


def test_unsynced_filter_via_endpoint(client, db):
    _install_actor()
    try:
        svc = CustomerService(db)
        clean = svc.create(_create_payload(company_name="Fully Synced Ltd"))
        dirty = svc.create(_create_payload(company_name="Half Synced Ltd"))
        svc.sync_all_profiles(clean.id)
        db.commit()

        response = client.get(API, params={"unsynced": "true", "per_page": 50})
    finally:
        _remove_actor()

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert str(dirty.id) in ids
    assert str(clean.id) not in ids
