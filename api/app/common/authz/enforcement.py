"""Zero-trust enforcement gate: runs on EVERY request before any handler.

Wired as an application-level dependency (``app/main.py``), so it executes
ahead of router module gates, ``require_role`` / ``require_privileged``
and endpoint bodies — for API calls, internal (service-to-service)
endpoints and everything else mounted on the app. It layers ON TOP of the
existing RBAC gates (they still run and still refuse), it never replaces
them, and it never widens access: its ALLOW only means "no contextual
objection".

Per request it:

1. assembles the :class:`AccessContext` (time of day, geolocation, device
   fingerprint, IP reputation, resource sensitivity);
2. evaluates the ABAC policy engine;
3. publishes the verdict to the DB-layer guard (``db_guard``) so database
   access is refused unless this request was explicitly cleared — no
   implicit trust from a prior successful auth;
4. audits the decision (allow / deny / challenge / terminate) to the
   append-only ``audit_events`` trail (#31). Non-allow decisions are
   committed durably BEFORE the request is rejected, so the evidence
   survives the rollback that the rejection triggers;
5. rejects the request on DENY (403), CHALLENGE (401 ``STEP_UP_REQUIRED``
   — the client re-runs the existing login → OTP flow) or TERMINATE (401).

PUBLIC-classified probes (health, ping) bypass evaluation entirely: a
load-balancer check must never be denied, challenged or audited.
"""

import logging
import uuid

from fastapi import Depends, Request
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.orm import Session

from app.common.audit import record_audit_event
from app.common.authz import db_guard
from app.common.authz.context import AccessContext, build_access_context
from app.common.authz.engine import Decision, PolicyVerdict, evaluate
from app.common.authz.sensitivity import SensitivityLevel
from app.common.database import get_db
from app.common.exceptions import (
    ForbiddenException,
    StepUpRequiredException,
    UnauthorizedException,
)
from app.lib.config import settings

logger = logging.getLogger(__name__)

#: entity_id for decisions with no authenticated principal.
_ANONYMOUS_ENTITY_ID = uuid.UUID(int=0)

_DISABLED_VERDICT = PolicyVerdict(
    decision=Decision.ALLOW, rule="abac_disabled", reason="ABAC disabled by settings"
)
_PUBLIC_VERDICT = PolicyVerdict(
    decision=Decision.ALLOW, rule="public_exempt", reason="Public probe endpoint"
)


def _decision_payload(context: AccessContext, verdict: PolicyVerdict) -> dict:
    """The audit ``after`` snapshot for one policy decision."""
    return {
        "decision": verdict.decision.value,
        "rule": verdict.rule,
        "reason": verdict.reason,
        "method": context.method,
        "path": context.path,
        "sensitivity": context.sensitivity.value,
        "principal": context.principal,
        "ip": context.ip,
        "geo_country": context.geo.country if context.geo else None,
        "device_fingerprint": context.device_fingerprint,
        "local_hour": context.local_hour,
        "session_id": str(context.session_id) if context.session_id else None,
    }


def record_policy_decision(
    db: Session,
    context: AccessContext,
    verdict: PolicyVerdict,
    *,
    durable: bool,
) -> None:
    """Audit one policy decision on the append-only trail (#31).

    ALLOW decisions ride the request transaction (committed by
    ``CommitOnSuccessRoute`` like every other request write). Non-allow
    decisions pass ``durable=True``: they are committed immediately because
    the rejection raised right after them rolls the request back —
    the same explicit-commit pattern the OTP attempt counter uses.
    """
    with db_guard.audit_bypass(db):
        record_audit_event(
            db,
            actor_id=context.user_id,
            entity_type="access_decision",
            entity_id=context.user_id or _ANONYMOUS_ENTITY_ID,
            action=f"policy_{verdict.decision.value}",
            after=_decision_payload(context, verdict),
        )
        if durable:
            db.commit()


def _reject(context: AccessContext, verdict: PolicyVerdict) -> None:
    """Translate a non-allow verdict into the API's error contract."""
    if verdict.decision is Decision.DENY:
        raise ForbiddenException(
            detail="Access refused by access policy.",
            required_permission=f"abac:{verdict.rule}",
        )
    if verdict.decision is Decision.CHALLENGE:
        raise StepUpRequiredException(
            detail=(
                "Additional verification is required for this action. "
                "Please sign in again to receive a verification code."
            )
        )
    # TERMINATE: the session is dead; the client must authenticate afresh.
    raise UnauthorizedException("Session terminated by access policy.")


def evaluate_session_risk(
    request: Request, db: Session, context: AccessContext
) -> PolicyVerdict | None:
    """Continuous session risk hook (issue #67 capability 3).

    Placeholder in the policy-engine tranche: session risk scoring plugs in
    here (per-session behavioural score, impossible travel, volume and
    privilege-escalation anomalies) and may return a CHALLENGE / TERMINATE
    verdict that overrides a static ALLOW.
    """
    return None


def zero_trust_gate(request: Request, db: Session = Depends(get_db)) -> None:
    """Application-wide dependency enforcing context-aware access control."""
    if not settings.ABAC_ENABLED:
        db_guard.set_verdict(db, _DISABLED_VERDICT)
        return

    # A shared/inherited session may still hold the PREVIOUS request's
    # transaction — e.g. a reports/sales-desk ``REPEATABLE READ READ ONLY``
    # snapshot when the test fixtures reuse one session across requests.
    # End it before this request's first write, exactly as
    # ``_start_report_snapshot`` does for the auth read; on a fresh
    # production session (one per request via get_db) this is a no-op.
    if db.in_transaction():
        try:
            db.commit()
        except PendingRollbackError:
            # A prior request left the shared session broken mid-flush;
            # heal it so this request starts from a clean transaction.
            db.rollback()

    context = build_access_context(request)
    request.state.access_context = context

    if context.sensitivity is SensitivityLevel.PUBLIC:
        db_guard.set_verdict(db, _PUBLIC_VERDICT)
        request.state.access_verdict = _PUBLIC_VERDICT
        return

    verdict = evaluate(context)
    if verdict.allowed:
        risk_verdict = evaluate_session_risk(request, db, context)
        if risk_verdict is not None:
            verdict = risk_verdict
    request.state.access_verdict = verdict

    if verdict.allowed:
        db_guard.set_verdict(db, verdict)
        if settings.ABAC_AUDIT_ALLOW_DECISIONS:
            record_policy_decision(db, context, verdict, durable=False)
        return

    # Non-allow: persist the evidence durably, publish the verdict so the
    # DB guard keeps the session fenced, then reject.
    logger.warning(
        "Zero-trust gate rejected request",
        extra={
            "decision": verdict.decision.value,
            "rule": verdict.rule,
            "path": context.path,
            "principal": context.principal,
        },
    )
    record_policy_decision(db, context, verdict, durable=True)
    db_guard.set_verdict(db, verdict)
    _reject(context, verdict)


__all__ = [
    "evaluate_session_risk",
    "record_policy_decision",
    "zero_trust_gate",
]
