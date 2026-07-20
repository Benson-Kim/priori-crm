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


def clear_reporting_timezone_cache() -> None:
    """Clear the cached ZoneInfo after tests override settings."""
    reporting_timezone.cache_clear()
