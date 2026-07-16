"""Accounting-periods service: create, close and reopen periods.

Closing/reopening are audited via the shared append-only audit trail so
the control itself leaves evidence. Overlap is enforced here (not at the
DB level): period administration is a privileged, low-frequency path and
the check runs inside the caller's transaction. The closing *procedure*
(retained-earnings roll-forward) follows the general ledger (#7).
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.common.audit import record_audit_event
from app.common.exceptions import BadRequestException, NotFoundException
from app.modules.periods.models import AccountingPeriod, PeriodStatus
from app.modules.periods.schemas import PeriodCreate

logger = logging.getLogger(__name__)


class PeriodsService:
    """Service layer for accounting-period administration."""

    def __init__(self, db: Session, current_user=None) -> None:
        self._db = db
        self._current_user = current_user

    @property
    def _actor_id(self):
        return getattr(self._current_user, "id", None)

    def list_periods(self) -> list[AccountingPeriod]:
        """All periods, newest window first."""
        return (
            self._db.query(AccountingPeriod)
            .order_by(AccountingPeriod.start_date.desc())
            .all()
        )

    def get_by_id(self, period_id: uuid.UUID) -> AccountingPeriod:
        """Load one period or raise 404."""
        period = (
            self._db.query(AccountingPeriod)
            .filter(AccountingPeriod.id == period_id)
            .first()
        )
        if not period:
            raise NotFoundException(
                detail=f"Accounting period '{period_id}' not found",
                resource="accounting_period",
            )
        return period

    def create(self, data: PeriodCreate) -> AccountingPeriod:
        """Create an OPEN period; overlapping an existing one is rejected."""
        overlap = (
            self._db.query(AccountingPeriod.id)
            .filter(
                AccountingPeriod.start_date <= data.end_date,
                AccountingPeriod.end_date >= data.start_date,
            )
            .first()
        )
        if overlap is not None:
            raise BadRequestException(
                detail="The period overlaps an existing accounting period.",
                field="start_date",
            )

        period = AccountingPeriod(
            start_date=data.start_date,
            end_date=data.end_date,
            status=PeriodStatus.OPEN.value,
        )
        self._db.add(period)
        self._db.flush()
        logger.info(
            "Created accounting period %s..%s",
            data.start_date,
            data.end_date,
            extra={"period_id": str(period.id)},
        )
        return period

    def close(self, period_id: uuid.UUID, note: str | None = None) -> AccountingPeriod:
        """Close a period (privileged, audited)."""
        period = self.get_by_id(period_id)
        if period.status == PeriodStatus.CLOSED.value:
            raise BadRequestException(
                detail="The period is already closed.", field="status"
            )

        period.status = PeriodStatus.CLOSED.value
        period.closed_at = datetime.now(UTC)
        period.closed_by = self._actor_id
        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=self._actor_id,
            entity_type="accounting_period",
            entity_id=period.id,
            action="period_closed",
            after={
                "start_date": period.start_date.isoformat(),
                "end_date": period.end_date.isoformat(),
                "note": note,
            },
        )
        logger.warning(
            "Closed accounting period %s..%s",
            period.start_date,
            period.end_date,
            extra={"period_id": str(period.id)},
        )
        return period

    def reopen(self, period_id: uuid.UUID, reason: str) -> AccountingPeriod:
        """Reopen a closed period; a justification is mandatory (audited)."""
        period = self.get_by_id(period_id)
        if period.status != PeriodStatus.CLOSED.value:
            raise BadRequestException(
                detail="Only a closed period can be reopened.", field="status"
            )

        period.status = PeriodStatus.OPEN.value
        period.closed_at = None
        period.closed_by = None
        period.reopen_reason = reason
        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=self._actor_id,
            entity_type="accounting_period",
            entity_id=period.id,
            action="period_reopened",
            after={
                "start_date": period.start_date.isoformat(),
                "end_date": period.end_date.isoformat(),
                "reason": reason,
            },
        )
        logger.warning(
            "Reopened accounting period %s..%s",
            period.start_date,
            period.end_date,
            extra={"period_id": str(period.id), "reason": reason},
        )
        return period
