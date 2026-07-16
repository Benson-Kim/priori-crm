"""Closed-period write guard.

``assert_period_open`` is called by every financial write path (document
create/update, payment recording) with the date that drives period
attribution. It is a single indexed query and a no-op when no closed
period covers the date, so open-period operation cost is one cheap
SELECT.

Kept in app.common (not the periods module) so document services depend
on a small guard, not the full periods service; the model import is lazy
to avoid registry-order coupling.
"""

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.common.exceptions import BadRequestException

logger = logging.getLogger(__name__)


def assert_period_open(
    db: Session,
    on_date: date | None,
    *,
    field: str = "transaction_date",
) -> None:
    """Raise when ``on_date`` falls inside a CLOSED accounting period.

    ``None`` dates are ignored (nothing to attribute). The error carries
    the offending field name so clients can surface it on the right
    control.
    """
    if on_date is None:
        return

    from app.modules.periods.models import AccountingPeriod, PeriodStatus

    closed = (
        db.query(AccountingPeriod.id)
        .filter(
            AccountingPeriod.status == PeriodStatus.CLOSED.value,
            AccountingPeriod.start_date <= on_date,
            AccountingPeriod.end_date >= on_date,
        )
        .first()
    )
    if closed is not None:
        raise BadRequestException(
            detail=(
                f"The accounting period covering {on_date.isoformat()} is "
                "closed. Reopen the period before posting into it."
            ),
            field=field,
        )
