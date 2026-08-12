"""Onboarding business logic: auto-creation on won deals + task toggling.

Atomicity contract

All mutating methods call ``self._db.flush()`` only; commit/rollback is
owned by ``get_db()``. ``create_for_won_deal`` runs inside the SAME
transaction as ``DealService.close`` marking the deal won, so a rolled
back close never leaves an orphan checklist and a committed win never
misses one. Every audit row is written in the same transaction as the
mutation it records (``common/audit.py`` contract).

Template versioning

Tasks are COPIED from the org's owner-settings template at create time and
the template version is snapshotted on the checklist, so a later template
edit never mutates existing checklists.
"""

import logging
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.common.audit import record_audit_event
from app.common.document_service import ServiceBase
from app.common.exceptions import BadRequestException, NotFoundException
from app.common.pagination import PaginatedResponse, PaginationParams
from app.common.search import build_search_clause
from app.constants.enums import DealStatus
from app.modules.customers.models import Customer
from app.modules.deals.models import Deal
from app.modules.onboarding.models import Onboarding, OnboardingTask
from app.modules.onboarding.schemas import (
    OnboardingResponse,
    OnboardingTaskResponse,
)
from app.modules.owner.service import OwnerService

logger = logging.getLogger(__name__)

#: Screen contract: "Microsoft 365 E5 · 200 seats" (Onboarding.svg).
_PLAN_LABEL_FORMAT = "{product} · {seats} seats"


def plan_label_for(deal: Deal) -> str:
    """Build the display plan line the Onboarding list renders verbatim."""
    return _PLAN_LABEL_FORMAT.format(product=deal.product, seats=deal.seats)


class OnboardingService(ServiceBase):
    """Service layer for Sales Desk onboarding checklists."""

    # CREATE (only ever triggered by the won-deal close flow)

    def create_for_won_deal(self, deal: Deal) -> Onboarding:
        """Create the delivery checklist for a deal being closed WON.

        Called by ``DealService.close`` INSIDE the same transaction that
        marks the deal won (issue #41 atomicity requirement). Copies the
        org's task template (owner settings, falling back to the built-in
        7-task default) and snapshots its version.
        """
        if deal.status != DealStatus.WON:
            raise BadRequestException(
                detail="Onboarding checklists are only created for won deals.",
                field="status",
            )
        existing = (
            self._db.query(Onboarding.id)
            .filter(Onboarding.deal_id == deal.id)
            .first()
        )
        if existing:
            raise BadRequestException(
                detail="This deal already has an onboarding checklist.",
                field="deal_id",
            )

        template = OwnerService(self._db).onboarding_template()

        onboarding = Onboarding(
            deal_id=deal.id,
            customer_id=deal.customer_id,
            plan_label=plan_label_for(deal),
            template_version=template.version,
            tasks=[
                OnboardingTask(position=position, label=label)
                for position, label in enumerate(template.tasks, start=1)
            ],
        )
        self._db.add(onboarding)
        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=self.actor_id,
            entity_type="onboarding",
            entity_id=onboarding.id,
            action="created",
            after=self._snapshot(onboarding),
        )

        logger.info(
            "Created onboarding checklist for won deal",
            extra={
                "onboarding_id": str(onboarding.id),
                "deal_id": str(deal.id),
                "customer_id": str(deal.customer_id),
                "template_version": template.version,
            },
        )
        return onboarding

    # READ

    def get_by_id(self, onboarding_id: uuid.UUID) -> Onboarding:
        """Retrieve an onboarding by ID (customer and tasks loaded)."""
        onboarding = (
            self._db.query(Onboarding).filter(Onboarding.id == onboarding_id).first()
        )
        if not onboarding:
            raise NotFoundException(
                detail=f"Onboarding with ID '{onboarding_id}' not found",
                resource="onboarding",
            )
        return onboarding

    def list_onboardings(
        self, params: PaginationParams, search: str | None = None
    ) -> PaginatedResponse[OnboardingResponse]:
        """List onboarding checklists (newest first) with optional search."""
        query = self._db.query(Onboarding)

        clause = build_search_clause(
            search or "",
            Customer.company_name,
            Customer.first_name,
            Customer.last_name,
            Onboarding.plan_label,
        )
        if clause is not None:
            query = query.join(
                Customer, Onboarding.customer_id == Customer.id
            ).filter(clause)

        total = query.count() if params.with_total else None

        rows = (
            query.order_by(Onboarding.created_at.desc())
            .offset(params.offset)
            .limit(params.fetch_limit)
            .all()
        )

        items = [self.build_response(o) for o in rows]
        return PaginatedResponse.create_from_window(items, params, total=total)

    # MUTATIONS

    def toggle_task(self, onboarding_id: uuid.UUID, position: int) -> Onboarding:
        """Flip one step's done flag; recompute completion server-side.

        Marking a step done stamps its ``completed_at``; re-opening it
        clears the stamp. The checklist-level ``completed_at`` is set when
        the last step is done and cleared again if any step is re-opened,
        so the record and its counts can never disagree.
        """
        onboarding = self.get_by_id(onboarding_id)
        task = next((t for t in onboarding.tasks if t.position == position), None)
        if task is None:
            raise NotFoundException(
                detail=(
                    f"Onboarding '{onboarding_id}' has no task at "
                    f"position {position}"
                ),
                resource="onboarding_task",
            )

        before_done = task.done
        task.done = not task.done
        task.completed_at = datetime.now(UTC) if task.done else None
        self._sync_completion(onboarding)
        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=self.actor_id,
            entity_type="onboarding",
            entity_id=onboarding.id,
            action="task_toggled",
            before={"position": position, "done": before_done},
            after={"position": position, "done": task.done},
        )
        return onboarding

    def mark_complete(self, onboarding_id: uuid.UUID) -> Onboarding:
        """Mark every remaining step done and stamp the checklist complete."""
        onboarding = self.get_by_id(onboarding_id)
        if onboarding.is_complete:
            raise BadRequestException(
                detail="This onboarding is already complete.",
                field="completed_at",
            )

        now = datetime.now(UTC)
        remaining = [t.position for t in onboarding.tasks if not t.done]
        for task in onboarding.tasks:
            if not task.done:
                task.done = True
                task.completed_at = now
        self._sync_completion(onboarding)
        self._db.flush()

        record_audit_event(
            self._db,
            actor_id=self.actor_id,
            entity_type="onboarding",
            entity_id=onboarding.id,
            action="completed",
            before={"remaining_positions": remaining},
            after=self._snapshot(onboarding),
        )
        return onboarding

    @staticmethod
    def _sync_completion(onboarding: Onboarding) -> None:
        """Keep the checklist-level completion stamp consistent with tasks."""
        if all(t.done for t in onboarding.tasks):
            if onboarding.completed_at is None:
                onboarding.completed_at = datetime.now(UTC)
        else:
            onboarding.completed_at = None

    # RESPONSE BUILDING (server-computed derived fields)

    def build_response(self, onboarding: Onboarding) -> OnboardingResponse:
        """Serialize a checklist exactly as the Onboarding screen consumes it.

        Percent and the "N of M" counts are recomputed here on every read
        and mutation so the UI's optimistic update can always reconcile
        against authoritative values.
        """
        steps = sorted(onboarding.tasks, key=lambda t: t.position)
        done = sum(1 for t in steps if t.done)
        total = len(steps)

        return OnboardingResponse(
            id=onboarding.id,
            deal_id=onboarding.deal_id,
            customer_id=onboarding.customer_id,
            company_name=onboarding.customer.display_name,
            plan_label=onboarding.plan_label,
            percent_complete=self._percent(done, total),
            tasks_completed=done,
            tasks_total=total,
            steps=[OnboardingTaskResponse.model_validate(t) for t in steps],
            template_version=onboarding.template_version,
            is_complete=onboarding.is_complete,
            completed_at=onboarding.completed_at,
            created_at=onboarding.created_at,
            updated_at=onboarding.updated_at,
        )

    @staticmethod
    def _percent(done: int, total: int) -> int:
        """Half-up integer percent (3/7 → 43, 6/7 → 86, per Onboarding.svg)."""
        if total == 0:
            return 0
        return int(
            (Decimal(done * 100) / Decimal(total)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )

    @staticmethod
    def _snapshot(onboarding: Onboarding) -> dict[str, Any]:
        """Serialisable audit snapshot of a checklist's state."""
        return {
            "deal_id": str(onboarding.deal_id),
            "customer_id": str(onboarding.customer_id),
            "plan_label": onboarding.plan_label,
            "template_version": onboarding.template_version,
            "tasks_total": len(onboarding.tasks),
            "tasks_completed": sum(1 for t in onboarding.tasks if t.done),
        }
