"""ABAC / session-risk operations surface (issue #85).

!68 shipped continuous session risk scoring whose remediation and tuning
surfaces were write-only: no way to list or terminate a user's sessions
short of SQL, no way to inspect or expire absorbed baseline trust, and a
``soft_clamp`` review queue that lived in a psql snippet (ADR-0012).
This router closes those three gaps by layering on the EXISTING
primitives — ``risk._terminate`` (via ``operator_terminate_session``),
the append-only ``audit_events`` trail (#31) and the
``baselines`` entry helpers. No new state stores.

Security posture:

- Every path under ``/admin`` classifies **RESTRICTED**
  (``sensitivity.py``), so the full ABAC treatment applies on top of
  RBAC: off-hours step-up, unknown-geo rules, IP/geo DENY, decision
  auditing.
- RBAC gate: ``require_role(PLATFORM_OPERATOR, ADMIN)`` — an explicit
  ad-hoc set, NOT a hierarchy (ADR-0011: platform and tenant authority
  stay disjoint axes). The platform operator is the incident responder
  this surface exists for; the tenant ADMIN is this deployment's
  hands-on operator today. Session/baseline telemetry is security
  infrastructure state, not tenant business data — exposing it to the
  operator is a deliberate, documented decision (issue #85).
- **Step-up gating arrives via issue #73** (operator MFA / step-up for
  the platform console, in flight on the tenancy branch): when both
  branches land, these endpoints must be wired behind that gate too.
  Until then the ADR-0011-style RBAC + RESTRICTED classification is the
  gate, exactly like the existing ``/platform`` surface.
- Every mutating action writes an audit row with the OPERATOR as actor;
  terminations reuse ``_terminate``'s denylist + audit path so the
  victim's live tokens die immediately.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm.attributes import flag_modified

from app.common.audit import AuditEvent, record_audit_event
from app.common.authz import baselines
from app.common.authz.risk import effective_score, operator_terminate_session
from app.common.dependencies import DbSession, require_role
from app.common.exceptions import ConflictException, NotFoundException
from app.common.routing import CommitOnSuccessRoute
from app.constants.enums import SessionStatus, UserRole
from app.modules.admin_ops.schemas import (
    AdminBaselineEntry,
    AdminBaselineEntryExpiryResponse,
    AdminRiskEventsResponse,
    AdminRiskEventView,
    AdminSessionTerminateRequest,
    AdminSessionTerminateResponse,
    AdminSessionView,
    AdminUserBaselineResponse,
    AdminUserSessionsResponse,
    AdminVolumeBaseline,
)
from app.modules.auth.models import User, UserBaseline, UserSession

router = APIRouter(
    route_class=CommitOnSuccessRoute,
    dependencies=[Depends(require_role(UserRole.PLATFORM_OPERATOR, UserRole.ADMIN))],
)


class BaselineEntryKind(StrEnum):
    """Which absorbed-trust collection an ops action targets."""

    DEVICES = "devices"
    COUNTRIES = "countries"


def _require_user(db, user_id: uuid.UUID) -> User:
    """404 for an unknown user id.

    Not an enumeration oracle: this surface is RESTRICTED and RBAC-gated
    to operators, who administer the very accounts being named — the
    same posture as the /platform owner listing (ADR-0011).
    """
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundException(detail="User not found", resource="user")
    return user


@router.get(
    "/users/{user_id}/sessions",
    response_model=AdminUserSessionsResponse,
    summary="List a user's sessions with live risk state (operators only)",
    description=(
        "Every session of the user — active, challenged and terminated — "
        "with raw and decay-adjusted risk scores, the non-decaying floor, "
        "status, termination reason and last observed context (device, "
        "IP, country, geo/seen timestamps). The remediation view issue "
        "#85 names: an operator triages a suspect account without SQL."
    ),
    responses={
        403: {"description": "Caller is not a platform operator / admin"},
        404: {"description": "Unknown user id"},
    },
)
def list_user_sessions(
    user_id: uuid.UUID, request: Request, db: DbSession
) -> AdminUserSessionsResponse:
    """Return all sessions for one user, newest first."""
    _require_user(db, user_id)
    # The gate's evaluation instant: effective_score decays from the same
    # clock every policy decision uses, so the listed score is what the
    # NEXT gate check would act on.
    now = request.state.access_context.requested_at
    sessions = (
        db.query(UserSession)
        .filter(UserSession.user_id == user_id)
        .order_by(UserSession.created_at.desc())
        .all()
    )
    views = [
        AdminSessionView(
            id=s.id,
            status=s.status,
            risk_score=s.risk_score or 0,
            effective_score=effective_score(s, now),
            risk_floor=s.risk_floor or 0,
            escalation_count=s.escalation_count or 0,
            termination_reason=s.termination_reason,
            device_fingerprint=s.device_fingerprint,
            last_ip=s.last_ip,
            last_country=s.last_country,
            last_geo_at=s.last_geo_at,
            last_seen_at=s.last_seen_at,
            created_at=s.created_at,
        )
        for s in sessions
    ]
    return AdminUserSessionsResponse(user_id=user_id, sessions=views)


@router.post(
    "/sessions/{session_id}/terminate",
    response_model=AdminSessionTerminateResponse,
    summary="Terminate one session, audited and immediate (operators only)",
    description=(
        "Reuses the risk engine's _terminate path: the sid lands on the "
        "shared token denylist (live access tokens die on the very next "
        "request), the status flips to terminated forever, and a "
        "session_terminated audit event records the OPERATOR as actor "
        "plus the full reason. 409 when already terminated."
    ),
    responses={
        403: {"description": "Caller is not a platform operator / admin"},
        404: {"description": "Unknown session id"},
        409: {"description": "Session is already terminated"},
    },
)
def terminate_session(
    session_id: uuid.UUID,
    body: AdminSessionTerminateRequest,
    request: Request,
    db: DbSession,
) -> AdminSessionTerminateResponse:
    """Kill one session now; its tokens die with it."""
    # Locked read: a concurrent risk transition on the same row must not
    # interleave with the operator kill (same discipline as the risk
    # engine's own transitions; SQLite serializes writers anyway).
    session = db.get(UserSession, session_id, with_for_update=True)
    if session is None:
        raise NotFoundException(detail="Session not found", resource="session")
    if session.status == SessionStatus.TERMINATED.value:
        raise ConflictException(detail="Session is already terminated.")

    reason = "terminated by operator"
    if body.reason and body.reason.strip():
        reason = f"{reason}: {body.reason.strip()}"
    operator_terminate_session(db, session, request.state.access_context, reason)
    return AdminSessionTerminateResponse(
        id=session.id,
        status=session.status,
        termination_reason=session.termination_reason,
    )


@router.get(
    "/users/{user_id}/baseline",
    response_model=AdminUserBaselineResponse,
    summary="Inspect a user's behavioural baseline (operators only)",
    description=(
        "Absorbed devices and countries with their verified_at freshness "
        "(fresh = still suppresses the soft signals, exactly as the "
        "detectors will evaluate it), the tenant-local hour histogram, "
        "and the learned per-class volume EWMAs. learned=false with "
        "empty collections when the user has no baseline row yet."
    ),
    responses={
        403: {"description": "Caller is not a platform operator / admin"},
        404: {"description": "Unknown user id"},
    },
)
def inspect_user_baseline(
    user_id: uuid.UUID, request: Request, db: DbSession
) -> AdminUserBaselineResponse:
    """Return the learned baseline, or an explicit empty view."""
    _require_user(db, user_id)
    baseline = db.get(UserBaseline, user_id)
    if baseline is None:
        return AdminUserBaselineResponse(
            user_id=user_id,
            learned=False,
            devices=[],
            countries=[],
            hour_counts={},
            hour_observations=0,
            volume_baselines={},
            updated_at=None,
        )
    now = request.state.access_context.requested_at
    return AdminUserBaselineResponse(
        user_id=user_id,
        learned=True,
        devices=[
            AdminBaselineEntry(**entry)
            for entry in baselines.describe_entries(baseline.known_devices, now)
        ],
        countries=[
            AdminBaselineEntry(**entry)
            for entry in baselines.describe_entries(baseline.known_countries, now)
        ],
        hour_counts=dict(baseline.hour_counts or {}),
        hour_observations=baseline.hour_observations or 0,
        volume_baselines={
            cls: AdminVolumeBaseline(
                ewma=float(entry.get("ewma", 0.0)),
                windows=int(entry.get("windows", 0)),
                count=int(entry.get("count", 0)),
                window_started_at=entry.get("window_started_at") or None,
            )
            for cls, entry in (baseline.volume_baselines or {}).items()
        },
        updated_at=baseline.updated_at,
    )


@router.delete(
    "/users/{user_id}/baseline/{kind}",
    response_model=AdminBaselineEntryExpiryResponse,
    summary="Expire one absorbed device/country, audited (operators only)",
    description=(
        "Sets the entry aged-out (verified_at pushed past the trust TTL) "
        "rather than editing history: the next use of that context "
        "re-fires the soft signal — one challenge, then re-absorption on "
        "a passed step-up, with the owner notified as for any new trust. "
        "The value goes in the query string (?value=...) because device "
        "fingerprints are client-supplied free text that does not belong "
        "in a path segment. Audited with the operator as actor."
    ),
    responses={
        403: {"description": "Caller is not a platform operator / admin"},
        404: {"description": "Unknown user id, no baseline, or no such entry"},
    },
)
def expire_baseline_entry(
    user_id: uuid.UUID,
    kind: BaselineEntryKind,
    request: Request,
    db: DbSession,
    value: str = Query(..., min_length=1, max_length=160),
) -> AdminBaselineEntryExpiryResponse:
    """Age one absorbed entry out of the trusted baseline, audited."""
    _require_user(db, user_id)
    baseline = db.get(UserBaseline, user_id)
    if baseline is None:
        raise NotFoundException(
            detail="User has no behavioural baseline", resource="baseline"
        )
    context = request.state.access_context
    now = context.requested_at
    entries = (
        baseline.known_devices
        if kind is BaselineEntryKind.DEVICES
        else baseline.known_countries
    )
    before = baselines.expire_entry(entries, value, now)
    if before is None:
        raise NotFoundException(
            detail="No such absorbed entry", resource="baseline_entry"
        )
    flag_modified(
        baseline,
        "known_devices" if kind is BaselineEntryKind.DEVICES else "known_countries",
    )
    baseline.updated_at = now
    record_audit_event(
        db,
        actor_id=context.user_id,
        entity_type="baseline",
        entity_id=user_id,
        action="baseline_entry_expired",
        before={"kind": kind.value, **before},
        after={
            "kind": kind.value,
            "value": value,
            "expired_by_operator": True,
            "ip": context.ip,
            "path": context.path,
        },
    )
    return AdminBaselineEntryExpiryResponse(
        user_id=user_id, kind=kind.value, value=value, expired=True
    )


@router.get(
    "/risk-events",
    response_model=AdminRiskEventsResponse,
    summary="List session risk events from the audit trail (operators only)",
    description=(
        "First-class listing over the append-only audit_events rows the "
        "risk engine writes (entity_type='session'): the weekly "
        "soft_clamp review (ADR-0012 'Operating the risk model') runs as "
        "GET /admin/risk-events?action=session_challenged&soft_clamp=true"
        "&since=... instead of a psql snippet. Paginated, newest first, "
        "with an exact total so a truncated page is never mistaken for "
        "the full picture."
    ),
    responses={403: {"description": "Caller is not a platform operator / admin"}},
)
def list_risk_events(
    db: DbSession,
    action: str | None = Query(
        None, max_length=40, description="Exact audit action, e.g. session_challenged"
    ),
    soft_clamp: bool | None = Query(
        None,
        description=(
            "Filter on the soft_clamp marker inside the event payload; "
            "true = the weekly false-positive review queue"
        ),
    ),
    since: datetime | None = Query(
        None, description="ISO-8601 lower bound on created_at (inclusive)"
    ),
    user_id: uuid.UUID | None = Query(None, description="Filter by acting user"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> AdminRiskEventsResponse:
    """Paginated view over the session risk events."""
    query = db.query(AuditEvent).filter(AuditEvent.entity_type == "session")
    if action:
        query = query.filter(AuditEvent.action == action)
    if user_id is not None:
        query = query.filter(AuditEvent.actor_id == user_id)
    if since is not None:
        query = query.filter(AuditEvent.created_at >= since)
    if soft_clamp is not None:
        # JSON payload filter: rows without the marker read as NULL and
        # match neither true nor false — soft_clamp=false therefore means
        # "explicitly recorded as not clamped", which is the useful
        # complement for tuning comparisons.
        query = query.filter(AuditEvent.after["soft_clamp"].as_boolean() == soft_clamp)

    total = query.count()
    rows = (
        query.order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit).all()
    )
    events = [
        AdminRiskEventView(
            id=row.id,
            created_at=row.created_at,
            action=row.action,
            session_id=row.entity_id,
            user_id=row.actor_id,
            detail=row.after,
        )
        for row in rows
    ]
    return AdminRiskEventsResponse(
        events=events, total=total, limit=limit, offset=offset
    )
