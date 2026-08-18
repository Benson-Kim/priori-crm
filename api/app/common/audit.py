"""Append-only audit trail for financial mutations.

Every payment, cancellation and hard delete writes an ``audit_events`` row
IN THE SAME TRANSACTION as the mutation itself (``record_audit_event`` only
flushes; the caller's commit/rollback governs both), so the trail can never
disagree with the ledger. Rows are never updated or deleted — enforced at
the database by a BEFORE UPDATE OR DELETE trigger that raises
(app/common/audit_triggers.py, migration b0c1d2e3f4a5), not just by
application convention: this table is evidence, not state.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.common.database import Base

logger = logging.getLogger(__name__)


def status_value(status: Any) -> str:
    """Normalize a status (raw column string or Enum member) to its value.

    Status attributes are plain strings when loaded from the DB but Enum
    members right after an in-memory assignment; ``str()`` on a str-mixin
    Enum renders ``'ExpenseStatus.CANCELED'`` on modern Pythons, so audit
    payloads must normalize explicitly.
    """
    return getattr(status, "value", status)


class AuditEvent(Base):
    """One immutable record of a financially significant mutation."""

    __tablename__ = "audit_events"

    __table_args__ = (
        # Typical query: "show me everything that happened to entity X".
        Index("ix_audit_events_entity", "entity_type", "entity_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Authenticated user who performed the action (null = system)",
    )
    entity_type: Mapped[str] = mapped_column(
        String(30), nullable=False, comment="invoice | quote | expense | ..."
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        comment="payment_recorded | canceled | soft_deleted | hard_deleted | ...",
    )
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditEvent({self.entity_type}/{self.entity_id} "
            f"{self.action} by {self.actor_id})>"
        )


def record_audit_event(
    db: Session,
    *,
    actor_id: Any | None,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    before: dict | None = None,
    after: dict | None = None,
) -> AuditEvent:
    """Write one audit row inside the caller's open transaction.

    Flush-only by design: atomicity with the audited mutation is the whole
    point — a rolled-back payment must not leave a phantom audit record,
    and a committed one must never be missing its trail.
    """
    event = AuditEvent(
        actor_id=actor_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        before=before,
        after=after,
    )
    db.add(event)
    db.flush()
    return event
