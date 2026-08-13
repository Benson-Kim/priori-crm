"""Future pipeline business logic: nurture prospects, engage, due counts.

Atomicity contract

All mutating methods call ``self._db.flush()`` only; commit/rollback is
owned by ``get_db()``. The engage action additionally wraps its three
mutations (create customer → create deal → remove prospect) in a SAVEPOINT
so a failure at ANY step rolls all of them back — a customer can never be
orphaned by a failed deal creation, regardless of the caller's transaction
handling.

Due logic (server-enforced — the UI only renders)

``due_state`` (overdue / today / in_days: N), the ``dot_state`` status dot
and every notification count are computed against the org-local accounting
date boundary via ``common/reporting_time.reporting_date()``. The UI never
does date math.
"""

import logging
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import case, func

from app.common.audit import record_audit_event
from app.common.document_service import ServiceBase
from app.common.exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.reporting_time import reporting_date
from app.common.search import build_search_clause
from app.constants.enums import DealStatus
from app.modules.auth.models import User
from app.modules.customers.models import Customer
from app.modules.customers.service import CustomerService
from app.modules.deals.models import Deal
from app.modules.deals.schemas import DealCreate, DealOwnerResponse, DealResponse
from app.modules.deals.service import DealService
from app.modules.nurture.models import NurtureProspect
from app.modules.nurture.schemas import (
    NurtureDueState,
    NurtureEngageRequest,
    NurtureNotificationCounts,
    NurtureProspectCreate,
    NurtureProspectResponse,
    NurtureSummary,
)

logger = logging.getLogger(__name__)

#: Server-side default for a missing engage-by date (the UI prefills +90d).
DEFAULT_ENGAGE_IN_DAYS = 90

#: Days ahead counted as "upcoming" on the notification bell (#45).
UPCOMING_WINDOW_DAYS = 14

#: First stage-record note when the engage request carries none.
_DEFAULT_ENGAGE_NOTE = "Engaged from the future pipeline nurture list."


def _default_engage_product(db) -> str:
    """First product of the org catalog, the default a one-click engage opens on.

    Resolution mirrors ``GET /owner/settings/sales-pricing`` (issue #44):
    the org's settings-resolved catalog first — so an org that customized
    its catalog never gets a deal opened on a product it does not sell —
    then the seed constant, then a generic 'Subscription' placeholder so a
    one-click engage can never fail on catalog configuration.
    """
    from app.constants.settings_defaults import DEFAULT_PRODUCT_CATALOG
    from app.modules.owner.service import OwnerService

    entry = next(iter(OwnerService(db).sales_pricing_settings().catalog), None)
    if entry is not None and entry.name:
        return entry.name

    first = next(iter(DEFAULT_PRODUCT_CATALOG), None)
    if first is None:
        return "Subscription"
    return first["name"] if isinstance(first, dict) else str(first)


class NurtureService(ServiceBase):
    """Service layer for the Sales Desk future pipeline."""

    # CREATE

    def create(
        self, data: NurtureProspectCreate, user_id: uuid.UUID | None = None
    ) -> NurtureProspect:
        """Create a planned prospect ('+ Add planned deal')."""
        if data.owner_id is not None:
            self._validate_owner(data.owner_id)

        engage_on = data.engage_on or reporting_date() + timedelta(
            days=DEFAULT_ENGAGE_IN_DAYS
        )

        prospect = NurtureProspect(
            owner_id=data.owner_id,
            company_name=data.company.strip(),
            contact=(data.contact or "").strip() or None,
            note=(data.note or "").strip() or None,
            engage_on=engage_on,
            est_annual_value=data.est_arr,
            created_by=user_id,
        )
        self._db.add(prospect)
        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=user_id,
            entity_type="nurture_prospect",
            entity_id=prospect.id,
            action="created",
            after=self._snapshot(prospect),
        )

        logger.info(
            "Created nurture prospect",
            extra={
                "prospect_id": str(prospect.id),
                "company": prospect.company_name,
                "engage_on": prospect.engage_on.isoformat(),
                "created_by": str(user_id) if user_id else None,
            },
        )
        return prospect

    # READ

    def get_by_id(self, prospect_id: uuid.UUID) -> NurtureProspect:
        """Retrieve a prospect by ID or raise 404."""
        prospect = (
            self._db.query(NurtureProspect)
            .filter(NurtureProspect.id == prospect_id)
            .first()
        )
        if not prospect:
            raise NotFoundException(
                detail=f"Nurture prospect with ID '{prospect_id}' not found",
                resource="nurture_prospect",
            )
        return prospect

    def list_prospects(
        self,
        params: PaginationParams,
        search: str | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> PaginatedResponse[NurtureProspectResponse]:
        """List prospects soonest-due first, with search + owner filters."""
        query = self._apply_filters(self._db.query(NurtureProspect), search, owner_id)

        total = query.count() if params.with_total else None

        rows = (
            query.order_by(
                NurtureProspect.engage_on,
                NurtureProspect.created_at,
            )
            .offset(params.offset)
            .limit(params.fetch_limit)
            .all()
        )

        today = reporting_date()
        items = [self.build_response(p, today=today) for p in rows]
        return PaginatedResponse.create_from_window(items, params, total=total)

    def get_summary(
        self,
        search: str | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> NurtureSummary:
        """Header aggregates: count, total est. ARR and due-now count.

        Computed over the same filtered set as the list so the header
        ("Nurture list — 4 companies · est. $67,020 potential ARR",
        "1 due now") always matches the rows below it.
        """
        today = reporting_date()
        query = self._apply_filters(self._db.query(NurtureProspect), search, owner_id)
        row = query.with_entities(
            func.count(NurtureProspect.id),
            func.coalesce(func.sum(NurtureProspect.est_annual_value), 0),
            func.coalesce(
                func.sum(case((NurtureProspect.engage_on <= today, 1), else_=0)),
                0,
            ),
        ).one()
        return NurtureSummary(count=row[0], total_est_arr=row[1], due_now=row[2])

    def get_notification_counts(self) -> NurtureNotificationCounts:
        """Due counts feeding the sidebar badge / notification bell (#45).

        - due nurture: ``engage_on <= today`` (org-local boundary);
        - upcoming nurture: due within the next ``UPCOMING_WINDOW_DAYS``
          days (exclusive of today — those are already due);
        - due parked: parked deals whose ``parked_until`` has arrived.
        """
        today = reporting_date()
        upcoming_cutoff = today + timedelta(days=UPCOMING_WINDOW_DAYS)

        due_nurture = (
            self._db.query(func.count(NurtureProspect.id))
            .filter(NurtureProspect.engage_on <= today)
            .scalar()
        )
        upcoming_nurture = (
            self._db.query(func.count(NurtureProspect.id))
            .filter(
                NurtureProspect.engage_on > today,
                NurtureProspect.engage_on <= upcoming_cutoff,
            )
            .scalar()
        )
        due_parked = (
            self._db.query(func.count(Deal.id))
            .filter(
                Deal.status == DealStatus.PARKED.value,
                Deal.parked_until <= today,
            )
            .scalar()
        )
        return NurtureNotificationCounts(
            due_nurture=due_nurture or 0,
            upcoming_nurture=upcoming_nurture or 0,
            due_parked=due_parked or 0,
        )

    def _apply_filters(self, query, search: str | None, owner_id: uuid.UUID | None):
        """Shared list/summary filters (single source of truth)."""
        if owner_id:
            query = query.filter(NurtureProspect.owner_id == owner_id)
        clause = build_search_clause(
            search or "",
            NurtureProspect.company_name,
            NurtureProspect.contact,
        )
        if clause is not None:
            query = query.filter(clause)
        return query

    # ENGAGE

    def engage(self, prospect_id: uuid.UUID, data: NurtureEngageRequest) -> Deal:
        """Engage a prospect: customer + open deal at Activation, atomically.

        Creates the customer through the existing customers service
        (reusing its validation and dedup, incl. case-insensitive email
        uniqueness) — or links an existing one via ``customer_id`` (the
        re-submit path offered by the duplicate 409) — then creates an open
        deal at stage Activation and removes the prospect. All three
        mutations share one SAVEPOINT: if deal creation fails, the customer
        insert rolls back with it and no orphan customer remains.
        """
        prospect = self.get_by_id(prospect_id)

        owner_id = data.owner_id or prospect.owner_id
        if owner_id is None:
            raise BadRequestException(
                detail=(
                    "An owner is required to engage — assign one on the "
                    "prospect or pass owner_id."
                ),
                field="owner_id",
            )

        value = data.value if data.value is not None else prospect.est_annual_value
        if value is None:
            raise BadRequestException(
                detail=(
                    "A deal value is required to engage: the prospect has "
                    "no est. ARR to default from."
                ),
                field="value",
            )

        note = (
            (data.note or "").strip()
            or (prospect.note or "").strip()
            or _DEFAULT_ENGAGE_NOTE
        )

        # "Start engaging" is one click on the nurture card (#40, #48): the
        # screen collects nothing, so the deal opens on the org's first
        # catalogued product at a single seat and the rep adjusts both in the
        # deal drawer. Anything the caller does send still wins.
        product = data.product or _default_engage_product(self._db)
        seats = data.seats or 1

        customer_service = CustomerService(self._db, current_user=self._current_user)
        deal_service = DealService(self._db, current_user=self._current_user)
        snapshot = self._snapshot(prospect)

        try:
            with self._db.begin_nested():
                if data.customer_id is not None:
                    customer = customer_service.get_by_id(data.customer_id)
                else:
                    customer = customer_service.create(data.customer)

                currency = data.currency or (
                    customer.primary_currency or customer.currency
                )

                deal = deal_service.create(
                    DealCreate(
                        customer_id=customer.id,
                        owner_id=owner_id,
                        product=product,
                        seats=seats,
                        value=value,
                        currency=currency,
                        note=note,
                    ),
                    user_id=self.actor_id,
                )

                self._db.delete(prospect)
                self._db.flush()

                record_audit_event(
                    self._db,
                    actor_id=self.actor_id,
                    entity_type="nurture_prospect",
                    entity_id=prospect_id,
                    action="engaged",
                    before=snapshot,
                    after={
                        "customer_id": str(customer.id),
                        "deal_id": str(deal.id),
                    },
                )
        except ConflictException as exc:
            raise self._duplicate_customer_conflict(exc, data) from exc

        logger.info(
            "Engaged nurture prospect",
            extra={
                "prospect_id": str(prospect_id),
                "customer_id": str(deal.customer_id),
                "deal_id": str(deal.id),
                "linked_existing_customer": data.customer_id is not None,
            },
        )
        return deal

    def _duplicate_customer_conflict(
        self, exc: ConflictException, data: NurtureEngageRequest
    ) -> ConflictException:
        """Enrich a duplicate-email 409 with the re-submit-linking option.

        The customers service already refused the duplicate (its dedup is
        the single source of truth); here the existing customer's id is
        attached so the UI (#48) can offer "link the deal to the existing
        customer" by re-submitting with ``customer_id``.
        """
        if data.customer is None or exc.extra.get("field") != "email":
            return exc
        email = str(data.customer.email).strip().lower()
        existing = self._db.query(Customer).filter(Customer.email == email).first()
        if existing is None:
            return exc
        enriched = ConflictException(
            detail=(
                f"Customer with email '{email}' already exists. Re-submit "
                "with customer_id to link the deal to the existing customer."
            ),
            field="email",
        )
        enriched.extra["customer_id"] = str(existing.id)
        return enriched

    def build_deal_response(self, deal: Deal) -> DealResponse:
        """Serialize the engage-created deal via the deals service.

        The engage endpoint returns the deal exactly as the pipeline
        consumes it, so the UI can navigate straight to it.
        """
        deal_service = DealService(self._db, current_user=self._current_user)
        return deal_service.build_response(deal)

    # DELETE

    def delete(self, prospect_id: uuid.UUID) -> None:
        """Remove a prospect without engaging it."""
        prospect = self.get_by_id(prospect_id)
        record_audit_event(
            self._db,
            actor_id=self.actor_id,
            entity_type="nurture_prospect",
            entity_id=prospect.id,
            action="hard_deleted",
            before=self._snapshot(prospect),
        )
        self._db.delete(prospect)
        self._db.flush()

    # RESPONSE BUILDING (server-computed derived fields)

    def build_response(
        self, prospect: NurtureProspect, today: date | None = None
    ) -> NurtureProspectResponse:
        """Serialize a prospect exactly as the screens consume it."""
        today = today or reporting_date()
        return NurtureProspectResponse(
            id=prospect.id,
            company=prospect.company_name,
            contact=prospect.contact,
            note=prospect.note,
            engage_on=prospect.engage_on,
            est_arr=prospect.est_annual_value,
            owner=self._owner_response(prospect.owner) if prospect.owner else None,
            due_state=self.compute_due_state(prospect.engage_on, today),
            dot_state="due" if prospect.engage_on <= today else "planned",
            created_at=prospect.created_at,
            updated_at=prospect.updated_at,
        )

    @staticmethod
    def compute_due_state(engage_on: date, today: date) -> NurtureDueState:
        """The due badge against the org-local date boundary.

        overdue (< today) / today (== today) / in_days: N (> today), with
        the chip label rendered verbatim ("Overdue" / "Today" / "45d").
        """
        if engage_on < today:
            return NurtureDueState(state="overdue", in_days=None, label="Overdue")
        if engage_on == today:
            return NurtureDueState(state="today", in_days=None, label="Today")
        days = (engage_on - today).days
        return NurtureDueState(state="in_days", in_days=days, label=f"{days}d")

    @staticmethod
    def _owner_response(owner: User) -> DealOwnerResponse:
        """Owner as the screens render it (same shape as the deals list)."""
        name = f"{owner.first_name} {owner.last_name}".strip()
        first_initial = (owner.first_name or " ")[0]
        last_initial = (owner.last_name or " ")[0]
        initials = f"{first_initial}{last_initial}".strip().upper()
        return DealOwnerResponse(id=owner.id, name=name, initials=initials)

    # INTERNALS

    def _validate_owner(self, owner_id: uuid.UUID) -> User:
        owner = self._db.query(User).filter(User.id == owner_id).first()
        if not owner:
            raise NotFoundException(
                detail=f"User with ID '{owner_id}' not found", resource="user"
            )
        return owner

    @staticmethod
    def _snapshot(prospect: NurtureProspect) -> dict[str, Any]:
        """Serialisable audit snapshot of a prospect's state."""
        return {
            "owner_id": str(prospect.owner_id) if prospect.owner_id else None,
            "company_name": prospect.company_name,
            "contact": prospect.contact,
            "note": prospect.note,
            "engage_on": prospect.engage_on.isoformat(),
            "est_annual_value": (
                str(prospect.est_annual_value)
                if prospect.est_annual_value is not None
                else None
            ),
        }
