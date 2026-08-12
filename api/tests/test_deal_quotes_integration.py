"""Sales Desk <-> Quotes integration (issue #44).

Covers:
- seed defaults: the 8-SKU catalog verbatim, annual discount 15, FX
  conventions (decimal strings, no floats);
- prefill: POST /deals/{id}/quotes creates via the EXISTING quotes service —
  customer/currency from the deal + billing profile, catalog-derived line
  prices (annual -15%, extra discount), reference from the existing
  reference_sequence, per-line tax from the profile treatment;
- linkage: the quote is visible from both sides (deal detail embed + quote
  list contract: billing-profile code, USD-equivalent total, display status);
- currency rejection: EUR/GBP reference currencies -> typed
  ReferenceCurrencyException (400);
- unsynced-profile block: issuing (mark-sent/send) and finalizing
  (approve/convert) a deal-linked quote against an unsynced billing profile
  -> typed UnsyncedBillingProfileException (409); standalone quotes keep
  their existing lifecycle;
- USD-equivalent totals: server-computed per the org FX conventions, on the
  list AND the deal embed; the builder conversions row calibrated verbatim
  against the App__3_.svg design export;
- no duplicated pricing logic: totals/VAT come exclusively from the
  inherited BaseDocumentService.calculate_totals delegation (spied), quote
  rows exclusively from QuoteService.create (spied), and the integration
  module re-implements no tax/total math (source assertion);
- quotes survive deal closure unchanged; closed deals refuse new quotes;
- the documented Draft/Pending/Synced display mapping (exhaustive);
- GET /owner/settings/sales-pricing price-list read (derived Annual/seat +
  10-seat ARR columns).
"""

import inspect
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.common.dependencies import get_current_user
from app.common.document_service import BaseDocumentService
from app.common.exceptions import (
    BadRequestException,
    NotFoundException,
    ReferenceCurrencyException,
    UnsyncedBillingProfileException,
)
from app.common.fx import reference_conversions, usd_equivalent
from app.common.reporting_time import reporting_date
from app.constants.enums import (
    BillingPeriod,
    CustomerType,
    DealWonReason,
    QuoteDisplayStatus,
    QuoteStatus,
    UserRole,
)
from app.constants.settings_defaults import (
    DEFAULT_ANNUAL_BILLING_DISCOUNT_PCT,
    DEFAULT_FX_UNITS_PER_USD,
    DEFAULT_PRODUCT_CATALOG,
)
from app.main import app
from app.modules.auth.models import User
from app.modules.customers.models import CustomerBillingProfile
from app.modules.customers.schemas import CustomerCreate
from app.modules.customers.service import CustomerService
from app.modules.deals import quotes_integration
from app.modules.deals.quotes_integration import DealQuoteService
from app.modules.deals.schemas import (
    DealCreate,
    DealQuoteCreateRequest,
    DealQuoteLineInput,
)
from app.modules.deals.service import DealService
from app.modules.quotes.models import Quote
from app.modules.quotes.service import QuoteService

DEALS_API = "/api/v1/deals"
QUOTES_API = "/api/v1/quotes"
CUSTOMERS_API = "/api/v1/customers"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


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


def _make_customer(db, name="Baraka Logistics"):
    """Create a customer through the service so both billing profiles exist."""
    svc = CustomerService(db)
    return svc.create(
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
    return DealService(db, current_user=owner).create(
        DealCreate(**data), user_id=owner.id
    )


def _quote_svc(db, owner) -> DealQuoteService:
    return DealQuoteService(db, current_user=owner)


def _body(**overrides) -> DealQuoteCreateRequest:
    base = dict(
        lines=[
            DealQuoteLineInput(
                product="Microsoft 365 Business Basic",
                seats=10,
                billing_period=BillingPeriod.ANNUAL,
            )
        ],
    )
    base.update(overrides)
    return DealQuoteCreateRequest(**base)


def _profile(db, customer_id, currency) -> CustomerBillingProfile:
    return (
        db.query(CustomerBillingProfile)
        .filter(
            CustomerBillingProfile.customer_id == customer_id,
            CustomerBillingProfile.currency == currency,
        )
        .one()
    )


def _sync_profiles(db, customer_id) -> None:
    db.query(CustomerBillingProfile).filter(
        CustomerBillingProfile.customer_id == customer_id
    ).update({"synced": True})
    db.flush()


@pytest.fixture
def actor():
    """Install a privileged HTTP actor (approve/convert require it)."""
    identity = SimpleNamespace(id=uuid4(), role=UserRole.ADMIN)
    app.dependency_overrides[get_current_user] = lambda: identity
    yield identity
    app.dependency_overrides.pop(get_current_user, None)


# --------------------------------------------------------------------------
# Seed defaults (settings_defaults parity with the design exports)
# --------------------------------------------------------------------------


def test_product_catalog_seeded_verbatim_from_design():
    expected = [
        ("Microsoft 365 Business Basic", "6.00"),
        ("Microsoft 365 Business Standard", "12.50"),
        ("Microsoft 365 Business Premium", "22.00"),
        ("Microsoft 365 E3", "36.00"),
        ("Microsoft 365 E5", "57.00"),
        ("Exchange Online Plan 1", "4.00"),
        ("Teams Phone Standard", "8.00"),
        ("Copilot for Microsoft 365", "30.00"),
    ]
    assert [
        (e["name"], e["usd_per_seat_month"]) for e in DEFAULT_PRODUCT_CATALOG
    ] == expected
    # Decimal strings, never floats: exact construction must round-trip.
    for entry in DEFAULT_PRODUCT_CATALOG:
        assert str(Decimal(entry["usd_per_seat_month"])) == entry["usd_per_seat_month"]


def test_annual_discount_and_fx_conventions_seeded():
    assert DEFAULT_ANNUAL_BILLING_DISCOUNT_PCT == "15"
    assert set(DEFAULT_FX_UNITS_PER_USD) == {"USD", "KES", "EUR", "GBP"}
    assert DEFAULT_FX_UNITS_PER_USD["USD"] == "1"
    for value in DEFAULT_FX_UNITS_PER_USD.values():
        Decimal(value)  # decimal strings, never floats


def test_reference_conversions_match_design_totals_row():
    # App__3_.svg totals row: KSh 192,882.48 = $1,489.44 = 1,370.28 EUR
    # = 1,176.66 GBP.
    row = reference_conversions(Decimal("192882.48"), "KES")
    assert row["USD"] == Decimal("1489.44")
    assert row["EUR"] == Decimal("1370.28")
    assert row["GBP"] == Decimal("1176.66")


# --------------------------------------------------------------------------
# Prefill flow (service level)
# --------------------------------------------------------------------------


def test_prefill_creates_linked_quote_via_existing_service(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)  # USD

    body = _body(
        lines=[
            DealQuoteLineInput(
                product="Microsoft 365 Business Basic",
                seats=3,
                billing_period=BillingPeriod.MONTHLY,
            ),
            DealQuoteLineInput(
                product="Microsoft 365 E5",
                seats=2,
                billing_period=BillingPeriod.ANNUAL,
            ),
        ]
    )
    quote = _quote_svc(db, owner).create_quote_for_deal(
        deal.id, body, user_id=owner.id
    )

    # Linkage + prefill: customer and currency come from the deal.
    assert quote.deal_id == deal.id
    assert quote.customer_id == deal.customer_id
    assert quote.currency == "USD"
    assert quote.status == QuoteStatus.DRAFT

    # References come from the existing reference_sequence generators.
    assert quote.quote_number.startswith("QTE-")
    assert quote.quote_reference.startswith("QT-")

    # Catalog-derived prices (USD profile: FX x1, zero-rated export).
    lines = {li.item_name: li for li in quote.line_items}
    basic = lines["Microsoft 365 Business Basic"]
    assert basic.quantity == Decimal("3")
    assert basic.unit_price == Decimal("6.00")  # monthly, list price
    e5 = lines["Microsoft 365 E5"]
    assert e5.quantity == Decimal("2")
    # annual: 57.00 x 0.85 = 48.45/user/month x 12 months
    assert e5.unit_price == Decimal("581.40")

    # Totals equal the existing delegation's output for the same items —
    # byte-for-byte, because they ARE that output.
    recomputed = QuoteService.calculate_totals(
        [
            {
                "item_name": li.item_name,
                "description": li.description,
                "quantity": li.quantity,
                "unit_price": li.unit_price,
                "tax_type": li.tax_type,
            }
            for li in quote.line_items
        ],
        tax_point_date=quote.transaction_date,
    )
    assert quote.subtotal == recomputed.subtotal
    assert quote.tax_total == recomputed.tax_total
    assert quote.total_due == recomputed.total_due

    # Zero-rated export profile: no VAT on the USD quote.
    assert quote.subtotal == Decimal("1180.80")
    assert quote.tax_total == Decimal("0.00")
    assert quote.total_due == Decimal("1180.80")

    # Default validity window when the client sends no due date.
    assert quote.due_date == reporting_date() + timedelta(days=30)


def test_prefill_extra_discount_and_kes_fx(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer, currency="KES")

    body = _body(
        lines=[
            DealQuoteLineInput(
                product="Microsoft 365 Business Standard",
                seats=5,
                billing_period=BillingPeriod.ANNUAL,
                extra_discount_pct=Decimal("20"),
            )
        ]
    )
    quote = _quote_svc(db, owner).create_quote_for_deal(deal.id, body)

    # 12.50 x 129.50 = 1618.75 list; x 0.85 (annual) x 0.80 (extra)
    # = 1100.75/user/month; x 12 months = 13209.00 per seat.
    (line,) = quote.line_items
    assert line.unit_price == Decimal("13209.00")
    assert quote.currency == "KES"
    # KES profile treatment is VAT 16%: the delegation must have produced
    # a non-zero tax line (computed by the shared rules, not asserted as
    # flat math here).
    assert quote.tax_total > 0
    assert quote.total_due == quote.subtotal + quote.tax_total


def test_prefill_rejects_unknown_catalog_product(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)

    body = _body(
        lines=[DealQuoteLineInput(product="Slack Enterprise Grid", seats=5)]
    )
    with pytest.raises(BadRequestException, match="not in the product catalog"):
        _quote_svc(db, owner).create_quote_for_deal(deal.id, body)


def test_prefill_missing_deal_404_and_past_due_date_rejected(db):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)

    with pytest.raises(NotFoundException):
        _quote_svc(db, owner).create_quote_for_deal(uuid4(), _body())

    with pytest.raises(BadRequestException, match="due_date"):
        _quote_svc(db, owner).create_quote_for_deal(
            deal.id, _body(due_date=reporting_date() - timedelta(days=1))
        )


# --------------------------------------------------------------------------
# Currency rejection (EUR/GBP are reference-only)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("currency", ["EUR", "GBP"])
def test_reference_currency_rejected_typed(db, currency):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)

    with pytest.raises(ReferenceCurrencyException) as exc_info:
        _quote_svc(db, owner).create_quote_for_deal(
            deal.id, _body(currency=currency)
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.error_code == "ReferenceCurrencyException"
    assert exc_info.value.extra["currency"] == currency
    assert set(exc_info.value.extra["billable"]) == {"USD", "KES"}


def test_reference_currency_rejected_over_http(db, client, actor):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)

    resp = client.post(
        f"{DEALS_API}/{deal.id}/quotes",
        json={
            "lines": [{"product": "Microsoft 365 E3", "seats": 4}],
            "currency": "EUR",
        },
    )
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["error_code"] == "ReferenceCurrencyException"
    assert payload["details"]["currency"] == "EUR"
    assert db.query(Quote).count() == 0  # nothing persisted


# --------------------------------------------------------------------------
# Unsynced-profile issue/finalize gate
# --------------------------------------------------------------------------


def test_unsynced_profile_blocks_issue_and_finalize(db, client, actor):
    owner = _make_owner(db)
    customer = _make_customer(db)  # profiles start unsynced
    deal = _make_deal(db, owner, customer)
    quote = _quote_svc(db, owner).create_quote_for_deal(deal.id, _body())

    # Creating a draft against an unsynced profile is allowed; ISSUING is not.
    resp = client.post(f"{QUOTES_API}/{quote.id}/mark-sent")
    assert resp.status_code == 409
    payload = resp.json()
    assert payload["error_code"] == "UnsyncedBillingProfileException"
    assert payload["details"]["profile_code"] == _profile(
        db, customer.id, "USD"
    ).code
    assert payload["details"]["currency"] == "USD"

    # Syncing the profile through the existing endpoint unblocks issuing.
    sync = client.post(f"{CUSTOMERS_API}/{customer.id}/profiles/USD/sync")
    assert sync.status_code == 200
    resp = client.post(f"{QUOTES_API}/{quote.id}/mark-sent")
    assert resp.status_code == 200

    # FINALIZING re-checks: un-sync (an edit resets the flag) and approve
    # must be refused with the same typed error.
    _profile(db, customer.id, "USD").synced = False
    db.flush()
    resp = client.post(f"{QUOTES_API}/{quote.id}/approve")
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "UnsyncedBillingProfileException"

    _sync_profiles(db, customer.id)
    resp = client.post(f"{QUOTES_API}/{quote.id}/approve")
    assert resp.status_code == 200

    # Conversion (push to accounting) is also gated.
    _profile(db, customer.id, "USD").synced = False
    db.flush()
    resp = client.post(f"{QUOTES_API}/{quote.id}/convert-to-invoice")
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "UnsyncedBillingProfileException"

    _sync_profiles(db, customer.id)
    resp = client.post(f"{QUOTES_API}/{quote.id}/convert-to-invoice")
    assert resp.status_code == 201


def test_standalone_quotes_keep_existing_lifecycle(db):
    """Quotes with no deal link predate profiles: never gated."""
    from app.modules.quotes.schemas import QuoteCreate

    customer = _make_customer(db)  # profiles exist but stay unsynced
    svc = QuoteService(db)
    quote = svc.create(
        QuoteCreate(
            customer_id=customer.id,
            transaction_date=reporting_date(),
            due_date=reporting_date() + timedelta(days=14),
            line_items=[
                {
                    "item_name": "Consulting",
                    "description": "Advisory retainer",
                    "quantity": Decimal("1"),
                    "unit_price": Decimal("100.00"),
                    "tax_type": "vat_16",
                }
            ],
        )
    )
    assert quote.deal_id is None
    # No UnsyncedBillingProfileException despite the unsynced profiles.
    assert svc.mark_as_sent(quote.id).status == QuoteStatus.SENT


# --------------------------------------------------------------------------
# Both-sides visibility + list contract (USD-equivalent totals)
# --------------------------------------------------------------------------


def test_quote_visible_from_both_sides_with_list_contract(db, client, actor):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer, currency="KES")

    resp = client.post(
        f"{DEALS_API}/{deal.id}/quotes",
        json={
            "lines": [
                {
                    "product": "Microsoft 365 Business Basic",
                    "seats": 10,
                    "billingPeriod": "annual",
                }
            ]
        },
    )
    assert resp.status_code == 201
    created = resp.json()
    assert created["deal_id"] == str(deal.id)
    assert created["display_status"] == "Draft"

    kes_profile = _profile(db, customer.id, "KES")
    expected_usd = usd_equivalent(Decimal(str(created["total_due"])), "KES")

    # Side 1: the deal detail embeds the linked quote.
    detail = client.get(f"{DEALS_API}/{deal.id}")
    assert detail.status_code == 200
    (embed,) = detail.json()["quotes"]
    assert embed["id"] == created["id"]
    assert embed["company_name"] == "Baraka Logistics"
    assert embed["billing_profile_code"] == kes_profile.code
    assert embed["currency"] == "KES"
    assert Decimal(str(embed["total_due"])) == Decimal(str(created["total_due"]))
    assert Decimal(str(embed["total_usd_equivalent"])) == expected_usd
    assert embed["display_status"] == "Draft"

    # Side 2: the quote list carries the Sales Desk columns.
    listed = client.get(QUOTES_API)
    assert listed.status_code == 200
    (row,) = listed.json()["items"]
    assert row["deal_id"] == str(deal.id)
    assert row["billing_profile_code"] == kes_profile.code
    assert Decimal(str(row["total_usd_equivalent"])) == expected_usd
    assert row["display_status"] == "Draft"


def test_usd_quote_equivalent_equals_total(db, client, actor):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)  # USD

    resp = client.post(
        f"{DEALS_API}/{deal.id}/quotes",
        json={"lines": [{"product": "Teams Phone Standard", "seats": 25}]},
    )
    assert resp.status_code == 201

    (row,) = client.get(QUOTES_API).json()["items"]
    assert Decimal(str(row["total_usd_equivalent"])) == Decimal(str(row["total_due"]))


# --------------------------------------------------------------------------
# Builder preview (design-calibrated; nothing persisted)
# --------------------------------------------------------------------------


def test_preview_calibrated_against_design_and_persists_nothing(db, client, actor):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer, currency="KES")

    resp = client.post(
        f"{DEALS_API}/{deal.id}/quotes/preview",
        json={
            "lines": [
                {
                    "product": "Microsoft 365 Business Basic",
                    "seats": 10,
                    "billingPeriod": "annual",
                }
            ]
        },
    )
    assert resp.status_code == 200
    preview = resp.json()

    # App__3_.svg calibration: $6.00 -> KSh 777.00 list, KSh 660.45
    # annual-discounted, KSh 79,254.00 line total for 10 seats x 12 months.
    (line,) = preview["lines"]
    assert Decimal(str(line["list_price_per_user_month"])) == Decimal("777.00")
    assert Decimal(str(line["discounted_price_per_user_month"])) == Decimal("660.45")
    assert line["months"] == 12
    assert Decimal(str(line["line_total"])) == Decimal("79254.00")
    assert line["period_label"] == "per year"

    # Totals block: produced by the shared delegation; VAT per the KES
    # profile's "VAT 16%" treatment.
    assert Decimal(str(preview["subtotal"])) == Decimal("79254.00")
    assert preview["tax_label"] == "VAT 16%"
    assert Decimal(str(preview["total_due"])) == Decimal(
        str(preview["subtotal"])
    ) + Decimal(str(preview["tax_total"]))

    # Conversions row mirrors the shared FX conventions exactly.
    expected = reference_conversions(Decimal(str(preview["total_due"])), "KES")
    assert Decimal(str(preview["conversions"]["usd"])) == expected["USD"]
    assert Decimal(str(preview["conversions"]["eur"])) == expected["EUR"]
    assert Decimal(str(preview["conversions"]["gbp"])) == expected["GBP"]

    # Profile banner data ("Posts to {code} · {terms} · {tax} · synced").
    banner = preview["billing_profile"]
    kes_profile = _profile(db, customer.id, "KES")
    assert banner["code"] == kes_profile.code
    assert banner["payment_terms"] == kes_profile.payment_terms
    assert banner["tax_treatment"] == "VAT 16%"
    assert banner["synced"] is False

    # Nothing persisted by the preview.
    assert db.query(Quote).count() == 0


# --------------------------------------------------------------------------
# No duplicated pricing logic
# --------------------------------------------------------------------------


def test_totals_delegate_to_base_document_service(db, monkeypatch):
    """The integration produces totals ONLY via the existing delegations."""
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)

    create_calls: list = []
    totals_calls: list = []

    original_create = QuoteService.create
    original_totals = BaseDocumentService.calculate_totals.__func__

    def spy_create(self, data, user_id=None, deal_id=None):
        create_calls.append(deal_id)
        return original_create(self, data, user_id=user_id, deal_id=deal_id)

    def spy_totals(cls, *args, **kwargs):
        totals_calls.append(cls)
        return original_totals(cls, *args, **kwargs)

    monkeypatch.setattr(QuoteService, "create", spy_create)
    monkeypatch.setattr(
        BaseDocumentService, "calculate_totals", classmethod(spy_totals)
    )

    svc = _quote_svc(db, owner)
    svc.create_quote_for_deal(deal.id, _body())
    assert create_calls == [deal.id]  # rows only via QuoteService.create

    svc.preview_quote_for_deal(deal.id, _body())
    assert totals_calls and all(
        issubclass(cls, BaseDocumentService) for cls in totals_calls
    )


@pytest.mark.no_db
def test_no_pricing_logic_is_duplicated():
    """Structural guarantees that the totals/VAT pipeline exists once.

    - ``QuoteService.calculate_totals`` is the inherited
      ``BaseDocumentService`` implementation, not a copy;
    - the integration service defines no totals computation of its own;
    - the integration module never re-implements tax/total math: no flat
      16% factor and none of the shared financial primitives are called
      there (they belong to the document services it delegates to).
    """
    assert "calculate_totals" not in QuoteService.__dict__
    assert (
        QuoteService.calculate_totals.__func__
        is BaseDocumentService.calculate_totals.__func__
    )
    assert "calculate_totals" not in DealQuoteService.__dict__

    source = inspect.getsource(quotes_integration)
    for forbidden in (
        "0.16",  # flat 16% VAT factor
        "16 /",  # 16-as-percentage arithmetic
        'Decimal("16")',
        "build_line_items",
        "sum_line_totals",
        "calculate_subtotal_vat",
        "allocate_discounted_line_taxes",
        "calculate_discount(",
    ):
        assert forbidden not in source, f"duplicated pricing logic: {forbidden!r}"


# --------------------------------------------------------------------------
# Deal closure semantics
# --------------------------------------------------------------------------


def test_quotes_survive_deal_closure_unchanged(db, client, actor):
    owner = _make_owner(db)
    customer = _make_customer(db)
    deal = _make_deal(db, owner, customer)
    quote = _quote_svc(db, owner).create_quote_for_deal(deal.id, _body())
    before = (quote.status, quote.total_due, quote.subtotal, quote.version)

    DealService(db, current_user=owner).close(
        deal.id, "won", DealWonReason.PRODUCT_FIT.value, "Signed."
    )

    db.refresh(quote)
    assert quote.deal_id == deal.id  # closure never touches quotes
    assert (quote.status, quote.total_due, quote.subtotal, quote.version) == before

    # Still embedded on the closed deal's detail.
    detail = client.get(f"{DEALS_API}/{deal.id}")
    assert detail.status_code == 200
    assert [q["id"] for q in detail.json()["quotes"]] == [str(quote.id)]

    # But a closed deal refuses NEW quotes.
    resp = client.post(
        f"{DEALS_API}/{deal.id}/quotes",
        json={"lines": [{"product": "Microsoft 365 E3", "seats": 1}]},
    )
    assert resp.status_code == 400
    assert "closed deal" in resp.json()["error"]


# --------------------------------------------------------------------------
# Display-status mapping (documented contract)
# --------------------------------------------------------------------------


@pytest.mark.no_db
def test_display_status_mapping_is_exhaustive_and_matches_docs():
    expected = {
        QuoteStatus.DRAFT: QuoteDisplayStatus.DRAFT,
        QuoteStatus.EXPIRED: QuoteDisplayStatus.DRAFT,
        QuoteStatus.SENT: QuoteDisplayStatus.PENDING,
        QuoteStatus.APPROVED: QuoteDisplayStatus.PENDING,
        QuoteStatus.INVOICED: QuoteDisplayStatus.SYNCED,
    }
    assert set(expected) == set(QuoteStatus)  # exhaustive over the lifecycle
    for status, display in expected.items():
        assert QuoteDisplayStatus.from_quote_status(status) is display
        assert QuoteDisplayStatus.from_quote_status(status.value) is display
    assert [d.value for d in QuoteDisplayStatus] == ["Draft", "Pending", "Synced"]


# --------------------------------------------------------------------------
# Price-list read (GET /owner/settings/sales-pricing)
# --------------------------------------------------------------------------


def test_sales_pricing_settings_read(db, client, actor):
    resp = client.get("/api/v1/owner/settings/sales-pricing")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["annual_billing_discount_pct"] == "15"
    assert payload["fx_units_per_usd"] == DEFAULT_FX_UNITS_PER_USD

    catalog = payload["catalog"]
    assert [e["name"] for e in catalog] == [
        e["name"] for e in DEFAULT_PRODUCT_CATALOG
    ]
    # Derived columns match Quotes___Pricing.svg: Annual/seat = monthly x 12,
    # 10-seat ARR = monthly x 12 x 10 (raw list-price maths, no discounts).
    basic = catalog[0]
    assert basic["usd_per_seat_month"] == "6.00"
    assert basic["usd_per_seat_year"] == "72.00"
    assert basic["usd_ten_seat_arr"] == "720.00"
    e5 = next(e for e in catalog if e["name"] == "Microsoft 365 E5")
    assert e5["usd_per_seat_year"] == "684.00"
    assert e5["usd_ten_seat_arr"] == "6840.00"
