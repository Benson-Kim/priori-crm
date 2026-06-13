"""Dashboard read-model tests.

Covers: granularity auto-selection and bucket math (pure), summary-card
parity with the statements overview, receivables and new-customer
windows with zero-safe deltas, cashflow-series zero-fill / exclusions /
currency isolation / explicit granularity, top-sales grouping, ordering
and limit, and recent-transaction ordering, signs and enrichment.

Reuses the statements test fixtures so both read models are exercised
against the same dataset.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from app.constants.enums import Currency
from app.modules.dashboard.schemas import (
    Granularity,
    build_buckets,
    resolve_granularity,
)
from app.modules.dashboard.service import DashboardService
from app.modules.statements.schemas import RangePreset, ResolvedPeriod
from tests.test_statements import (
    PERIOD,
    _customer,
    _expense,
    _invoice,
    _seed_october,
)

# Granularity and buckets (pure, no DB)


@pytest.mark.no_db
class TestGranularityAndBuckets:
    def test_auto_selection_thresholds(self):
        month = ResolvedPeriod.resolve(
            RangePreset.CUSTOM, date(2025, 10, 1), date(2025, 10, 31)
        )
        assert resolve_granularity(month) is Granularity.DAY

        quarter = ResolvedPeriod.resolve(
            RangePreset.CUSTOM, date(2025, 7, 1), date(2025, 9, 30)
        )
        assert resolve_granularity(quarter) is Granularity.WEEK

        year = ResolvedPeriod.resolve(
            RangePreset.CUSTOM, date(2025, 1, 1), date(2025, 12, 31)
        )
        assert resolve_granularity(year) is Granularity.MONTH

    def test_buckets_cover_window_contiguously(self):
        for granularity in Granularity:
            buckets = build_buckets(date(2025, 1, 15), date(2025, 4, 2), granularity)
            assert buckets[0][0] == date(2025, 1, 15)
            assert buckets[-1][1] == date(2025, 4, 2)
            for start, end in buckets:
                assert start <= end
            for (_, prev_end), (next_start, _) in pairwise(buckets):
                assert next_start == prev_end + timedelta(days=1)

    def test_month_buckets_follow_calendar_months(self):
        buckets = build_buckets(date(2025, 1, 15), date(2025, 3, 10), Granularity.MONTH)
        assert buckets == [
            (date(2025, 1, 15), date(2025, 1, 31)),
            (date(2025, 2, 1), date(2025, 2, 28)),
            (date(2025, 3, 1), date(2025, 3, 10)),
        ]

    def test_december_month_bucket_handles_year_end(self):
        buckets = build_buckets(date(2025, 12, 1), date(2026, 1, 15), Granularity.MONTH)
        assert buckets == [
            (date(2025, 12, 1), date(2025, 12, 31)),
            (date(2026, 1, 1), date(2026, 1, 15)),
        ]


# Summary cards


class TestSummary:
    def test_income_and_expenses_match_statements_overview(self, db):
        _seed_october(db)
        service = DashboardService(db)
        summary = service.get_summary(PERIOD, Currency.KES)
        overview = service.get_overview(PERIOD, Currency.KES)
        # Verbatim reuse: the dashboard cards can never disagree with the
        # statements overview for the same window.
        assert summary.income == overview.total_revenue
        assert summary.expenses == overview.total_expenses

    def test_balance_is_outstanding_receivables(self, db):
        customer = _customer(db)
        _invoice(
            db,
            customer,
            date(2025, 10, 5),
            [("Consulting", Decimal("1000.00"))],
            status="sent",
        )
        _invoice(
            db,
            customer,
            date(2025, 10, 6),
            [("Consulting", Decimal("400.00"))],
            status="paid",
        )
        # Previous window (2025-08-31 .. 2025-09-30)
        _invoice(
            db,
            customer,
            date(2025, 9, 20),
            [("Consulting", Decimal("250.00"))],
            status="sent",
        )
        db.commit()

        summary = DashboardService(db).get_summary(PERIOD, Currency.KES)
        assert summary.balance.amount == Decimal("1000.00")
        assert summary.balance.previous_amount == Decimal("250.00")
        assert summary.balance.change_percent == pytest.approx(300.0)

    def test_new_customers_window_exclusions_and_delta(self, db):
        inside = _customer(db, name="Inside Ltd")
        inside.created_at = datetime(2025, 10, 10, 12, tzinfo=UTC)
        previous = _customer(db, name="Previous Ltd")
        previous.created_at = datetime(2025, 9, 15, 12, tzinfo=UTC)
        deleted = _customer(db, name="Gone Ltd")
        deleted.created_at = datetime(2025, 10, 11, 12, tzinfo=UTC)
        deleted.status = "deleted"
        db.commit()

        summary = DashboardService(db).get_summary(PERIOD, Currency.KES)
        assert summary.new_customers.count == 1
        assert summary.new_customers.previous_count == 1
        assert summary.new_customers.change_percent == pytest.approx(0.0)

    def test_zero_baselines_yield_null_deltas(self, db):
        customer = _customer(db)
        customer.created_at = datetime(2025, 10, 10, 12, tzinfo=UTC)
        _invoice(
            db,
            customer,
            date(2025, 10, 5),
            [("Consulting", Decimal("100.00"))],
            status="sent",
        )
        db.commit()

        summary = DashboardService(db).get_summary(PERIOD, Currency.KES)
        assert summary.balance.change_percent is None
        assert summary.income.change_percent is None
        assert summary.new_customers.change_percent is None


# Cashflow chart series


class TestCashflowSeries:
    def test_buckets_zero_filled_with_correct_totals(self, db):
        _seed_october(db)
        result = DashboardService(db).get_cashflow_series(PERIOD, Currency.KES)

        assert result.granularity is Granularity.DAY
        assert len(result.buckets) == 31  # complete, zero-filled series

        by_start = {bucket.bucket_start: bucket for bucket in result.buckets}
        assert by_start[date(2025, 10, 10)].income == Decimal("1000.00")
        assert by_start[date(2025, 10, 12)].income == Decimal("750.00")
        assert by_start[date(2025, 10, 15)].expense == Decimal("300.00")
        assert by_start[date(2025, 10, 15)].net == Decimal("-300.00")
        assert by_start[date(2025, 10, 1)].income == Decimal("0.00")
        assert by_start[date(2025, 10, 1)].expense == Decimal("0.00")

        assert result.total_income == Decimal("1750.00")
        assert result.total_expense == Decimal("300.00")
        assert result.net_total == Decimal("1450.00")

    def test_series_totals_match_overview_for_tax_free_data(self, db):
        # The fixtures carry no tax, so the tax-inclusive cash view must
        # equal the pre-tax overview totals.
        _seed_october(db)
        service = DashboardService(db)
        series = service.get_cashflow_series(PERIOD, Currency.KES)
        overview = service.get_overview(PERIOD, Currency.KES)
        assert series.total_income == overview.total_revenue.amount
        assert series.total_expense == overview.total_expenses.amount

    def test_draft_canceled_and_foreign_currency_excluded(self, db):
        customer, vendor, *_ = _seed_october(db)
        _invoice(
            db,
            customer,
            date(2025, 10, 20),
            [("Consulting", Decimal("9.00"))],
            status="draft",
        )
        _expense(
            db,
            vendor,
            date(2025, 10, 21),
            [("Cloud", Decimal("9.00"))],
            status="canceled",
        )
        _invoice(
            db,
            customer,
            date(2025, 10, 22),
            [("USD Consulting", Decimal("9.00"))],
            currency="USD",
        )
        db.commit()

        series = DashboardService(db).get_cashflow_series(PERIOD, Currency.KES)
        assert series.total_income == Decimal("1750.00")
        assert series.total_expense == Decimal("300.00")

    def test_explicit_granularity_override(self, db):
        _seed_october(db)
        series = DashboardService(db).get_cashflow_series(
            PERIOD, Currency.KES, granularity=Granularity.MONTH
        )
        assert series.granularity is Granularity.MONTH
        assert len(series.buckets) == 1
        bucket = series.buckets[0]
        assert (bucket.bucket_start, bucket.bucket_end) == (
            date(2025, 10, 1),
            date(2025, 10, 31),
        )
        assert bucket.net == Decimal("1450.00")


# Top sales


class TestTopSales:
    def test_grouping_ordering_and_units(self, db):
        _seed_october(db)
        result = DashboardService(db).get_top_sales(PERIOD, Currency.KES, limit=5)
        assert [item.item_name for item in result.items] == [
            "Consulting",
            "Licensing",
        ]
        consulting = result.items[0]
        assert consulting.amount == Decimal("1500.00")
        assert consulting.document_count == 2
        assert consulting.units_sold == Decimal("2.00")

    def test_limit_caps_rows(self, db):
        _seed_october(db)
        result = DashboardService(db).get_top_sales(PERIOD, Currency.KES, limit=1)
        assert [item.item_name for item in result.items] == ["Consulting"]


# Recent transactions


class TestRecentTransactions:
    def test_ordering_signs_and_enrichment(self, db):
        _, _, inv1, inv2, exp1 = _seed_october(db)
        result = DashboardService(db).get_recent_transactions(
            PERIOD, Currency.KES, limit=10
        )
        items = result.items
        # Newest first: expense (15th), inv2 (12th), inv1 (10th)
        assert [item.source_id for item in items] == [exp1.id, inv2.id, inv1.id]

        assert items[0].amount == Decimal("-300.00")
        assert items[0].category == "expense"
        assert items[0].source_type == "expense"
        assert items[0].item_name == "Cloud"

        assert items[1].amount == Decimal("750.00")
        assert items[1].category == "income"
        assert items[1].source_type == "quote_invoice"
        assert items[1].entity_name == "Acme Ltd"
        # First line item by line number, not the largest one.
        assert items[1].item_name == "Consulting"
        assert items[1].website is None

        # Stable unique ledger ids across the union.
        assert len({item.id for item in items}) == 3

    def test_limit_caps_rows(self, db):
        _seed_october(db)
        result = DashboardService(db).get_recent_transactions(
            PERIOD, Currency.KES, limit=2
        )
        assert len(result.items) == 2
