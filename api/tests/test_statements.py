"""Financial statements read-model tests.

Covers: period preset boundary math (incl. last_month across a year
boundary), custom-range validation, overview math (zero-revenue margin,
zero-baseline deltas), income-statement totals == sum of lines, status
and currency exclusion, cashflow sign conventions / ordering / filters /
search / drill-down / counts, and sentinel-window pagination semantics.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.common.exceptions import BadRequestException
from app.common.pagination import PaginationParams
from app.constants.enums import Currency
from app.modules.customers.models import Customer
from app.modules.expenses.models import Expense, ExpenseLineItem
from app.modules.invoices.models import Invoice, InvoiceLineItem
from app.modules.statements.schemas import RangePreset, ResolvedPeriod
from app.modules.statements.service import StatementsService
from app.modules.vendors.models import Vendor

# Period resolution (pure, no DB)


@pytest.mark.no_db
class TestPeriodResolution:
    def test_custom_requires_both_dates(self):
        with pytest.raises(BadRequestException):
            ResolvedPeriod.resolve(RangePreset.CUSTOM, date(2026, 1, 1), None)
        with pytest.raises(BadRequestException):
            ResolvedPeriod.resolve(RangePreset.CUSTOM, None, date(2026, 1, 31))

    def test_custom_from_after_to_rejected(self):
        with pytest.raises(BadRequestException):
            ResolvedPeriod.resolve(
                RangePreset.CUSTOM, date(2026, 2, 1), date(2026, 1, 1)
            )

    def test_custom_range_too_long_rejected(self):
        with pytest.raises(BadRequestException):
            ResolvedPeriod.resolve(
                RangePreset.CUSTOM, date(2010, 1, 1), date(2026, 1, 1)
            )

    def test_dates_rejected_for_presets(self):
        with pytest.raises(BadRequestException):
            ResolvedPeriod.resolve(
                RangePreset.THIS_MONTH, date(2026, 1, 1), date(2026, 1, 31)
            )

    def test_last_month_across_year_boundary(self):
        period = ResolvedPeriod.resolve(RangePreset.LAST_MONTH, today=date(2026, 1, 15))
        assert period.date_from == date(2025, 12, 1)
        assert period.date_to == date(2025, 12, 31)
        # Previous window: equal length (31 days), ending the day before.
        assert period.previous_date_to == date(2025, 11, 30)
        assert period.previous_date_from == date(2025, 10, 31)

    def test_previous_window_is_adjacent_and_equal_length(self):
        period = ResolvedPeriod.resolve(
            RangePreset.LAST_7_DAYS, today=date(2026, 6, 10)
        )
        assert period.date_from == date(2026, 6, 4)
        assert period.date_to == date(2026, 6, 10)
        assert period.previous_date_to == period.date_from - timedelta(days=1)
        current_len = (period.date_to - period.date_from).days
        previous_len = (period.previous_date_to - period.previous_date_from).days
        assert current_len == previous_len == 6

    def test_this_quarter_starts_on_quarter_month(self):
        period = ResolvedPeriod.resolve(
            RangePreset.THIS_QUARTER, today=date(2026, 5, 20)
        )
        assert period.date_from == date(2026, 4, 1)
        assert period.date_to == date(2026, 5, 20)

    def test_this_year_previous_window(self):
        period = ResolvedPeriod.resolve(RangePreset.THIS_YEAR, today=date(2026, 3, 1))
        assert period.date_from == date(2026, 1, 1)
        assert period.previous_date_to == date(2025, 12, 31)


# Shared DB fixtures / helpers


PERIOD = ResolvedPeriod.resolve(
    RangePreset.CUSTOM, date(2025, 10, 1), date(2025, 10, 31)
)


def _customer(db, name="Acme Ltd"):
    customer = Customer(
        customer_type="business",
        company_name=name,
        first_name="Jane",
        last_name="Doe",
        email=f"{uuid4().hex}@example.com",
        phone="0700000000",
        currency="KES",
    )
    db.add(customer)
    db.flush()
    return customer


def _vendor(db, name="CloudHost Ltd"):
    vendor = Vendor(vendor_name=name, status="active", currency="KES")
    db.add(vendor)
    db.flush()
    return vendor


def _invoice(db, customer, txn_date, lines, status="paid", currency="KES"):
    """Create an invoice with no-tax line items. lines: [(name, amount)]."""
    subtotal = sum((amount for _, amount in lines), Decimal("0.00"))
    invoice = Invoice(
        invoice_number=f"INV-{uuid4().hex[:12]}",
        invoice_reference=f"IN-{uuid4().hex[:8]}",
        customer_id=customer.id,
        transaction_date=txn_date,
        due_date=txn_date + timedelta(days=30),
        status=status,
        currency=currency,
        subtotal=subtotal,
        tax_total=Decimal("0.00"),
        total_due=subtotal,
        amount_paid=subtotal if status == "paid" else Decimal("0.00"),
        balance_due=Decimal("0.00") if status == "paid" else subtotal,
    )
    db.add(invoice)
    db.flush()
    for line_number, (name, amount) in enumerate(lines, start=1):
        db.add(
            InvoiceLineItem(
                invoice_id=invoice.id,
                line_number=line_number,
                item_name=name,
                description=f"{name} work",
                quantity=Decimal("1.00"),
                unit_price=amount,
                line_total=amount,
                tax_type="no_tax",
                tax_amount=Decimal("0.00"),
            )
        )
    db.flush()
    return invoice


def _expense(db, vendor, exp_date, lines, status="paid", currency="KES"):
    """Create an expense with no-tax line items. lines: [(name, amount)]."""
    subtotal = sum((amount for _, amount in lines), Decimal("0.00"))
    expense = Expense(
        expense_number=f"EXP-{uuid4().hex[:12]}",
        expense_reference=f"EX-{uuid4().hex[:8]}",
        vendor_id=vendor.id,
        expense_date=exp_date,
        due_date=exp_date + timedelta(days=14),
        status=status,
        currency=currency,
        subtotal=subtotal,
        tax_total=Decimal("0.00"),
        total_due=subtotal,
        amount_paid=subtotal if status == "paid" else Decimal("0.00"),
        balance_due=Decimal("0.00") if status == "paid" else subtotal,
    )
    db.add(expense)
    db.flush()
    for line_number, (name, amount) in enumerate(lines, start=1):
        db.add(
            ExpenseLineItem(
                expense_id=expense.id,
                line_number=line_number,
                item_name=name,
                description=f"{name} cost",
                quantity=Decimal("1.00"),
                unit_price=amount,
                line_total=amount,
                tax_type="no_tax",
                tax_amount=Decimal("0.00"),
            )
        )
    db.flush()
    return expense


def _seed_october(db):
    """Standard dataset: Oct 2025 (current) + Sep 2025 (previous window)."""
    customer = _customer(db)
    vendor = _vendor(db)
    inv1 = _invoice(
        db, customer, date(2025, 10, 10), [("Consulting", Decimal("1000.00"))]
    )
    inv2 = _invoice(
        db,
        customer,
        date(2025, 10, 12),
        [("Consulting", Decimal("500.00")), ("Licensing", Decimal("250.00"))],
    )
    exp1 = _expense(db, vendor, date(2025, 10, 15), [("Cloud", Decimal("300.00"))])
    # Previous window (PERIOD.previous_*: 2025-08-31 .. 2025-09-30)
    _invoice(db, customer, date(2025, 9, 10), [("Consulting", Decimal("800.00"))])
    _expense(db, vendor, date(2025, 9, 12), [("Cloud", Decimal("100.00"))])
    db.commit()
    return customer, vendor, inv1, inv2, exp1


# Overview


class TestOverview:
    def test_overview_math(self, db):
        _seed_october(db)
        result = StatementsService(db).get_overview(PERIOD, Currency.KES)

        assert result.total_revenue.amount == Decimal("1750.00")
        assert result.total_expenses.amount == Decimal("300.00")
        assert result.net_profit.amount == Decimal("1450.00")
        assert result.total_revenue.previous_amount == Decimal("800.00")
        assert result.total_expenses.previous_amount == Decimal("100.00")

        assert result.total_revenue.change_percent == pytest.approx(118.75)
        assert result.total_expenses.change_percent == pytest.approx(200.0)
        assert result.net_profit.change_percent == pytest.approx(107.14)
        assert result.profit_margin.percent == pytest.approx(82.86)
        assert result.profit_margin.previous_percent == pytest.approx(87.5)
        assert result.profit_margin.change_points == pytest.approx(-4.64)

    def test_draft_and_canceled_documents_excluded(self, db):
        customer, vendor, *_ = _seed_october(db)
        _invoice(
            db,
            customer,
            date(2025, 10, 20),
            [("Consulting", Decimal("9999.00"))],
            status="draft",
        )
        _invoice(
            db,
            customer,
            date(2025, 10, 21),
            [("Consulting", Decimal("8888.00"))],
            status="canceled",
        )
        _expense(
            db,
            vendor,
            date(2025, 10, 22),
            [("Cloud", Decimal("7777.00"))],
            status="canceled",
        )
        db.commit()

        result = StatementsService(db).get_overview(PERIOD, Currency.KES)
        assert result.total_revenue.amount == Decimal("1750.00")
        assert result.total_expenses.amount == Decimal("300.00")

    def test_empty_period_is_zero_with_null_deltas(self, db):
        _seed_october(db)
        empty = ResolvedPeriod.resolve(
            RangePreset.CUSTOM, date(2024, 1, 1), date(2024, 1, 31)
        )
        result = StatementsService(db).get_overview(empty, Currency.KES)
        assert result.total_revenue.amount == Decimal("0")
        assert result.total_revenue.change_percent is None
        assert result.profit_margin.percent is None
        assert result.profit_margin.change_points is None

    def test_zero_baseline_yields_null_change(self, db):
        # Data only in the current window -> previous totals are zero.
        customer = _customer(db)
        _invoice(db, customer, date(2025, 10, 5), [("Consulting", Decimal("100.00"))])
        db.commit()
        result = StatementsService(db).get_overview(PERIOD, Currency.KES)
        assert result.total_revenue.amount == Decimal("100.00")
        assert result.total_revenue.change_percent is None
        assert result.profit_margin.previous_percent is None


# Income statement


class TestIncomeStatement:
    def test_grouping_and_totals_equal_sum_of_lines(self, db):
        _seed_october(db)
        result = StatementsService(db).get_income_statement(PERIOD, Currency.KES)

        revenue = result.operating_revenue
        by_name = {line.account_category: line for line in revenue.line_items}
        assert by_name["Consulting"].amount == Decimal("1500.00")
        assert by_name["Consulting"].document_count == 2
        assert by_name["Licensing"].amount == Decimal("250.00")
        assert revenue.total == sum(
            (line.amount for line in revenue.line_items), Decimal("0")
        )
        assert revenue.total == Decimal("1750.00")

        expenses = result.operating_expenses
        assert [line.account_category for line in expenses.line_items] == ["Cloud"]
        assert expenses.total == Decimal("300.00")
        assert result.net_profit == Decimal("1450.00")

    def test_lines_ordered_by_amount_desc(self, db):
        _seed_october(db)
        result = StatementsService(db).get_income_statement(PERIOD, Currency.KES)
        amounts = [line.amount for line in result.operating_revenue.line_items]
        assert amounts == sorted(amounts, reverse=True)

    def test_currency_isolation(self, db):
        customer, *_ = _seed_october(db)
        _invoice(
            db,
            customer,
            date(2025, 10, 18),
            [("USD Consulting", Decimal("5000.00"))],
            currency="USD",
        )
        db.commit()

        service = StatementsService(db)
        kes = service.get_income_statement(PERIOD, Currency.KES)
        assert kes.operating_revenue.total == Decimal("1750.00")

        usd = service.get_income_statement(PERIOD, Currency.USD)
        assert usd.operating_revenue.total == Decimal("5000.00")
        assert [line.account_category for line in usd.operating_revenue.line_items] == [
            "USD Consulting"
        ]


# Cashflow ledger


class TestCashflow:
    def test_signs_ordering_and_source_types(self, db):
        _, _, inv1, inv2, exp1 = _seed_october(db)
        result = StatementsService(db).list_cashflow(
            PERIOD, Currency.KES, PaginationParams(page=1, per_page=10)
        )
        entries = result.items
        assert len(entries) == 3
        # Ordered by entry date DESC: expense (15th), inv2 (12th), inv1 (10th)
        assert entries[0].source_id == exp1.id
        assert entries[0].amount == Decimal("-300.00")
        assert entries[0].category == "expense"
        assert entries[0].source_type == "expense"
        assert entries[1].source_id == inv2.id
        assert entries[1].amount == Decimal("750.00")
        assert entries[1].source_type == "quote_invoice"
        assert entries[2].source_id == inv1.id
        assert entries[2].amount == Decimal("1000.00")
        # Stable unique ledger ids across the union
        assert len({entry.id for entry in entries}) == 3

    def test_category_filter(self, db):
        _seed_october(db)
        service = StatementsService(db)
        params = PaginationParams(page=1, per_page=10)
        income = service.list_cashflow(PERIOD, Currency.KES, params, category="income")
        assert {entry.category for entry in income.items} == {"income"}
        assert len(income.items) == 2
        expense = service.list_cashflow(
            PERIOD, Currency.KES, params, category="expense"
        )
        assert len(expense.items) == 1
        assert expense.items[0].amount < 0

    def test_search_by_entity_and_reference(self, db):
        _, _, inv1, _, _ = _seed_october(db)
        service = StatementsService(db)
        params = PaginationParams(page=1, per_page=10)

        by_entity = service.list_cashflow(PERIOD, Currency.KES, params, search="acme")
        assert len(by_entity.items) == 2
        assert all(entry.entity_name == "Acme Ltd" for entry in by_entity.items)

        by_ref = service.list_cashflow(
            PERIOD, Currency.KES, params, search=inv1.invoice_reference
        )
        assert [entry.source_id for entry in by_ref.items] == [inv1.id]

    def test_account_category_drilldown(self, db):
        _, _, _, inv2, _ = _seed_october(db)
        result = StatementsService(db).list_cashflow(
            PERIOD,
            Currency.KES,
            PaginationParams(page=1, per_page=10),
            account_category="Licensing",
        )
        assert [entry.source_id for entry in result.items] == [inv2.id]

    def test_canceled_and_draft_documents_never_appear(self, db):
        customer, vendor, *_ = _seed_october(db)
        _invoice(
            db,
            customer,
            date(2025, 10, 20),
            [("Consulting", Decimal("1.00"))],
            status="draft",
        )
        _expense(
            db,
            vendor,
            date(2025, 10, 21),
            [("Cloud", Decimal("1.00"))],
            status="canceled",
        )
        db.commit()
        result = StatementsService(db).list_cashflow(
            PERIOD, Currency.KES, PaginationParams(page=1, per_page=10)
        )
        assert len(result.items) == 3

    def test_counts(self, db):
        _seed_october(db)
        counts = StatementsService(db).get_cashflow_counts(PERIOD, Currency.KES)
        assert counts.all == 3
        assert counts.income == 2
        assert counts.expense == 1

    def test_counts_respect_search_and_drilldown(self, db):
        _seed_october(db)
        service = StatementsService(db)
        searched = service.get_cashflow_counts(PERIOD, Currency.KES, search="acme")
        assert searched.all == 2
        assert searched.expense == 0
        drilled = service.get_cashflow_counts(
            PERIOD, Currency.KES, account_category="Licensing"
        )
        assert drilled.all == 1

    def test_pagination_sentinel_has_next(self, db):
        _seed_october(db)
        service = StatementsService(db)

        page1 = service.list_cashflow(
            PERIOD, Currency.KES, PaginationParams(page=1, per_page=2)
        )
        assert len(page1.items) == 2
        assert page1.metadata.has_next is True
        assert page1.metadata.has_prev is False
        assert page1.metadata.total is None  # not requested -> no COUNT(*)

        page2 = service.list_cashflow(
            PERIOD, Currency.KES, PaginationParams(page=2, per_page=2)
        )
        assert len(page2.items) == 1
        assert page2.metadata.has_next is False
        assert page2.metadata.has_prev is True

    def test_pagination_opt_in_total(self, db):
        _seed_october(db)
        result = StatementsService(db).list_cashflow(
            PERIOD,
            Currency.KES,
            PaginationParams(page=1, per_page=2, with_total=True),
        )
        assert result.metadata.total == 3
        assert result.metadata.total_pages == 2
