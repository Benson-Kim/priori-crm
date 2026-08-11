"""Business logic for the Sales Desk deals module (issue #39).

Lifecycle rules enforced HERE (and mirrored by DB CHECK constraints):

- stages advance strictly one at a time in DEAL_STAGE_ORDER — no skipping;
- only OPEN deals can advance, log activity, close or park; PARKED deals can
  only resume; WON/LOST deals are terminal;
- advancing, closing, logging and parking all REQUIRE a non-blank note
  ("A note is required to advance or close." — HTTP 422 otherwise);
- close reasons must come from the enumerated won/lost lists;
- every lifecycle event writes a DealActivity stage record, and every
  create/advance/close/park/resume writes an audit_events row in the same
  transaction via common.audit.

Derived metrics (age, idle, time-in-pipeline, stage progress) are computed
from the stage record at read time — never stored.
"""

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import joinedload

from app.common.audit import record_audit_event
from app.common.database import assert_version
from app.common.document_service import ServiceBase
from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    DatabaseException,
    NotFoundException,
    ValidationException,
)
from app.common.financial import quantize_money
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.reporting_time import reporting_date
from app.common.search import build_search_clause
from app.constants.enums import (
    DEAL_STAGE_ORDER,
    DEAL_STAGE_PROGRESS_TOTAL,
    BillingCurrency,
    DealLostReason,
    DealStage,
    DealStatus,
    DealWonReason,
)
from app.constants.settings_defaults import BILLING_CURRENCY_PER_USD
from app.modules.auth.models import User
from app.modules.customers.models import Customer
from app.modules.deals.models import Deal, DealActivity
from app.modules.deals.schemas import (
    DealActivityResponse,
    DealAggregatesResponse,
    DealCreate,
    DealOwnerAggregate,
    DealResponse,
    DealStageAggregate,
    DealSummary,
    DealUpdate,
)

logger = logging.getLogger(__name__)

#: Design copy — the drawer textarea's helper text and the 422 detail.
NOTE_REQUIRED_MESSAGE = "A note is required to advance or close."

#: Timeline labels for non-stage lifecycle events (prototype verbatim).
CLOSED_WON_LABEL = "Closed — Won"
CLOSED_LOST_LABEL = "Closed — Lost"
PARKED_LABEL = "Moved to future pipeline"
RESUME_NOTE = "Re-engaged from the future pipeline."
DEFAULT_CREATE_NOTE = "Deal created"


def usd_equivalent(value: Decimal, currency: str) -> Decimal:
    """Convert a native-currency value to its USD equivalent (2 dp).

    Uses the fixed BILLING_CURRENCY_PER_USD table (units per 1 USD) so
    multi-currency rows can be aggregated on lists; detail views keep the
    native value + currency.
    """
    per_usd = Decimal(BILLING_CURRENCY_PER_USD[str(currency)])
    return quantize_money(Decimal(value) / per_usd)


class DealService(ServiceBase):
    """Handles deal CRUD and the stage/close/park state machine."""

    # Internal helpers

    def _get_deal(self, deal_id: uuid.UUID) -> Deal:
        deal = (
            self._db.query(Deal)
            .options(
                joinedload(Deal.customer),
                joinedload(Deal.owner),
                joinedload(Deal.activities).joinedload(DealActivity.author),
            )
            .filter(Deal.id == deal_id)
            .first()
        )
        if deal is None:
            raise NotFoundException(
                detail=f"Deal with ID '{deal_id}' not found",
                resource="deal",
            )
        return deal

    @staticmethod
    def _require_note(note: str | None, message: str = NOTE_REQUIRED_MESSAGE) -> str:
        """Return the stripped note or reject with 422 (design copy)."""
        cleaned = (note or "").strip()
        if not cleaned:
            raise ValidationException(detail=message)
        return cleaned

    @staticmethod
    def _require_open(deal: Deal, action: str) -> None:
        if deal.status != DealStatus.OPEN:
            raise BadRequestException(
                detail=(
                    f"Only open deals can {action} — this deal is '{deal.status}'."
                ),
                field="status",
            )

    def _record_activity(
        self,
        deal: Deal,
        stage_label: str,
        note: str,
        *,
        activity_date: date | None = None,
    ) -> DealActivity:
        activity = DealActivity(
            deal_id=deal.id,
            stage_label=stage_label,
            note=note,
            activity_date=activity_date or reporting_date(),
            author_id=self._actor_id,
        )
        self._db.add(activity)
        self._db.flush()
        return activity

    def _audit(
        self,
        deal: Deal,
        action: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        record_audit_event(
            self._db,
            actor_id=self._actor_id,
            entity_type="deal",
            entity_id=deal.id,
            action=action,
            before=before,
            after=after,
        )

    @staticmethod
    def _lifecycle_snapshot(deal: Deal) -> dict[str, Any]:
        return {
            "status": str(deal.status),
            "stage": str(deal.stage),
            "parked_until": (
                deal.parked_until.isoformat() if deal.parked_until else None
            ),
            "close_reason": deal.close_reason,
        }

    # Serialization (derived metrics)

    @staticmethod
    def _stage_position(deal: Deal) -> int:
        """1-based 'Stage N of 5' position; 5 is the closed end-cap."""
        if deal.status in (DealStatus.WON, DealStatus.LOST):
            return DEAL_STAGE_PROGRESS_TOTAL
        return DEAL_STAGE_ORDER.index(DealStage(deal.stage)) + 1

    @staticmethod
    def _activity_window(
        deal: Deal,
        first: date | None,
        last: date | None,
    ) -> tuple[int, int, int]:
        """Compute (age_days, idle_days, days_in_pipeline).

        Falls back to the creation date when a deal somehow has no stage
        record (the service always writes one on create).
        """
        today = reporting_date()
        fallback = deal.created_at.date()
        first = first or fallback
        last = last or fallback
        age_days = (today - first).days
        idle_days = (today - last).days
        # Closed/parked deals stop the clock at their last record — the
        # design's "Nd total" ribbon.
        days_in_pipeline = (
            age_days if deal.status == DealStatus.OPEN else (last - first).days
        )
        return age_days, idle_days, days_in_pipeline

    @staticmethod
    def _display_name(user: User | None) -> str | None:
        if user is None:
            return None
        return f"{user.first_name} {user.last_name}".strip()

    def _summary_fields(
        self,
        deal: Deal,
        *,
        first: date | None,
        last: date | None,
        latest: DealActivity | None,
    ) -> dict[str, Any]:
        age_days, idle_days, days_in_pipeline = self._activity_window(deal, first, last)
        position = self._stage_position(deal)
        customer = deal.customer
        return {
            "id": deal.id,
            "customer_id": deal.customer_id,
            "customer_name": customer.display_name if customer else "",
            "owner_id": deal.owner_id,
            "owner_name": self._display_name(deal.owner),
            "product": deal.product,
            "seats": deal.seats,
            "value": deal.value,
            "currency": deal.currency,
            "value_usd": usd_equivalent(deal.value, deal.currency),
            "status": DealStatus(deal.status),
            "stage": DealStage(deal.stage),
            "stage_label": DealStage(deal.stage).label,
            "stage_position": position,
            "stage_total": DEAL_STAGE_PROGRESS_TOTAL,
            "stage_progress": f"Stage {position} of {DEAL_STAGE_PROGRESS_TOTAL}",
            "age_days": age_days,
            "idle_days": idle_days,
            "days_in_pipeline": days_in_pipeline,
            "parked_until": deal.parked_until,
            "resume_stage": (
                DealStage(deal.resume_stage) if deal.resume_stage else None
            ),
            "close_reason": deal.close_reason,
            "latest_stage_label": latest.stage_label if latest else None,
            "latest_note": latest.note if latest else None,
            "latest_activity_date": latest.activity_date if latest else None,
            "version": deal.version,
            "created_at": deal.created_at,
        }

    @staticmethod
    def _ordered_activities(deal: Deal) -> list[DealActivity]:
        return sorted(
            deal.activities,
            key=lambda a: (a.activity_date, a.created_at),
        )

    def to_response(self, deal: Deal) -> DealResponse:
        """Serialize a fully loaded deal to the detail schema."""
        activities = self._ordered_activities(deal)
        first = activities[0].activity_date if activities else None
        last = activities[-1].activity_date if activities else None
        latest = activities[-1] if activities else None
        return DealResponse(
            **self._summary_fields(deal, first=first, last=last, latest=latest),
            close_note=deal.close_note,
            updated_at=deal.updated_at,
            activities=[
                DealActivityResponse(
                    id=a.id,
                    stage_label=a.stage_label,
                    note=a.note,
                    activity_date=a.activity_date,
                    author_id=a.author_id,
                    author_name=self._display_name(a.author),
                    created_at=a.created_at,
                )
                for a in activities
            ],
        )

    # Create

    def create(self, data: DealCreate) -> Deal:
        """Create a deal plus its opening stage record and audit row."""
        customer = (
            self._db.query(Customer).filter(Customer.id == data.customer_id).first()
        )
        if customer is None:
            raise NotFoundException(
                detail=f"Customer with ID '{data.customer_id}' not found",
                resource="customer",
            )

        owner = self._db.query(User).filter(User.id == data.owner_id).first()
        if owner is None:
            raise NotFoundException(
                detail=f"User with ID '{data.owner_id}' not found",
                resource="user",
            )

        # The deal value currency FOLLOWS the customer's primary_currency
        # billing profile unless explicitly overridden with another billing
        # currency (USD|KES). Never a third enum.
        currency = data.currency or customer.primary_currency or BillingCurrency.USD

        try:
            deal = Deal(
                customer_id=customer.id,
                owner_id=owner.id,
                product=data.product.strip(),
                seats=data.seats,
                value=quantize_money(data.value),
                currency=str(currency),
                stage=DealStage.ACTIVATION,
                status=DealStatus.OPEN,
            )
            self._db.add(deal)
            self._db.flush()

            note = (data.note or "").strip() or DEFAULT_CREATE_NOTE
            self._record_activity(deal, DealStage.ACTIVATION.label, note)
            self._audit(
                deal,
                "created",
                after=self._lifecycle_snapshot(deal),
            )

            logger.info("Created deal %s", deal.id, extra={"deal_id": str(deal.id)})
            return deal
        except IntegrityError as e:
            logger.exception("Integrity error creating deal")
            raise ConflictException("Deal data violates database constraints") from e
        except SQLAlchemyError as e:
            logger.exception("Database error creating deal")
            raise DatabaseException("Failed to create deal") from e

    # Read

    def get_deal(self, deal_id: uuid.UUID) -> DealResponse:
        return self.to_response(self._get_deal(deal_id))

    def list_deals(
        self,
        params: PaginationParams,
        *,
        status: str | None = None,
        stage: str | None = None,
        owner_id: uuid.UUID | None = None,
        customer_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> PaginatedResponse[DealSummary]:
        """Paginated, filtered, searchable deal list with derived metrics.

        Default order mirrors the design: open deals first (later stages
        first, higher USD value first within a stage), parked next, closed
        last.
        """
        try:
            query = (
                self._db.query(Deal)
                .options(joinedload(Deal.customer), joinedload(Deal.owner))
                .join(Customer, Deal.customer_id == Customer.id)
            )

            if status and status != "all":
                if status not in set(DealStatus):
                    raise BadRequestException(
                        detail=f"Unknown status filter '{status}'.",
                        field="status",
                    )
                query = query.filter(Deal.status == status)
            if stage:
                if stage not in set(DealStage):
                    raise BadRequestException(
                        detail=f"Unknown stage filter '{stage}'.",
                        field="stage",
                    )
                query = query.filter(Deal.stage == stage)
            if owner_id:
                query = query.filter(Deal.owner_id == owner_id)
            if customer_id:
                query = query.filter(Deal.customer_id == customer_id)

            if search:
                clause = build_search_clause(
                    search,
                    Deal.product,
                    Customer.company_name,
                    Customer.first_name,
                    Customer.last_name,
                )
                if clause is not None:
                    query = query.filter(clause)

            total = query.count() if params.with_total else None

            status_rank = case(
                (Deal.status == DealStatus.OPEN.value, 0),
                (Deal.status == DealStatus.PARKED.value, 1),
                else_=2,
            )
            stage_rank = case(
                *((Deal.stage == s.value, i) for i, s in enumerate(DEAL_STAGE_ORDER)),
                else_=len(DEAL_STAGE_ORDER),
            )
            usd_factor = case(
                *(
                    (Deal.currency == cur, 1.0 / float(rate))
                    for cur, rate in BILLING_CURRENCY_PER_USD.items()
                ),
                else_=1.0,
            )
            deals = (
                query.order_by(
                    status_rank.asc(),
                    stage_rank.desc(),
                    (Deal.value * usd_factor).desc(),
                    Deal.created_at.desc(),
                )
                .offset(params.offset)
                .limit(params.fetch_limit)
                .all()
            )

            # One batched fetch of the page's stage records: first/last
            # dates drive age/idle, the last row is the "Latest record".
            windows = self._activity_windows([d.id for d in deals])

            items = []
            for deal in deals:
                first, last, latest = windows.get(deal.id, (None, None, None))
                items.append(
                    DealSummary(
                        **self._summary_fields(
                            deal, first=first, last=last, latest=latest
                        )
                    )
                )
            return PaginatedResponse.create_from_window(items, params, total)
        except (BadRequestException, ValidationException):
            raise
        except SQLAlchemyError as e:
            logger.exception("Database error listing deals")
            raise DatabaseException("Failed to list deals") from e

    def _activity_windows(
        self, deal_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[date | None, date | None, DealActivity | None]]:
        """Map deal_id -> (first_date, last_date, latest_activity) for a page."""
        if not deal_ids:
            return {}
        rows = (
            self._db.query(DealActivity)
            .filter(DealActivity.deal_id.in_(deal_ids))
            .order_by(
                DealActivity.deal_id,
                DealActivity.activity_date.asc(),
                DealActivity.created_at.asc(),
            )
            .all()
        )
        windows: dict[uuid.UUID, tuple[date | None, date | None, DealActivity | None]]
        windows = {}
        for activity in rows:
            first, _, _ = windows.get(activity.deal_id, (None, None, None))
            windows[activity.deal_id] = (
                first or activity.activity_date,
                activity.activity_date,
                activity,
            )
        return windows

    # Update / delete

    def update(
        self,
        deal_id: uuid.UUID,
        data: DealUpdate,
        *,
        expected_version: int | None = None,
    ) -> Deal:
        """Partial update of an open deal's editable fields."""
        deal = self._get_deal(deal_id)
        self._require_open(deal, "be edited")
        assert_version(self._db, Deal, deal_id, expected_version)

        before = {
            "product": deal.product,
            "seats": deal.seats,
            "value": str(deal.value),
            "owner_id": str(deal.owner_id) if deal.owner_id else None,
        }
        try:
            if data.product is not None:
                deal.product = data.product.strip()
            if data.seats is not None:
                deal.seats = data.seats
            if data.value is not None:
                deal.value = quantize_money(data.value)
            if data.owner_id is not None:
                owner = self._db.query(User).filter(User.id == data.owner_id).first()
                if owner is None:
                    raise NotFoundException(
                        detail=f"User with ID '{data.owner_id}' not found",
                        resource="user",
                    )
                deal.owner_id = owner.id

            deal.version += 1
            self._db.flush()
            self._audit(
                deal,
                "updated",
                before=before,
                after={
                    "product": deal.product,
                    "seats": deal.seats,
                    "value": str(deal.value),
                    "owner_id": str(deal.owner_id) if deal.owner_id else None,
                },
            )
            return deal
        except (NotFoundException, BadRequestException, ConflictException):
            raise
        except IntegrityError as e:
            logger.exception("Integrity error updating deal")
            raise ConflictException("Deal data violates database constraints") from e
        except SQLAlchemyError as e:
            logger.exception("Database error updating deal")
            raise DatabaseException("Failed to update deal") from e

    def delete(self, deal_id: uuid.UUID) -> None:
        """Hard-delete a deal and its stage record (privileged only)."""
        self.require_privileged_actor("delete deals")
        deal = self._get_deal(deal_id)
        before = self._lifecycle_snapshot(deal)
        try:
            self._audit(deal, "hard_deleted", before=before)
            self._db.delete(deal)
            self._db.flush()
        except SQLAlchemyError as e:
            logger.exception("Database error deleting deal")
            raise DatabaseException("Failed to delete deal") from e

    # Stage machine

    def advance_stage(self, deal_id: uuid.UUID, note: str | None) -> Deal:
        """Advance an open deal exactly one stage, with a mandatory note."""
        deal = self._get_deal(deal_id)
        self._require_open(deal, "advance")
        cleaned = self._require_note(note)

        current_index = DEAL_STAGE_ORDER.index(DealStage(deal.stage))
        if current_index >= len(DEAL_STAGE_ORDER) - 1:
            raise BadRequestException(
                detail=(
                    f"{DEAL_STAGE_ORDER[-1].label} is the final stage — "
                    "close the deal as won or lost instead."
                ),
                field="stage",
            )
        before = self._lifecycle_snapshot(deal)
        next_stage = DEAL_STAGE_ORDER[current_index + 1]
        deal.stage = next_stage
        deal.version += 1
        self._db.flush()
        self._record_activity(deal, next_stage.label, cleaned)
        self._audit(
            deal,
            "stage_advanced",
            before=before,
            after=self._lifecycle_snapshot(deal),
        )
        return deal

    def log_activity(self, deal_id: uuid.UUID, note: str | None) -> Deal:
        """Log a stage record at the current stage without advancing."""
        deal = self._get_deal(deal_id)
        self._require_open(deal, "log activity")
        cleaned = self._require_note(note)
        self._record_activity(deal, DealStage(deal.stage).label, cleaned)
        deal.version += 1
        self._db.flush()
        return deal

    def close(
        self,
        deal_id: uuid.UUID,
        *,
        result: str,
        reason: str,
        note: str | None,
    ) -> Deal:
        """Close an open deal as won or lost with an enumerated reason."""
        deal = self._get_deal(deal_id)
        self._require_open(deal, "be closed")
        cleaned = self._require_note(note)

        if result == DealStatus.WON:
            allowed = [r.value for r in DealWonReason]
            label = CLOSED_WON_LABEL
        else:
            allowed = [r.value for r in DealLostReason]
            label = CLOSED_LOST_LABEL
        if reason not in allowed:
            raise ValidationException(
                detail=(
                    f"'{reason}' is not a valid {result} reason. "
                    f"Choose one of: {', '.join(allowed)}."
                )
            )

        before = self._lifecycle_snapshot(deal)
        deal.status = DealStatus(result)
        deal.close_reason = reason
        deal.close_note = cleaned
        deal.parked_until = None
        deal.resume_stage = None
        deal.version += 1
        self._db.flush()
        # Prototype writes the close record as "{reason} — {note}".
        self._record_activity(deal, label, f"{reason} — {cleaned}")
        self._audit(
            deal,
            f"closed_{result}",
            before=before,
            after=self._lifecycle_snapshot(deal),
        )
        return deal

    def park(
        self,
        deal_id: uuid.UUID,
        *,
        parked_until: date,
        note: str | None,
    ) -> Deal:
        """Park an open deal into the future pipeline until a given date."""
        deal = self._get_deal(deal_id)
        self._require_open(deal, "be parked")
        cleaned = self._require_note(
            note, "A note is required to park a deal — record why now isn't the time."
        )
        if parked_until <= reporting_date():
            raise ValidationException(
                detail="The re-engage date must be in the future."
            )

        before = self._lifecycle_snapshot(deal)
        deal.status = DealStatus.PARKED
        deal.parked_until = parked_until
        deal.resume_stage = deal.stage
        deal.version += 1
        self._db.flush()
        self._record_activity(
            deal,
            PARKED_LABEL,
            f"{cleaned} — re-engage on {parked_until.isoformat()}",
        )
        self._audit(deal, "parked", before=before, after=self._lifecycle_snapshot(deal))
        return deal

    def resume(self, deal_id: uuid.UUID) -> Deal:
        """Return a parked deal to the pipeline at its recorded stage."""
        deal = self._get_deal(deal_id)
        if deal.status != DealStatus.PARKED:
            raise BadRequestException(
                detail=f"Only parked deals can resume — this deal is '{deal.status}'.",
                field="status",
            )
        before = self._lifecycle_snapshot(deal)
        resume_stage = DealStage(deal.resume_stage or deal.stage)
        deal.status = DealStatus.OPEN
        deal.stage = resume_stage
        deal.parked_until = None
        deal.resume_stage = None
        deal.version += 1
        self._db.flush()
        self._record_activity(deal, resume_stage.label, RESUME_NOTE)
        self._audit(
            deal, "resumed", before=before, after=self._lifecycle_snapshot(deal)
        )
        return deal

    # Aggregates

    def aggregates(self) -> DealAggregatesResponse:
        """Trivial per-stage and per-rep aggregates for the pipeline header.

        Sums are grouped by currency in SQL and converted/summed as exact
        Decimals in Python (a handful of groups), so no float FX math ever
        touches money. Richer analytics belong to #45.
        """
        try:
            stage_rows = (
                self._db.query(
                    Deal.stage,
                    Deal.currency,
                    func.count(Deal.id),
                    func.coalesce(func.sum(Deal.value), 0),
                )
                .filter(Deal.status == DealStatus.OPEN.value)
                .group_by(Deal.stage, Deal.currency)
                .all()
            )
            per_stage: dict[str, dict[str, Any]] = {
                s.value: {"count": 0, "value_usd": Decimal("0.00")}
                for s in DEAL_STAGE_ORDER
            }
            for stage, currency, count, value_sum in stage_rows:
                bucket = per_stage[stage]
                bucket["count"] += count
                bucket["value_usd"] += usd_equivalent(Decimal(str(value_sum)), currency)

            owner_rows = (
                self._db.query(
                    Deal.owner_id,
                    Deal.status,
                    Deal.currency,
                    func.count(Deal.id),
                    func.coalesce(func.sum(Deal.value), 0),
                )
                .group_by(Deal.owner_id, Deal.status, Deal.currency)
                .all()
            )
            per_owner: dict[uuid.UUID | None, dict[str, Any]] = {}
            for owner_id, status, currency, count, value_sum in owner_rows:
                bucket = per_owner.setdefault(
                    owner_id,
                    {
                        "open_count": 0,
                        "open_value_usd": Decimal("0.00"),
                        "won_count": 0,
                        "lost_count": 0,
                    },
                )
                if status == DealStatus.OPEN:
                    bucket["open_count"] += count
                    bucket["open_value_usd"] += usd_equivalent(
                        Decimal(str(value_sum)), currency
                    )
                elif status == DealStatus.WON:
                    bucket["won_count"] += count
                elif status == DealStatus.LOST:
                    bucket["lost_count"] += count

            owner_names: dict[uuid.UUID, str] = {}
            owner_ids = [oid for oid in per_owner if oid is not None]
            if owner_ids:
                for user in self._db.query(User).filter(User.id.in_(owner_ids)).all():
                    owner_names[user.id] = self._display_name(user) or ""

            return DealAggregatesResponse(
                stages=[
                    DealStageAggregate(
                        stage=s,
                        stage_label=s.label,
                        count=per_stage[s.value]["count"],
                        value_usd=per_stage[s.value]["value_usd"],
                    )
                    for s in DEAL_STAGE_ORDER
                ],
                owners=[
                    DealOwnerAggregate(
                        owner_id=owner_id,
                        owner_name=owner_names.get(owner_id),
                        **bucket,
                    )
                    for owner_id, bucket in per_owner.items()
                ],
            )
        except SQLAlchemyError as e:
            logger.exception("Database error aggregating deals")
            raise DatabaseException("Failed to aggregate deals") from e
