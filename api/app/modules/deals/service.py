"""Deal business logic: pipeline CRUD, stage machine and derived values.

Atomicity contract

All mutating methods call ``self._db.flush()`` only; commit/rollback is
owned by ``get_db()``. Every audit row is written in the same transaction
as the mutation it records (``common/audit.py`` contract).

Stage/state rules (server-enforced — the UI only renders)

- Stages advance strictly one at a time; no skipping.
- Advancing or closing REQUIRES a non-empty note (422 otherwise).
- Closed (won/lost) and parked deals can never advance.
- Close requires an enumerated reason + note; park requires a future
  re-engage date; resume returns the deal to its ``resume_stage``.
"""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import case, func, select

from app.common.audit import record_audit_event, status_value
from app.common.database import assert_version
from app.common.document_service import ServiceBase, StateMachineMixin
from app.common.exceptions import (
    BadRequestException,
    NotFoundException,
    ValidationException,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.reporting_time import reporting_date
from app.common.search import build_search_clause
from app.constants.enums import (
    DEAL_PIPELINE_TOTAL_STEPS,
    CustomerStatus,
    DealHygieneBucket,
    DealLostReason,
    DealStage,
    DealStatus,
    DealTab,
    DealWonReason,
)
from app.modules.auth.models import User
from app.modules.customers.models import Customer, CustomerBillingProfile
from app.modules.deals.models import Deal, DealActivity
from app.modules.deals.schemas import (
    NOTE_REQUIRED_MESSAGE,
    DealActivityResponse,
    DealBillingProfileResponse,
    DealCreate,
    DealFilterParams,
    DealOwnerResponse,
    DealResponse,
    DealStatusCounts,
    DealUpdate,
)

logger = logging.getLogger(__name__)

#: Stage-record labels for lifecycle events (verbatim from the designs).
_CLOSE_LABELS = {DealStatus.WON: "Closed — Won", DealStatus.LOST: "Closed — Lost"}
_PARK_LABEL = "Moved to future pipeline"
_RESUME_NOTE = "Re-engaged from the future pipeline."
_DEFAULT_CREATE_NOTE = "Deal created."


class DealService(StateMachineMixin, ServiceBase):
    """Service layer for Sales Desk deals."""

    _document_noun = "deal"

    # Formal status state machine — enforced via _transition(). Stage
    # advancement is orthogonal and only permitted while OPEN.
    ALLOWED_TRANSITIONS: ClassVar[dict[DealStatus, list[DealStatus]]] = {
        DealStatus.OPEN: [DealStatus.WON, DealStatus.LOST, DealStatus.PARKED],
        DealStatus.PARKED: [DealStatus.OPEN],
        DealStatus.WON: [],  # terminal
        DealStatus.LOST: [],  # terminal
    }

    # CREATE

    def create(self, data: DealCreate, user_id: uuid.UUID | None = None) -> Deal:
        """Create a new deal with its first stage record."""
        customer = (
            self._db.query(Customer).filter(Customer.id == data.customer_id).first()
        )
        if not customer:
            raise NotFoundException(
                detail=f"Customer with ID '{data.customer_id}' not found",
                resource="customer",
            )
        if customer.status != CustomerStatus.ACTIVE:
            raise BadRequestException(
                detail=(
                    "Cannot create a deal for inactive customer: "
                    f"{customer.display_name}"
                ),
                field="customer_id",
            )
        self._validate_owner(data.owner_id)
        self._validate_currency(customer, data.currency.value)

        deal = Deal(
            customer_id=data.customer_id,
            owner_id=data.owner_id,
            product=data.product,
            seats=data.seats,
            value=data.value,
            currency=data.currency.value,
            stage=DealStage.ACTIVATION,
            status=DealStatus.OPEN,
            created_by=user_id,
        )
        self._db.add(deal)
        self._db.flush()

        note = (data.note or "").strip() or _DEFAULT_CREATE_NOTE
        self._record(deal, DealStage.ACTIVATION.label, note)

        record_audit_event(
            self._db,
            actor_id=user_id,
            entity_type="deal",
            entity_id=deal.id,
            action="created",
            after=self._snapshot(deal),
        )

        logger.info(
            "Created deal",
            extra={
                "deal_id": str(deal.id),
                "customer_id": str(deal.customer_id),
                "value": float(deal.value),
                "created_by": str(user_id) if user_id else None,
            },
        )
        return deal

    # READ

    def get_by_id(self, deal_id: uuid.UUID) -> Deal:
        """Retrieve a deal by ID (customer, owner and history loaded)."""
        deal = self._db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise NotFoundException(
                detail=f"Deal with ID '{deal_id}' not found", resource="deal"
            )
        return deal

    def list_deals(
        self, params: PaginationParams, filters: DealFilterParams
    ) -> PaginatedResponse[DealResponse]:
        """List deals with tab/owner/hygiene/search filters + pagination."""
        query = self._apply_filters(self._db.query(Deal), filters)

        total = query.count() if params.with_total else None

        stage_rank = case(
            (Deal.stage == DealStage.ACTIVATION.value, 1),
            (Deal.stage == DealStage.QUALIFICATION.value, 2),
            (Deal.stage == DealStage.PROPOSAL_QUOTE.value, 3),
            (Deal.stage == DealStage.NEGOTIATION.value, 4),
            else_=5,
        )
        status_rank = case(
            (Deal.status == DealStatus.OPEN.value, 0),
            (Deal.status == DealStatus.PARKED.value, 1),
            (Deal.status == DealStatus.WON.value, 2),
            else_=3,
        )

        rows = (
            query.order_by(
                status_rank,
                stage_rank,
                Deal.value.desc(),
                Deal.created_at.desc(),
            )
            .offset(params.offset)
            .limit(params.fetch_limit)
            .all()
        )

        items = [self.build_response(deal) for deal in rows]
        return PaginatedResponse.create_from_window(items, params, total=total)

    def get_status_counts(self, owner_id: uuid.UUID | None = None) -> DealStatusCounts:
        """Counts per tab (All / Open / Won / Lost) + parked."""
        query = self._db.query(Deal.status, func.count(Deal.id))
        if owner_id:
            query = query.filter(Deal.owner_id == owner_id)
        counts = dict(query.group_by(Deal.status).all())
        return DealStatusCounts(
            all=sum(counts.values()),
            open=counts.get(DealStatus.OPEN.value, 0),
            won=counts.get(DealStatus.WON.value, 0),
            lost=counts.get(DealStatus.LOST.value, 0),
            parked=counts.get(DealStatus.PARKED.value, 0),
        )

    def _apply_filters(self, query, filters: DealFilterParams):
        """Apply the pipeline list filters (single source of truth)."""
        if filters.tab == DealTab.OPEN:
            query = query.filter(Deal.status == DealStatus.OPEN.value)
        elif filters.tab == DealTab.WON:
            query = query.filter(Deal.status == DealStatus.WON.value)
        elif filters.tab == DealTab.LOST:
            query = query.filter(Deal.status == DealStatus.LOST.value)
        elif not filters.show_closed:
            # ALL tab with closed hidden: open + parked stay visible.
            query = query.filter(
                Deal.status.notin_([DealStatus.WON.value, DealStatus.LOST.value])
            )

        if filters.owner_id:
            query = query.filter(Deal.owner_id == filters.owner_id)

        if filters.hygiene:
            query = self._apply_hygiene_filter(query, filters.hygiene)

        clause = build_search_clause(
            filters.search or "",
            Customer.company_name,
            Customer.first_name,
            Customer.last_name,
            Deal.product,
        )
        if clause is not None:
            query = query.join(Customer, Deal.customer_id == Customer.id).filter(clause)

        return query

    def _apply_hygiene_filter(self, query, bucket: DealHygieneBucket):
        """Activity-hygiene buckets; only open deals ever qualify.

        idle = days since the latest record; age = days since the first.
        Mirrors the design's chips: Active this week (idle ≤ 7), 8-30 days
        quiet (7 < idle ≤ 30), No activity 30d+ (idle > 30), Open 45d+
        (age > 45).
        """
        today = reporting_date()

        last_activity = (
            select(func.max(DealActivity.activity_date))
            .where(DealActivity.deal_id == Deal.id)
            .correlate(Deal)
            .scalar_subquery()
        )
        first_activity = (
            select(func.min(DealActivity.activity_date))
            .where(DealActivity.deal_id == Deal.id)
            .correlate(Deal)
            .scalar_subquery()
        )

        query = query.filter(Deal.status == DealStatus.OPEN.value)

        if bucket == DealHygieneBucket.ACTIVE_WEEK:
            return query.filter(last_activity >= self._days_ago(today, 7))
        if bucket == DealHygieneBucket.QUIET_8_30:
            return query.filter(
                last_activity < self._days_ago(today, 7),
                last_activity >= self._days_ago(today, 30),
            )
        if bucket == DealHygieneBucket.NO_ACTIVITY_30:
            return query.filter(last_activity < self._days_ago(today, 30))
        # OPEN_45: in the pipeline for more than 45 days.
        return query.filter(first_activity < self._days_ago(today, 45))

    @staticmethod
    def _days_ago(today: date, days: int) -> date:
        return today - timedelta(days=days)

    # UPDATE

    def update(
        self, deal_id: uuid.UUID, data: DealUpdate, expected_version: int
    ) -> Deal:
        """Update commercial fields of an open/parked deal."""
        deal = self.get_by_id(deal_id)
        if deal.is_closed:
            raise BadRequestException(
                detail="Closed deals are read-only. Reopen is not supported.",
                field="status",
            )
        assert_version(self._db, Deal, deal_id, expected_version)

        updates = data.model_dump(exclude_unset=True)
        if not updates:
            return deal

        if "owner_id" in updates and updates["owner_id"] is not None:
            self._validate_owner(updates["owner_id"])
        if "currency" in updates and updates["currency"] is not None:
            new_currency = updates["currency"].value
            if new_currency != deal.currency:
                self._validate_currency(deal.customer, new_currency)
            updates["currency"] = new_currency

        for field, value in updates.items():
            if value is not None:
                setattr(deal, field, value)

        deal.version += 1
        self._db.flush()
        return deal

    # DELETE

    def delete(self, deal_id: uuid.UUID) -> None:
        """Hard-delete an open/parked deal (closed deals are evidence)."""
        deal = self.get_by_id(deal_id)
        if deal.is_closed:
            raise BadRequestException(
                detail="Closed deals cannot be deleted — they are pipeline history.",
                field="status",
            )
        record_audit_event(
            self._db,
            actor_id=self.actor_id,
            entity_type="deal",
            entity_id=deal.id,
            action="hard_deleted",
            before=self._snapshot(deal),
        )
        self._db.delete(deal)
        self._db.flush()

    # STAGE / LIFECYCLE

    def advance_stage(self, deal_id: uuid.UUID, note: str) -> Deal:
        """Advance an open deal exactly one stage; note mandatory (422)."""
        deal = self.get_by_id(deal_id)
        note = self._require_note(note)
        self._assert_open(deal, "advance")

        stage = DealStage(deal.stage)
        next_stage = stage.next_stage
        if next_stage is None:
            raise BadRequestException(
                detail=(
                    f"'{stage.label}' is the final open stage. Close the "
                    "deal won or lost instead."
                ),
                field="stage",
            )

        deal.stage = next_stage
        deal.version += 1
        self._record(deal, next_stage.label, note)

        record_audit_event(
            self._db,
            actor_id=self.actor_id,
            entity_type="deal",
            entity_id=deal.id,
            action="stage_advanced",
            before={"stage": stage.value},
            after={"stage": next_stage.value},
        )
        return deal

    def log_activity(self, deal_id: uuid.UUID, note: str) -> Deal:
        """Log a note at the current stage of an open deal (422 if empty)."""
        deal = self.get_by_id(deal_id)
        note = self._require_note(note)
        self._assert_open(deal, "log activity on")
        self._record(deal, DealStage(deal.stage).label, note)
        return deal

    def close(self, deal_id: uuid.UUID, result: str, reason: str, note: str) -> Deal:
        """Close a deal won/lost with an enumerated reason + mandatory note."""
        deal = self.get_by_id(deal_id)
        note = self._require_note(note)

        try:
            new_status = DealStatus(result)
        except ValueError:
            new_status = None
        if new_status not in (DealStatus.WON, DealStatus.LOST):
            raise BadRequestException(
                detail="Close result must be 'won' or 'lost'.", field="result"
            )
        allowed_reasons = (
            DealWonReason if new_status == DealStatus.WON else DealLostReason
        )
        if reason not in [m.value for m in allowed_reasons]:
            raise BadRequestException(
                detail=(
                    f"'{reason}' is not a valid {new_status.value} reason. "
                    f"Allowed: {[m.value for m in allowed_reasons]}"
                ),
                field="reason",
            )

        before_status = status_value(deal.status)
        self._transition(deal, new_status)
        deal.close_reason = reason
        deal.close_note = note
        deal.closed_at = datetime.now(UTC)

        self._record(deal, _CLOSE_LABELS[new_status], f"{reason} — {note}")

        record_audit_event(
            self._db,
            actor_id=self.actor_id,
            entity_type="deal",
            entity_id=deal.id,
            action="closed",
            before={"status": before_status, "stage": deal.stage},
            after={
                "status": new_status.value,
                "close_reason": reason,
                "close_note": note,
            },
        )
        return deal

    def park(self, deal_id: uuid.UUID, parked_until: date, note: str) -> Deal:
        """Park an open deal into the future pipeline until a future date."""
        deal = self.get_by_id(deal_id)
        note = self._require_note(note)

        if parked_until <= reporting_date():
            raise BadRequestException(
                detail="The re-engage date must be in the future.",
                field="parked_until",
            )

        before_status = status_value(deal.status)
        self._transition(deal, DealStatus.PARKED)
        deal.resume_stage = deal.stage
        deal.parked_until = parked_until

        self._record(
            deal,
            _PARK_LABEL,
            f"{note} — re-engage on {parked_until.isoformat()}",
        )

        record_audit_event(
            self._db,
            actor_id=self.actor_id,
            entity_type="deal",
            entity_id=deal.id,
            action="parked",
            before={"status": before_status},
            after={
                "status": DealStatus.PARKED.value,
                "parked_until": parked_until.isoformat(),
                "resume_stage": deal.resume_stage,
            },
        )
        return deal

    def resume(self, deal_id: uuid.UUID) -> Deal:
        """Re-engage a parked deal back to its resume stage."""
        deal = self.get_by_id(deal_id)

        resume_stage = DealStage(deal.resume_stage or deal.stage)
        self._transition(deal, DealStatus.OPEN)
        deal.stage = resume_stage
        deal.parked_until = None
        deal.resume_stage = None

        self._record(deal, resume_stage.label, _RESUME_NOTE)

        record_audit_event(
            self._db,
            actor_id=self.actor_id,
            entity_type="deal",
            entity_id=deal.id,
            action="resumed",
            before={"status": DealStatus.PARKED.value},
            after={
                "status": DealStatus.OPEN.value,
                "stage": resume_stage.value,
            },
        )
        return deal

    # RESPONSE BUILDING (server-computed derived fields)

    def build_response(self, deal: Deal) -> DealResponse:
        """Serialize a deal exactly as the screens consume it."""
        today = reporting_date()
        # Sort explicitly (oldest → newest) so derived values never depend
        # on the in-session collection order after same-request appends.
        activities = sorted(
            deal.activities, key=lambda a: (a.activity_date, a.created_at)
        )

        if activities:
            first_date = activities[0].activity_date
            last_date = activities[-1].activity_date
        else:  # defensive: every service-created deal has a first record
            first_date = last_date = deal.created_at.date()

        stage = DealStage(deal.stage)
        status = DealStatus(deal.status)

        weighted_value: Decimal | None = None
        if status in (DealStatus.OPEN, DealStatus.PARKED):
            weighted_value = (deal.value * stage.probability).quantize(Decimal("0.01"))

        history = [DealActivityResponse.model_validate(a) for a in activities]

        billing_profile = next(
            (
                DealBillingProfileResponse.model_validate(p)
                for p in deal.customer.billing_profiles
                if p.currency == deal.currency
            ),
            None,
        )

        return DealResponse(
            id=deal.id,
            customer_id=deal.customer_id,
            company_name=deal.customer.display_name,
            owner=self._owner_response(deal.owner),
            product=deal.product,
            seats=deal.seats,
            value=deal.value,
            currency=deal.currency,
            stage=stage,
            stage_index=stage.pipeline_index,
            stage_total=DEAL_PIPELINE_TOTAL_STEPS,
            stage_label=stage.label,
            status=status,
            parked_until=deal.parked_until,
            resume_stage=(DealStage(deal.resume_stage) if deal.resume_stage else None),
            close_reason=deal.close_reason,
            close_note=deal.close_note,
            closed_at=deal.closed_at,
            age_days=(today - first_date).days,
            idle_days=(today - last_date).days,
            weighted_value=weighted_value,
            latest_record=history[-1] if history else None,
            stage_history=history,
            billing_profile=billing_profile,
            created_at=deal.created_at,
            updated_at=deal.updated_at,
            version=deal.version,
        )

    @staticmethod
    def _owner_response(owner: User) -> DealOwnerResponse:
        name = f"{owner.first_name} {owner.last_name}".strip()
        first_initial = (owner.first_name or " ")[0]
        last_initial = (owner.last_name or " ")[0]
        initials = f"{first_initial}{last_initial}".strip().upper()
        return DealOwnerResponse(id=owner.id, name=name, initials=initials)

    # INTERNALS

    def _record(self, deal: Deal, stage_label: str, note: str) -> DealActivity:
        """Append one stage-record row (same transaction as the mutation).

        Appended through the relationship so the in-session history stays
        current for response building within the same request.
        """
        activity = DealActivity(
            stage_label=stage_label,
            note=note,
            activity_date=reporting_date(),
            author_id=self.actor_id,
        )
        deal.activities.append(activity)
        self._db.flush()
        return activity

    @staticmethod
    def _require_note(note: str | None) -> str:
        """Reject empty/blank notes with 422 (drawer contract).

        The request schemas enforce this too; this guard covers direct
        service calls so the invariant can never be bypassed.
        """
        stripped = (note or "").strip()
        if not stripped:
            raise ValidationException(
                detail=NOTE_REQUIRED_MESSAGE,
                errors=[{"field": "note", "message": NOTE_REQUIRED_MESSAGE}],
            )
        return stripped

    @staticmethod
    def _assert_open(deal: Deal, action: str) -> None:
        """Open-only actions: closed and parked deals never advance."""
        if deal.status != DealStatus.OPEN:
            raise BadRequestException(
                detail=(
                    f"Cannot {action} a {status_value(deal.status)} deal. "
                    "Only open deals can change stage."
                ),
                field="status",
            )

    def _validate_owner(self, owner_id: uuid.UUID) -> User:
        owner = self._db.query(User).filter(User.id == owner_id).first()
        if not owner:
            raise NotFoundException(
                detail=f"User with ID '{owner_id}' not found", resource="user"
            )
        return owner

    def _validate_currency(self, customer: Customer, currency: str) -> None:
        """The deal currency must be one of the customer's billing-profile

        currencies (issue #43 contract) — the deal always has a billing
        line to post to.
        """
        exists = (
            self._db.query(CustomerBillingProfile.id)
            .filter(
                CustomerBillingProfile.customer_id == customer.id,
                CustomerBillingProfile.currency == currency,
            )
            .first()
        )
        if not exists:
            available = sorted(p.currency for p in customer.billing_profiles)
            raise BadRequestException(
                detail=(
                    f"Deal currency '{currency}' does not match any of the "
                    f"customer's billing-profile currencies {available}."
                ),
                field="currency",
            )

    @staticmethod
    def _snapshot(deal: Deal) -> dict[str, Any]:
        """Serialisable audit snapshot of a deal's state."""
        return {
            "customer_id": str(deal.customer_id),
            "owner_id": str(deal.owner_id),
            "product": deal.product,
            "seats": deal.seats,
            "value": str(deal.value),
            "currency": deal.currency,
            "stage": status_value(deal.stage),
            "status": status_value(deal.status),
        }
