"""audit_events append-only DB control (#71, !77 review §4).

Pins that the BEFORE UPDATE OR DELETE trigger installed by
app/common/audit_triggers.py (and by migration b0c1d2e3f4a5 in
production) raises on any attempt to rewrite the trail, so append-only
is a database control, not an application convention.

PostgreSQL-only: the trigger is PL/pgSQL and is deliberately not
installed on the SQLite fallback.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.common.audit import AuditEvent
from tests.conftest import USING_POSTGRES

pytestmark = pytest.mark.skipif(
    not USING_POSTGRES,
    reason="append-only trigger is PL/pgSQL (PostgreSQL only)",
)


def _seed_event(db) -> AuditEvent:
    event = AuditEvent(
        entity_type="owner_profile",
        entity_id=uuid.uuid4(),
        action="owner_suspended",
        actor_id=None,
        before={"status": "active"},
        after={"status": "suspended"},
    )
    db.add(event)
    db.commit()
    return event


class TestAuditEventsAppendOnly:
    def test_insert_allowed(self, db):
        event = _seed_event(db)
        assert db.get(AuditEvent, event.id) is not None

    def test_update_raises(self, db):
        event = _seed_event(db)
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(
                text("UPDATE audit_events SET action = 'tampered' WHERE id = :id"),
                {"id": event.id},
            )
        db.rollback()

    def test_delete_raises(self, db):
        event = _seed_event(db)
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(
                text("DELETE FROM audit_events WHERE id = :id"),
                {"id": event.id},
            )
        db.rollback()
        # The row survives the attempt.
        db.expire_all()
        assert db.get(AuditEvent, event.id) is not None
