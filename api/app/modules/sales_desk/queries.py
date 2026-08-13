"""Read-side SQL for the Sales Desk analytics module (issue #45).

All methods are pure reads executed on the caller's session — under the
sales-desk dependency that session sits in ONE read-only REPEATABLE READ
transaction (PostgreSQL), so every number in a response comes from the same
snapshot.

Aggregation strategy: deals are a low-cardinality workspace (a desk's
pipeline, not a ledger), and the derived per-deal values (age/idle days,
weighted value) need the org-local date and Decimal FX conversions that live
in Python. The repository therefore returns slim per-deal rows (scalars
only, no ORM entities) and the service folds them — mirroring how the deal
list itself derives these values, so the two surfaces cannot disagree.
"""

import uuid
from datetime import datetime

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.constants.enums import DealStatus
from app.modules.auth.models import User
from app.modules.customers.models import Customer, CustomerBillingProfile
from app.modules.deals.models import Deal, DealActivity
from app.modules.nurture.models import NurtureProspect


class SalesDeskRepository:
    """Read-only queries for dashboard, notifications and pipeline overview."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # Deals

    def open_deal_rows(self, owner_id: uuid.UUID | None = None):
        """Slim rows for every OPEN deal (parked/closed excluded).

        Returns (id, owner_id, stage, value, currency, created_at,
        first_activity, last_activity) — everything the open-pipeline
        aggregates and hygiene buckets need in one query.
        """
        first_activity = (
            select(func.min(DealActivity.activity_date))
            .where(DealActivity.deal_id == Deal.id)
            .correlate(Deal)
            .scalar_subquery()
        )
        last_activity = (
            select(func.max(DealActivity.activity_date))
            .where(DealActivity.deal_id == Deal.id)
            .correlate(Deal)
            .scalar_subquery()
        )
        query = self.db.query(
            Deal.id,
            Deal.owner_id,
            Deal.stage,
            Deal.value,
            Deal.currency,
            Deal.created_at,
            first_activity.label("first_activity"),
            last_activity.label("last_activity"),
        ).filter(Deal.status == DealStatus.OPEN.value)
        if owner_id:
            query = query.filter(Deal.owner_id == owner_id)
        return query.all()

    def closed_deal_rows(
        self,
        start_utc: datetime,
        end_utc: datetime,
        owner_id: uuid.UUID | None = None,
    ):
        """(owner_id, status, value, currency, closed_at) for deals closed

        in ``[start_utc, end_utc)``. Callers derive org-local buckets from
        ``closed_at`` (the real close timestamp, not activity records).
        """
        query = self.db.query(
            Deal.owner_id,
            Deal.status,
            Deal.value,
            Deal.currency,
            Deal.closed_at,
        ).filter(
            Deal.status.in_([DealStatus.WON.value, DealStatus.LOST.value]),
            Deal.closed_at >= start_utc,
            Deal.closed_at < end_utc,
        )
        if owner_id:
            query = query.filter(Deal.owner_id == owner_id)
        return query.all()

    def closed_counts_by_owner(self, owner_id: uuid.UUID | None = None):
        """All-time (owner_id, status, count) over won/lost deals."""
        query = (
            self.db.query(Deal.owner_id, Deal.status, func.count(Deal.id))
            .filter(Deal.status.in_([DealStatus.WON.value, DealStatus.LOST.value]))
            .group_by(Deal.owner_id, Deal.status)
        )
        if owner_id:
            query = query.filter(Deal.owner_id == owner_id)
        return query.all()

    def total_deal_count(self, owner_id: uuid.UUID | None = None) -> int:
        """Every deal in scope, any status (the 'All deals' chip)."""
        query = self.db.query(func.count(Deal.id))
        if owner_id:
            query = query.filter(Deal.owner_id == owner_id)
        return query.scalar() or 0

    def deal_owners(self, owner_id: uuid.UUID | None = None):
        """Users owning at least one deal, ordered by name (the reps)."""
        query = (
            self.db.query(User.id, User.first_name, User.last_name)
            .filter(exists().where(Deal.owner_id == User.id))
            .order_by(User.first_name, User.last_name, User.id)
        )
        if owner_id:
            query = query.filter(User.id == owner_id)
        return query.all()

    # Companies

    def recent_customers(self, owner_id: uuid.UUID | None = None, limit: int = 4):
        """Most recently added companies, newest first."""
        query = self.db.query(
            Customer.id,
            Customer.customer_type,
            Customer.company_name,
            Customer.first_name,
            Customer.last_name,
            Customer.industry,
            Customer.currency,
            Customer.primary_currency,
            Customer.created_at,
        )
        if owner_id:
            query = query.filter(Customer.owner_id == owner_id)
        return (
            query.order_by(Customer.created_at.desc(), Customer.id.desc())
            .limit(limit)
            .all()
        )

    def unsynced_companies_count(self, owner_id: uuid.UUID | None = None) -> int:
        """Companies with at least one unsynced billing profile (#43)."""
        unsynced_exists = exists().where(
            CustomerBillingProfile.customer_id == Customer.id,
            CustomerBillingProfile.synced.is_(False),
        )
        query = self.db.query(func.count(Customer.id)).filter(unsynced_exists)
        if owner_id:
            query = query.filter(Customer.owner_id == owner_id)
        return query.scalar() or 0

    def _desk_company_query(self):
        """The slim company row shared by the directory and the single-row

        lookup, so the two can never disagree about a company's shape.
        """
        return self.db.query(
            Customer.id,
            Customer.company_name,
            Customer.first_name,
            Customer.last_name,
            Customer.email,
            Customer.phone,
            Customer.industry,
            Customer.tenant_domain,
            Customer.owner_id,
            Customer.currency,
            Customer.primary_currency,
            Customer.created_at,
        )

    def desk_company_by_id(self, customer_id: uuid.UUID):
        """One company row for the desk, or ``None`` when absent."""
        return self._desk_company_query().filter(Customer.id == customer_id).first()

    def desk_companies(
        self,
        owner_id: uuid.UUID | None = None,
        search: str | None = None,
        unsynced_only: bool = False,
        limit: int = 200,
    ):
        """Companies for the desk directory, newest registration first.

        Returns ``(rows, total)``. ``total`` counts every match before the
        limit so the caller can show a truthful count without a second query.
        """
        query = self._desk_company_query()

        if owner_id:
            query = query.filter(Customer.owner_id == owner_id)

        if unsynced_only:
            # Served directly by ix_customer_billing_profiles_unsynced_customer.
            query = query.filter(
                exists().where(
                    CustomerBillingProfile.customer_id == Customer.id,
                    CustomerBillingProfile.synced.is_(False),
                )
            )

        if search:
            term = f"%{search.strip().lower()}%"
            query = query.filter(
                func.lower(
                    func.coalesce(Customer.company_name, "")
                    + " "
                    + Customer.first_name
                    + " "
                    + Customer.last_name
                    + " "
                    + func.coalesce(Customer.industry, "")
                    + " "
                    + func.coalesce(Customer.tenant_domain, "")
                ).like(term)
            )

        total = query.count()
        rows = (
            query.order_by(Customer.created_at.desc(), Customer.id.desc())
            .limit(limit)
            .all()
        )
        return rows, total

    def users_by_id(self, user_ids: list[uuid.UUID]):
        """Owner rows for the given ids, for rendering the owner column."""
        if not user_ids:
            return []
        return (
            self.db.query(User.id, User.first_name, User.last_name)
            .filter(User.id.in_(user_ids))
            .all()
        )

    def billing_profiles_for(self, customer_ids: list[uuid.UUID]):
        """Every billing profile for the given customers, in one query."""
        if not customer_ids:
            return []
        return (
            self.db.query(CustomerBillingProfile)
            .filter(CustomerBillingProfile.customer_id.in_(customer_ids))
            .order_by(CustomerBillingProfile.currency)
            .all()
        )

    def deal_counts_for(self, customer_ids: list[uuid.UUID]):
        """``(customer_id, status, count)`` triples for the given customers."""
        if not customer_ids:
            return []
        return (
            self.db.query(
                Deal.customer_id,
                Deal.status,
                func.count(Deal.id),
            )
            .filter(Deal.customer_id.in_(customer_ids))
            .group_by(Deal.customer_id, Deal.status)
            .all()
        )

    # Future pipeline (nurture prospects + parked deals, #40)

    def nurture_due_counts(
        self, today, upcoming_cutoff, owner_id: uuid.UUID | None = None
    ) -> tuple[int, int]:
        """(due, upcoming) nurture-prospect counts at org-local ``today``."""
        base = self.db.query(func.count(NurtureProspect.id))
        if owner_id:
            base = base.filter(NurtureProspect.owner_id == owner_id)
        due = base.filter(NurtureProspect.engage_on <= today).scalar() or 0
        upcoming = (
            base.filter(
                NurtureProspect.engage_on > today,
                NurtureProspect.engage_on <= upcoming_cutoff,
            ).scalar()
            or 0
        )
        return due, upcoming

    def parked_due_counts(
        self, today, upcoming_cutoff, owner_id: uuid.UUID | None = None
    ) -> tuple[int, int]:
        """(due, upcoming) parked-deal counts at org-local ``today``."""
        base = self.db.query(func.count(Deal.id)).filter(
            Deal.status == DealStatus.PARKED.value
        )
        if owner_id:
            base = base.filter(Deal.owner_id == owner_id)
        due = base.filter(Deal.parked_until <= today).scalar() or 0
        upcoming = (
            base.filter(
                Deal.parked_until > today,
                Deal.parked_until <= upcoming_cutoff,
            ).scalar()
            or 0
        )
        return due, upcoming

    def stale_open_deals_count(
        self, stale_before, owner_id: uuid.UUID | None = None
    ) -> int:
        """Open deals whose latest activity is older than ``stale_before``.

        Same predicate as the NO_ACTIVITY_30 hygiene list filter
        (idle > 30 days, i.e. last activity date < today - 30d).
        """
        last_activity = (
            select(func.max(DealActivity.activity_date))
            .where(DealActivity.deal_id == Deal.id)
            .correlate(Deal)
            .scalar_subquery()
        )
        query = self.db.query(func.count(Deal.id)).filter(
            Deal.status == DealStatus.OPEN.value,
            last_activity < stale_before,
        )
        if owner_id:
            query = query.filter(Deal.owner_id == owner_id)
        return query.scalar() or 0
