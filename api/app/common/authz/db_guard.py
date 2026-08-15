"""Zero-trust guard at the database layer (#67).

The HTTP gate (``app.common.authz.enforcement``) is the primary enforcement
point, but zero trust means *no implicit trust carried from a prior
successful auth* — including by code that reaches the ORM without having
passed the gate. This module closes that gap:

- ``get_db`` marks every request-scoped session (``session.info``).
- A global SQLAlchemy listener refuses ORM statement execution AND flushes
  on a request-scoped session unless the current request's policy verdict
  is present and is ALLOW. A route that somehow bypassed the gate (a
  router mounted outside the app dependency, a future regression) fails
  closed at its first database touch instead of silently reading data.

Sessions created outside a request (schedulers via ``get_db_context``,
scripts, the test fixtures' directly-built sessions) carry no marker and
are untouched: they are not HTTP requests, and their trust model (process
boundary / internal secret) is enforced elsewhere.

The guard deliberately has one narrow bypass, used only by the authz
module itself: persisting the audit row for a DENY / CHALLENGE / TERMINATE
decision requires a write on a session whose verdict is, by definition,
not ALLOW. The decision evidence must never be blocked by the decision.
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session

from app.common.authz.engine import PolicyVerdict
from app.common.exceptions import ForbiddenException
from app.lib.config import settings

logger = logging.getLogger(__name__)

#: session.info key: this session belongs to an HTTP request (set by get_db).
REQUEST_SCOPED_KEY = "zero_trust_request_scoped"
#: session.info key: the PolicyVerdict the gate stored for this request.
VERDICT_KEY = "zero_trust_verdict"
#: session.info key: authz-internal bypass for persisting decision audits.
AUDIT_BYPASS_KEY = "zero_trust_audit_bypass"

_installed = False


def mark_request_scoped(session: Session) -> None:
    """Tag a session as request-scoped (called by ``get_db``)."""
    session.info[REQUEST_SCOPED_KEY] = True


def set_verdict(session: Session, verdict: PolicyVerdict) -> None:
    """Publish the request's policy verdict for the DB-layer guard."""
    session.info[VERDICT_KEY] = verdict


def get_verdict(session: Session) -> PolicyVerdict | None:
    """The policy verdict stored on a session, if any."""
    verdict = session.info.get(VERDICT_KEY)
    return verdict if isinstance(verdict, PolicyVerdict) else None


@contextmanager
def audit_bypass(session: Session) -> Generator[None, None, None]:
    """Allow the authz module to persist decision evidence on any verdict."""
    session.info[AUDIT_BYPASS_KEY] = True
    try:
        yield
    finally:
        session.info.pop(AUDIT_BYPASS_KEY, None)


def _refuse_if_uncleared(session: Session) -> None:
    """Fail closed when a request-scoped session has no ALLOW verdict."""
    if not settings.ABAC_ENABLED:
        return
    if not session.info.get(REQUEST_SCOPED_KEY):
        return
    if session.info.get(AUDIT_BYPASS_KEY):
        return
    verdict = get_verdict(session)
    if verdict is not None and verdict.allowed:
        return
    logger.warning(
        "Zero-trust DB guard refused database access",
        extra={
            "verdict": verdict.decision.value if verdict else None,
            "rule": verdict.rule if verdict else None,
        },
    )
    raise ForbiddenException(
        detail=(
            "Database access refused: this request carries no cleared "
            "access-policy verdict."
        ),
        required_permission="zero_trust:allow",
    )


def install_zero_trust_db_guard() -> None:
    """Register the guard once, for every ORM Session in the process."""
    global _installed
    if _installed:
        return
    _installed = True

    @event.listens_for(Session, "do_orm_execute")
    def _guard_orm_execute(orm_execute_state: ORMExecuteState) -> None:
        _refuse_if_uncleared(orm_execute_state.session)

    @event.listens_for(Session, "before_flush")
    def _guard_flush(session: Session, flush_context: Any, instances: Any) -> None:
        _refuse_if_uncleared(session)
