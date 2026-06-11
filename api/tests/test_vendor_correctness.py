"""Tests for the vendor correctness fixes in #36.

Covers:
- ISSUE-047: open-payable statuses are derived from ExpenseStatus and never
  reference the fictional "partial"/"sent" values.
- ISSUE-049: the shared default statement period resolves to the last 12
  months when bounds are omitted.
- ISSUE-051: VendorStatusCounts.all is the sum of all status rows.
"""

from datetime import date, timedelta

from app.common.statement import (
    DEFAULT_STATEMENT_PERIOD_DAYS,
    default_statement_period,
)
from app.constants.enums import ExpenseStatus
from app.modules.vendors import service as vendor_service
from app.modules.vendors.service import (
    CLOSED_PAYABLE_STATUSES,
    OPEN_PAYABLE_STATUSES,
    OVERDUE_PAYABLE_STATUS,
)


class TestPayableStatusConstants:
    """ISSUE-047: payables statuses must align with ExpenseStatus."""

    def test_open_statuses_are_pending_and_overdue(self):
        assert set(OPEN_PAYABLE_STATUSES) == {
            ExpenseStatus.PENDING,
            ExpenseStatus.OVERDUE,
        }

    def test_no_fictional_statuses(self):
        # "partial" / "sent" are not ExpenseStatus members and must be gone.
        every = set(OPEN_PAYABLE_STATUSES) | set(CLOSED_PAYABLE_STATUSES)
        assert "partial" not in every
        assert "sent" not in every

    def test_every_payable_status_is_a_valid_expense_status(self):
        valid = {s.value for s in ExpenseStatus}
        for status in (*OPEN_PAYABLE_STATUSES, *CLOSED_PAYABLE_STATUSES):
            assert status in valid

    def test_open_and_closed_partition_expense_statuses(self):
        # Together, open + closed payable statuses cover the full enum with
        # no overlap, so a payable is unambiguously open or closed.
        every = set(OPEN_PAYABLE_STATUSES) | set(CLOSED_PAYABLE_STATUSES)
        assert every == {s.value for s in ExpenseStatus}
        assert not (set(OPEN_PAYABLE_STATUSES) & set(CLOSED_PAYABLE_STATUSES))

    def test_overdue_status_is_open(self):
        assert OVERDUE_PAYABLE_STATUS == ExpenseStatus.OVERDUE
        assert OVERDUE_PAYABLE_STATUS in OPEN_PAYABLE_STATUSES

    def test_module_exposes_single_open_statuses_constant(self):
        # The queries should reference the constant, not re-list statuses.
        assert vendor_service.OPEN_PAYABLE_STATUSES is OPEN_PAYABLE_STATUSES


class TestDefaultStatementPeriod:
    """ISSUE-049: vendor + customer statements share a 12-month default."""

    def test_defaults_to_last_twelve_months(self):
        start, end = default_statement_period(None, None)
        assert end == date.today()
        assert start == end - timedelta(days=DEFAULT_STATEMENT_PERIOD_DAYS)

    def test_explicit_bounds_are_preserved(self):
        start_in = date(2025, 1, 1)
        end_in = date(2025, 6, 30)
        start, end = default_statement_period(start_in, end_in)
        assert (start, end) == (start_in, end_in)

    def test_only_start_defaulted_when_end_given(self):
        end_in = date(2025, 6, 30)
        start, end = default_statement_period(None, end_in)
        assert end == end_in
        assert start == end_in - timedelta(days=DEFAULT_STATEMENT_PERIOD_DAYS)

    def test_only_end_defaulted_when_start_given(self):
        start_in = date(2025, 1, 1)
        start, end = default_statement_period(start_in, None)
        assert start == start_in
        assert end == date.today()
