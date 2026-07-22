"""Organization-local accounting date helpers.

Accounting cutoffs must not depend on the API host, worker, or database session
timezone. Resolve the date once from the configured reporting timezone and pass
that concrete date through the complete reporting call chain.
"""

from datetime import UTC, date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.lib.config import settings


@lru_cache(maxsize=1)
def reporting_timezone() -> ZoneInfo:
    """Return the configured organization reporting timezone."""
    return ZoneInfo(settings.REPORTING_TIMEZONE)


def reporting_date(now: datetime | None = None) -> date:
    """Return the organization-local calendar date for ``now``.

    Naive injected datetimes are interpreted as UTC so tests and callers never
    accidentally inherit the process-local timezone.
    """
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(reporting_timezone()).date()


# Overdue Status Calculation


# Single source of truth for which statuses can never be overdue/expired.
DEFAULT_TERMINAL_STATUSES: frozenset[str] = frozenset({"paid", "canceled"})


def check_is_overdue(
    status: str,
    due_date: date,
    terminal_statuses: frozenset[str] | set[str] = DEFAULT_TERMINAL_STATUSES,
    *,
    as_of_date: date | None = None,
) -> bool:
    """
    Check if a record is overdue/expired based on status and due date.

    The single definition of "past due": the status is not terminal and the
    due date is in the past. ``terminal_statuses`` lets each document type
    supply the statuses that can't be overdue/expired (e.g. invoices use
    paid/canceled; quotes use approved/invoiced).
    """

    cutoff = as_of_date or reporting_date()
    normalized_terminal = {item.lower() for item in terminal_statuses}

    return status.lower() not in normalized_terminal and due_date < cutoff


def calculate_days_overdue(
    status: str,
    due_date: date,
    terminal_statuses: frozenset[str] | set[str] = DEFAULT_TERMINAL_STATUSES,
    *,
    as_of_date: date | None = None,
) -> int:
    """
    Calculate the number of days a record is overdue/expired.
    Returns 0 if not overdue.
    """

    cutoff = as_of_date or reporting_date()
    if not check_is_overdue(status, due_date, terminal_statuses, as_of_date=cutoff):
        return 0

    return (cutoff - due_date).days


def clear_reporting_timezone_cache() -> None:
    """Clear the cached ZoneInfo after tests override settings."""
    reporting_timezone.cache_clear()
