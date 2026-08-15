"""Continuous session risk scoring (#67, capability 3).

A per-session behavioural score, re-evaluated on EVERY request by the
zero-trust gate — trust is "right now", not "since login". Signals:

- **Impossible travel** — consecutive requests whose geolocations imply a
  speed above ``RISK_IMPOSSIBLE_TRAVEL_KMH``.
- **Device change** — the presented device fingerprint differs from the
  session's last one.
- **Unusual data-access volume** — more than ``RISK_VOLUME_MAX_REQUESTS``
  requests inside a rolling ``RISK_VOLUME_WINDOW_SECONDS`` window.
- **Privilege-escalation attempts** — RBAC rejections (``require_role`` /
  ``require_privileged`` 403s) recorded via
  :func:`note_privilege_escalation`.

Crossing ``RISK_CHALLENGE_THRESHOLD`` flips the session to
``challenge_required`` (the client re-runs the existing login → OTP flow;
the fresh session it mints is the cleared one). Crossing
``RISK_TERMINATE_THRESHOLD`` terminates the session outright. Neither
state is ever cleared in place, and the risk score never decays within a
session: a session that misbehaved stays suspect for its whole life.

Every anomaly and every state transition is audited on the append-only
``audit_events`` trail (entity_type ``session``), consistent with #31.

Tokens minted before this feature carry no ``sid`` claim; they simply have
no session to score (the static ABAC rules still apply) and age out with
the access-token lifetime.
"""

import logging
import math
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy.orm import Session

from app.common.audit import record_audit_event
from app.common.authz.context import AccessContext
from app.common.authz.engine import Decision, PolicyVerdict
from app.constants.enums import SessionStatus
from app.lib.config import settings

logger = logging.getLogger(__name__)

_EARTH_RADIUS_KM = 6371.0

#: Floor for the elapsed time in the speed computation, so two immediate
#: requests from distant locations read as a huge speed, not a div-by-zero.
_MIN_ELAPSED_HOURS = 1.0 / 3600.0

#: Movements below this distance are never "impossible travel": within-city
#: movement and GPS jitter over a few seconds would otherwise compute as an
#: absurd speed. An attacker relaying from another region clears this floor.
_MIN_TRAVEL_DISTANCE_KM = 100.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two coordinates, in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _aware(value: datetime | None) -> datetime | None:
    """Normalize DB datetimes to UTC-aware (SQLite returns naive)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _audit_session_event(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
    action: str,
    detail: dict,
) -> None:
    record_audit_event(
        db,
        actor_id=context.user_id,
        entity_type="session",
        entity_id=session.id,
        action=action,
        after={
            "risk_score": session.risk_score,
            "path": context.path,
            "method": context.method,
            "ip": context.ip,
            **detail,
        },
    )


def _detect_impossible_travel(
    db: Session, session: "UserSession", context: AccessContext  # noqa: F821
) -> None:
    """Speed between the last and current geolocation above the plausible cap."""
    geo = context.geo
    last_seen = _aware(session.last_seen_at)
    if (
        geo is None
        or not geo.has_coordinates
        or session.last_lat is None
        or session.last_lon is None
        or last_seen is None
    ):
        return

    distance_km = haversine_km(session.last_lat, session.last_lon, geo.lat, geo.lon)
    if distance_km < _MIN_TRAVEL_DISTANCE_KM:
        return
    elapsed_hours = max(
        (context.requested_at - last_seen).total_seconds() / 3600.0,
        _MIN_ELAPSED_HOURS,
    )
    speed_kmh = distance_km / elapsed_hours
    if speed_kmh <= settings.RISK_IMPOSSIBLE_TRAVEL_KMH:
        return

    session.risk_score += settings.RISK_SCORE_IMPOSSIBLE_TRAVEL
    _audit_session_event(
        db,
        session,
        context,
        "impossible_travel",
        {
            "distance_km": round(distance_km, 1),
            "elapsed_hours": round(elapsed_hours, 4),
            "speed_kmh": round(speed_kmh, 1),
            "from": [session.last_lat, session.last_lon],
            "to": [geo.lat, geo.lon],
        },
    )


def _detect_device_change(
    db: Session, session: "UserSession", context: AccessContext  # noqa: F821
) -> None:
    """The presenting device/browser changed mid-session."""
    current = context.device_fingerprint
    previous = session.device_fingerprint
    if not current or not previous or current == previous:
        return

    session.risk_score += settings.RISK_SCORE_DEVICE_CHANGE
    _audit_session_event(
        db,
        session,
        context,
        "device_change",
        {"from": previous, "to": current},
    )


def _detect_volume_anomaly(
    db: Session, session: "UserSession", context: AccessContext  # noqa: F821
) -> None:
    """Request volume inside the rolling window exceeds the ceiling.

    The bump fires exactly once per window (at the crossing), so a burst
    raises the score by one step rather than once per extra request.
    """
    window_started = _aware(session.window_started_at)
    window_seconds = settings.RISK_VOLUME_WINDOW_SECONDS
    now = context.requested_at

    if (
        window_started is None
        or (now - window_started).total_seconds() > window_seconds
    ):
        session.window_started_at = now
        session.window_request_count = 1
        return

    session.window_request_count += 1
    if session.window_request_count == settings.RISK_VOLUME_MAX_REQUESTS + 1:
        session.risk_score += settings.RISK_SCORE_VOLUME_ANOMALY
        _audit_session_event(
            db,
            session,
            context,
            "volume_anomaly",
            {
                "window_seconds": window_seconds,
                "request_count": session.window_request_count,
                "max_requests": settings.RISK_VOLUME_MAX_REQUESTS,
            },
        )


def _terminate(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
    reason: str,
) -> PolicyVerdict:
    session.status = SessionStatus.TERMINATED.value
    session.termination_reason = reason
    _audit_session_event(db, session, context, "session_terminated", {"why": reason})
    logger.warning(
        "Session terminated by risk policy",
        extra={"session_id": str(session.id), "reason": reason},
    )
    return PolicyVerdict(
        decision=Decision.TERMINATE,
        rule="session_risk",
        reason=reason,
    )


def assess_session_risk(
    db: Session, context: AccessContext
) -> PolicyVerdict | None:
    """Re-score the request's session; return a verdict when not cleared.

    Called by the zero-trust gate AFTER the static rules allowed the
    request (the gate has already published a provisional ALLOW verdict,
    so these reads/writes pass the DB guard). Returns None when the
    session is clean — the static ALLOW stands.
    """
    if context.session_id is None:
        # Anonymous, service, or legacy (pre-sid) token: nothing to score.
        return None

    from app.modules.auth.models import UserSession

    session = db.get(UserSession, context.session_id)
    if session is None:
        # A signed token naming a session we never minted (or one purged):
        # zero trust says refuse, not shrug.
        return PolicyVerdict(
            decision=Decision.TERMINATE,
            rule="session_risk",
            reason="Unknown session",
        )

    if session.user_id != context.user_id:
        return _terminate(db, session, context, "token/session identity mismatch")

    if session.status == SessionStatus.TERMINATED.value:
        return PolicyVerdict(
            decision=Decision.TERMINATE,
            rule="session_risk",
            reason=f"Session terminated ({session.termination_reason or 'earlier'})",
        )

    if session.status == SessionStatus.CHALLENGE_REQUIRED.value:
        return PolicyVerdict(
            decision=Decision.CHALLENGE,
            rule="session_risk",
            reason="Session awaiting step-up verification",
        )

    # Behavioural signals for this request.
    _detect_impossible_travel(db, session, context)
    _detect_device_change(db, session, context)
    _detect_volume_anomaly(db, session, context)

    verdict: PolicyVerdict | None = None
    if session.risk_score >= settings.RISK_TERMINATE_THRESHOLD:
        verdict = _terminate(
            db,
            session,
            context,
            f"risk score {session.risk_score} crossed terminate threshold",
        )
    elif session.risk_score >= settings.RISK_CHALLENGE_THRESHOLD:
        session.status = SessionStatus.CHALLENGE_REQUIRED.value
        _audit_session_event(
            db,
            session,
            context,
            "session_challenged",
            {"why": f"risk score {session.risk_score} crossed challenge threshold"},
        )
        verdict = PolicyVerdict(
            decision=Decision.CHALLENGE,
            rule="session_risk",
            reason=f"Risk score {session.risk_score} requires step-up",
        )

    # Update the trail state AFTER the detectors compared against it.
    session.last_ip = context.ip
    if context.geo is not None:
        if context.geo.country is not None:
            session.last_country = context.geo.country
        if context.geo.has_coordinates:
            session.last_lat = context.geo.lat
            session.last_lon = context.geo.lon
    if context.device_fingerprint:
        session.device_fingerprint = context.device_fingerprint
    session.last_seen_at = context.requested_at
    db.flush()

    return verdict


def note_privilege_escalation(request: Request, db: Session) -> None:
    """Record an RBAC rejection against the request's session, durably.

    Called by ``require_role`` / ``require_privileged`` right before they
    raise their 403. The write COMMITS explicitly (the OTP attempt-counter
    pattern): the 403 that follows rolls the request back, and an
    escalation attempt that vanishes with the rollback would make repeated
    probing free.
    """
    context = getattr(request.state, "access_context", None)
    if context is None or context.session_id is None:
        return

    from app.modules.auth.models import UserSession

    session = db.get(UserSession, context.session_id)
    if session is None or session.status == SessionStatus.TERMINATED.value:
        return

    session.escalation_count += 1
    session.risk_score += settings.RISK_SCORE_PRIVILEGE_ESCALATION
    _audit_session_event(
        db,
        session,
        context,
        "privilege_escalation",
        {"escalation_count": session.escalation_count},
    )
    db.commit()
