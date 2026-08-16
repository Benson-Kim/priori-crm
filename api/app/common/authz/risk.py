"""Continuous session risk scoring (#67, capability 3).

A per-session behavioural score, re-evaluated on EVERY request by the
zero-trust gate — trust is "right now", not "since login". Signals are
graduated by evidence weight (issue #67):

SOFT (low weight; escalate only in combination; structurally they can
challenge but NEVER terminate — a score at the terminate threshold with no
HARD signal in the evaluation clamps to a challenge, so soft evidence
alone can never hard-lock a legitimate user, whatever it accumulates to):

- **New device / new country / unusual hour** — the session's first
  scored request deviates from the user's own behavioural baseline
  (``baselines.py``); each fires only when there is history to deviate
  from. A passed OTP step-up absorbs the context into the baseline, so a
  genuine user is challenged for the same new device/place at most once.
- **Device change** — the presented device fingerprint differs from the
  session's last one.
- **Mild volume deviation** — more requests inside a rolling
  ``RISK_VOLUME_WINDOW_SECONDS`` window than this user typically makes
  for this sensitivity class (adaptive ceiling, global default).

HARD (escalate directly):

- **Impossible travel** — consecutive requests whose geolocations imply a
  speed above ``RISK_IMPOSSIBLE_TRAVEL_KMH`` (weight 70: an immediate
  step-up; termination only with corroboration, because carrier-NAT
  geolocation jitter is a real false-positive source).
- **Exfiltration-scale reads** — ``RISK_VOLUME_EXFIL_MULTIPLIER`` times
  the global volume ceiling in one window (weight 100: terminates).
- **Privilege-escalation attempts** — RBAC rejections (``require_role`` /
  ``require_privileged`` 403s) recorded via
  :func:`note_privilege_escalation`.
- **Repeated failed step-ups** — exhausting the OTP attempt budget
  terminates the user's challenged sessions
  (``AuthService.verify_otp``).
- **Token/session identity anomalies** — unknown or mismatched ``sid``
  terminates outright.

Crossing ``RISK_CHALLENGE_THRESHOLD`` flips the session to
``challenge_required`` (the client re-runs the existing login → OTP flow;
the fresh session it mints is the cleared one). Crossing
``RISK_TERMINATE_THRESHOLD`` terminates the session outright. Neither
state is ever cleared in place.

**Scores decay** (``RISK_DECAY_PER_HOUR``). An undecayed score is a ratchet:
benign noise — a browser auto-update (+25), one busy minute (+30), a stray
403 (+25) — accumulates past the challenge threshold on any long-lived
session, so a legitimate user is eventually challenged for nothing. Decay
applies ONLY to the score; a session already flipped to
``challenge_required`` or ``terminated`` is never restored in place, so the
"trust is re-established only by re-authentication" invariant holds.

**Durability is split by what an attacker can exploit.** Score increments
and status transitions COMMIT explicitly: the 4xx they cause would
otherwise roll back the very evidence for it. The volume counter lives in
the shared ``RateLimitStore``, not on the session row, because a Postgres
counter is rolled back by any failing request — an attacker probing
endpoints that error would reset their own window for free. Trail state
(last seen, geo, fingerprint) rides the request transaction: it is written
every request and losing it on a failure is not exploitable.

Sessions also expire: ``SESSION_MAX_AGE_HOURS`` (absolute) and
``SESSION_IDLE_TIMEOUT_MINUTES`` (since last seen), each terminating with
its own audited reason so an expiry is never mistaken for a risk kill.

Every anomaly and every state transition is audited on the append-only
``audit_events`` trail (entity_type ``session``), consistent with #31.

Tokens minted before this feature carry no ``sid`` claim; they simply have
no session to score (the static ABAC rules still apply) and age out with
the access-token lifetime.
"""

import logging
import math
from datetime import UTC, datetime
from functools import lru_cache

from fastapi import Request
from sqlalchemy.orm import Session

from app.common.audit import record_audit_event
from app.common.authz import baselines
from app.common.authz.context import AccessContext
from app.common.authz.engine import Decision, PolicyVerdict
from app.common.rate_limit_store import RateLimitStore, build_rate_limit_store
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


@lru_cache(maxsize=1)
def _volume_store() -> RateLimitStore:
    """Shared counter for the data-access volume window.

    Reuses the rate-limiter's store (Redis in production, in-memory
    fallback) rather than a column on ``user_sessions``: it survives the
    rollback of a failing request, so probing endpoints that error cannot
    reset the window, and it costs no Postgres write per request. Built the
    same way as ``_auth_throttle_store``, so a multi-worker deployment
    shares one window.
    """
    return build_rate_limit_store(
        backend=settings.RATE_LIMIT_BACKEND,
        redis_url=settings.REDIS_URL,
    )


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


def effective_score(
    session: "UserSession",  # noqa: F821
    now: datetime,
) -> int:
    """The session's risk score with elapsed decay applied.

    Read-only: decay is computed, never written, so a quiet session costs
    no UPDATE per request. The stored score is settled to this value only
    when a detector actually fires (see :func:`_add_risk`).
    """
    raw = session.risk_score or 0
    updated_at = _aware(session.risk_updated_at)
    if raw <= 0 or updated_at is None or settings.RISK_DECAY_PER_HOUR <= 0:
        return max(raw, 0)
    elapsed_hours = (now - updated_at).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return raw
    shed = int(settings.RISK_DECAY_PER_HOUR * elapsed_hours)
    return max(raw - shed, 0)


def _add_risk(
    session: "UserSession",  # noqa: F821
    points: int,
    now: datetime,
) -> None:
    """Settle the decayed score, then add ``points`` and stamp the clock.

    Settling at bump time (rather than on every request) is what keeps
    decay free: between anomalies nothing is written.
    """
    session.risk_score = effective_score(session, now) + points
    session.risk_updated_at = now


def _audit_session_event(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
    action: str,
    detail: dict,
) -> None:
    """Record one session event, with the score AS THE DECISION SAW IT.

    Both the raw stored score and the decayed effective score go in, plus
    the decay rate: with decay in play a row reporting only the raw value
    could read 80 for a decision actually taken on 45, which would make the
    trail unreconstructable — the opposite of what an audit trail is for.
    """
    now = context.requested_at
    record_audit_event(
        db,
        actor_id=context.user_id,
        entity_type="session",
        entity_id=session.id,
        action=action,
        after={
            "risk_score": session.risk_score,
            "effective_score": effective_score(session, now),
            "decay_per_hour": settings.RISK_DECAY_PER_HOUR,
            "path": context.path,
            "method": context.method,
            "ip": context.ip,
            **detail,
        },
    )


def _detect_impossible_travel(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
) -> bool:
    """Speed between the last and current geolocation above the plausible cap.

    Elapsed time is anchored on ``last_geo_at`` — when the stored
    coordinates were CAPTURED — not on ``last_seen_at``, which every
    request (geolocated or not) updates. With intermittent geo coverage
    the latter underestimates the elapsed time and so overestimates the
    speed: a user who genuinely travelled while sending non-geolocated
    requests would read as teleporting. No anchor (pre-anchor rows, or a
    first-ever fix) means no signal — fail-safe.
    """
    geo = context.geo
    last_geo_at = _aware(session.last_geo_at)
    if (
        geo is None
        or not geo.has_coordinates
        or session.last_lat is None
        or session.last_lon is None
        or last_geo_at is None
    ):
        return False

    distance_km = haversine_km(session.last_lat, session.last_lon, geo.lat, geo.lon)
    if distance_km < _MIN_TRAVEL_DISTANCE_KM:
        return False
    elapsed_hours = max(
        (context.requested_at - last_geo_at).total_seconds() / 3600.0,
        _MIN_ELAPSED_HOURS,
    )
    speed_kmh = distance_km / elapsed_hours
    if speed_kmh <= settings.RISK_IMPOSSIBLE_TRAVEL_KMH:
        return False

    _add_risk(session, settings.RISK_SCORE_IMPOSSIBLE_TRAVEL, context.requested_at)
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
    return True


def _detect_device_change(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
) -> bool:
    """The presenting device/browser changed mid-session."""
    current = context.device_fingerprint
    previous = session.device_fingerprint
    if not current or not previous or current == previous:
        return False

    _add_risk(session, settings.RISK_SCORE_DEVICE_CHANGE, context.requested_at)
    _audit_session_event(
        db,
        session,
        context,
        "device_change",
        {"from": previous, "to": current},
    )
    return True


def _evaluate_session_start_softs(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
    baseline,
) -> bool:
    """SOFT baseline-deviation signals, once per session (issue #67).

    Evaluated on the session's FIRST scored request only
    (``last_seen_at is None`` — later requests are covered by the
    mid-session detectors), against the user's own behavioural baseline:
    unknown device, unknown country, unusual hour. Each fires only when
    there is history to deviate from, carries a low weight, and escalates
    only in combination — by design the full set stays below the terminate
    threshold, so soft evidence alone can challenge but never hard-lock a
    legitimate user in a new place, on a new device, at night.

    Sessions minted by ``verify_otp`` had their context absorbed into the
    baseline at mint time (the step-up WAS passed), so these fire mainly
    for hijacked tokens presented from somewhere the user has never been.
    """
    if session.last_seen_at is not None:
        return False

    signals = baselines.evaluate_session_start_signals(baseline, context)
    for signal in signals:
        _add_risk(session, signal.points, context.requested_at)
        _audit_session_event(
            db,
            session,
            context,
            f"soft_signal_{signal.name}",
            {"points": signal.points, **signal.detail},
        )
    return bool(signals)


def _detect_volume_anomaly(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
    baseline,
) -> tuple[bool, bool]:
    """Request volume inside the rolling window exceeds a ceiling.

    Returns ``(soft_fired, hard_fired)`` so the caller can tell the mild
    deviation apart from the exfiltration signal: only the latter is HARD
    evidence that may carry a termination.

    Two ceilings, matching the signal taxonomy (#67):

    - **Mild deviation (SOFT)** — per sensitivity class, adaptive to the
      user's learned typical volume for that class
      (``baselines.volume_ceiling``); low weight, corroborates but never
      challenges alone.
    - **Exfiltration scale (HARD)** — an absolute, class-independent
      ceiling at ``RISK_VOLUME_EXFIL_MULTIPLIER`` times the global limit;
      no legitimate workflow reads at that rate, so it terminates
      directly.

    Counted in the shared store, keyed on the session (and class for the
    mild signal). Single-slot ``fired`` keys latch each bump so a
    sustained burst raises the score once per window at the crossing,
    rather than once per excess request.
    """
    store = _volume_store()
    window = settings.RISK_VOLUME_WINDOW_SECONDS
    sid = str(session.id)
    cls = context.sensitivity.value
    soft_fired = False
    hard_fired = False

    mild_ceiling = baselines.volume_ceiling(baseline, cls)
    over_mild = not store.hit(f"risk:vol:{sid}:{cls}", mild_ceiling, window).allowed
    if over_mild and store.hit(f"risk:vol:fired:{sid}:{cls}", 1, window).allowed:
        _add_risk(session, settings.RISK_SCORE_VOLUME_ANOMALY, context.requested_at)
        _audit_session_event(
            db,
            session,
            context,
            "volume_anomaly",
            {
                "window_seconds": window,
                "max_requests": mild_ceiling,
                "sensitivity_class": cls,
            },
        )
        soft_fired = True

    exfil_ceiling = (
        settings.RISK_VOLUME_MAX_REQUESTS * settings.RISK_VOLUME_EXFIL_MULTIPLIER
    )
    over_exfil = not store.hit(f"risk:volx:{sid}", exfil_ceiling, window).allowed
    if over_exfil and store.hit(f"risk:volx:fired:{sid}", 1, window).allowed:
        _add_risk(session, settings.RISK_SCORE_EXFILTRATION, context.requested_at)
        _audit_session_event(
            db,
            session,
            context,
            "exfiltration_volume",
            {
                "window_seconds": window,
                "max_requests": exfil_ceiling,
            },
        )
        hard_fired = True

    return soft_fired, hard_fired


def _terminate(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
    reason: str,
) -> PolicyVerdict:
    from app.common.token_denylist import revoke_session_access

    session.status = SessionStatus.TERMINATED.value
    session.termination_reason = reason
    # Push the sid onto the shared denylist so the session's live access
    # tokens die on the token-validation path itself, everywhere — not
    # only where the gate re-checks session status (#67 review F5).
    revoke_session_access(session.id)
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


def _check_lifetime(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
) -> PolicyVerdict | None:
    """Absolute-age and idle expiry, each with its own audited reason.

    Distinct from a risk kill: a session ending because it is simply old
    should never read, in the trail, as a session ending because it
    misbehaved.
    """
    now = context.requested_at

    created_at = _aware(session.created_at)
    if created_at is not None:
        age_hours = (now - created_at).total_seconds() / 3600.0
        if age_hours > settings.SESSION_MAX_AGE_HOURS:
            return _terminate(db, session, context, "max session age exceeded")

    last_seen = _aware(session.last_seen_at)
    if last_seen is not None:
        idle_minutes = (now - last_seen).total_seconds() / 60.0
        if idle_minutes > settings.SESSION_IDLE_TIMEOUT_MINUTES:
            return _terminate(db, session, context, "session idle timeout")

    return None


def assess_session_risk(db: Session, context: AccessContext) -> PolicyVerdict | None:
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

    # Locked read (SELECT ... FOR UPDATE), mirroring note_privilege_
    # escalation (M3): without it, concurrent requests of one session each
    # read the same stale risk_score, run the detectors on it, and commit
    # independently computed replacements — last-writer-wins, so N parallel
    # anomalies accumulate as one and stay below the thresholds (review
    # F1). The gate already serialized same-session requests at the trail-
    # state UPDATE's row lock; taking the lock at the READ merely moves it
    # ahead of the detectors so every increment lands on committed state.
    # A firing detector commits immediately (releasing the lock); a clean
    # request holds it exactly as long as the trail-state update always
    # did. (SQLite has no FOR UPDATE; its whole-database write lock
    # serializes writers anyway. CI runs Postgres.)
    session = db.get(UserSession, context.session_id, with_for_update=True)
    if session is None:
        # A signed token naming a session we never minted (or one purged):
        # zero trust says refuse, not shrug.
        return PolicyVerdict(
            decision=Decision.TERMINATE,
            rule="session_risk",
            reason="Unknown session",
        )

    if session.user_id != context.user_id:
        return _commit_and_return(
            db, _terminate(db, session, context, "token/session identity mismatch")
        )

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

    expiry = _check_lifetime(db, session, context)
    if expiry is not None:
        return _commit_and_return(db, expiry)

    baseline = baselines.get_or_create_baseline(db, session.user_id)

    # Behavioural signals for this request. Each returns whether it fired,
    # so a clean request stays a pure read plus the trail-state update.
    # SOFT and HARD evidence are tracked apart: only a batch carrying HARD
    # evidence (impossible travel, exfiltration-scale reads) may terminate.
    soft_fired = _evaluate_session_start_softs(db, session, context, baseline)
    hard_fired = _detect_impossible_travel(db, session, context)
    soft_fired |= _detect_device_change(db, session, context)
    volume_soft, volume_hard = _detect_volume_anomaly(db, session, context, baseline)
    soft_fired |= volume_soft
    hard_fired |= volume_hard
    fired = soft_fired or hard_fired

    # Continuous learning (typical hours, typical per-class volume) rides
    # the request transaction: losing an increment to a rollback only
    # under-counts, which is safe for statistics that SUPPRESS anomalies.
    baselines.learn_request(baseline, context)

    score = effective_score(session, context.requested_at)
    verdict: PolicyVerdict | None = None
    if score >= settings.RISK_TERMINATE_THRESHOLD and hard_fired:
        verdict = _terminate(
            db, session, context, f"risk score {score} crossed terminate threshold"
        )
    elif score >= settings.RISK_CHALLENGE_THRESHOLD:
        # A score at/above the terminate threshold WITHOUT hard evidence in
        # this batch clamps to a challenge. The single-batch weight sums
        # guarantee softs-from-zero stay below terminate, but a score that
        # legitimately sat just under the challenge line (softs + decay)
        # plus one more soft batch can cross it — a new laptop, a new
        # country, hours of work, then a browser auto-update in a busy
        # minute. That shape must step up, never hard-lock (issue #67's
        # false-positive tolerance). An attacker gains nothing: the
        # challenge is already a wall they cannot answer, and failing the
        # OTP budget terminates the challenged session anyway.
        session.status = SessionStatus.CHALLENGE_REQUIRED.value
        clamped = score >= settings.RISK_TERMINATE_THRESHOLD
        why = f"risk score {score} crossed challenge threshold"
        if clamped:
            why = (
                f"risk score {score} crossed terminate threshold on soft "
                "evidence alone; clamped to challenge (termination requires "
                "a hard signal)"
            )
        _audit_session_event(
            db,
            session,
            context,
            "session_challenged",
            {"why": why, "soft_clamp": clamped},
        )
        verdict = PolicyVerdict(
            decision=Decision.CHALLENGE,
            rule="session_risk",
            reason=f"Risk score {score} requires step-up",
        )

    # Update the trail state AFTER the detectors compared against it.
    session.last_ip = context.ip
    if context.geo is not None:
        if context.geo.country is not None:
            session.last_country = context.geo.country
        if context.geo.has_coordinates:
            session.last_lat = context.geo.lat
            session.last_lon = context.geo.lon
            session.last_geo_at = context.requested_at
    if context.device_fingerprint:
        session.device_fingerprint = context.device_fingerprint
    session.last_seen_at = context.requested_at

    if fired or verdict is not None:
        # A score bump or a status change must outlive the rejection it
        # causes; anything less makes repeated probing free.
        return _commit_and_return(db, verdict)

    db.flush()
    return None


def _commit_and_return(
    db: Session, verdict: PolicyVerdict | None
) -> PolicyVerdict | None:
    """Persist risk evidence durably, then hand back the verdict."""
    db.commit()
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

    # Locked read (SELECT ... FOR UPDATE): concurrent 403 probes on one
    # session would otherwise each read the same stale score/count and
    # commit last-write-wins — N parallel probes accumulating as one, which
    # is exactly the "repeated probing is free" hole this function closes.
    # The commit right below releases the lock immediately, so the window
    # is microseconds. It also refreshes past the gate's earlier read, so
    # the increment lands on the freshest committed state. (SQLite has no
    # FOR UPDATE; its whole-database locking serializes writers anyway.)
    session = db.get(UserSession, context.session_id, with_for_update=True)
    if session is None or session.status == SessionStatus.TERMINATED.value:
        return

    session.escalation_count += 1
    _add_risk(session, settings.RISK_SCORE_PRIVILEGE_ESCALATION, context.requested_at)
    _audit_session_event(
        db,
        session,
        context,
        "privilege_escalation",
        {"escalation_count": session.escalation_count},
    )

    # Evaluate the thresholds HERE, inside the same locked transaction
    # (review F4). Scoring without transitioning left the crossing in
    # limbo: a session at 50 taking +25 completed an ordinary 403, and the
    # NEXT request found the score without any current hard signal — so
    # the gate clamped what the taxonomy defines as HARD evidence
    # (privilege-escalation attempts) to a challenge instead of the direct
    # escalation the model documents. The transition and its audit
    # evidence now persist atomically with the increment, under the same
    # row lock, before the 403 goes out.
    score = effective_score(session, context.requested_at)
    if score >= settings.RISK_TERMINATE_THRESHOLD:
        _terminate(
            db,
            session,
            context,
            f"risk score {score} crossed terminate threshold "
            "(privilege-escalation attempts)",
        )
    elif (
        score >= settings.RISK_CHALLENGE_THRESHOLD
        and session.status == SessionStatus.ACTIVE.value
    ):
        session.status = SessionStatus.CHALLENGE_REQUIRED.value
        _audit_session_event(
            db,
            session,
            context,
            "session_challenged",
            {
                "why": f"risk score {score} crossed challenge threshold",
                "soft_clamp": False,
            },
        )
    db.commit()
