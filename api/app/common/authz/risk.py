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
  step-up). HARD only when ``RISK_IMPOSSIBLE_TRAVEL_MAX_ACTION`` is
  ``"terminate"``; the default ``"challenge"`` caps it at a step-up —
  in a mobile-heavy CGNAT market carrier exit-node hopping makes IP
  geolocation jump cities between requests, so a single hop must never
  corroborate a termination (#67 line review §3).
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
(last seen, geo, fingerprint) rides the request transaction and is written
only when it changes MATERIALLY (#67 line review §5): identical values are
not restated, and ``last_seen_at`` refreshes at ``_TRAIL_REFRESH_SECONDS``
granularity — losing trail state on a failure is not exploitable.

**Hot-path cost** (#67 line review §5): the clean path — the overwhelming
majority of requests — is an optimistic (unlocked) session read plus
atomic shared-store counter hits; NO session-row write, NO row lock. The
``SELECT ... FOR UPDATE`` re-check runs only when a detector fires, the
session expired, or the trail changed materially, and baseline learning
writes are sampled 1-in-``RISK_BASELINE_LEARN_SAMPLE_N`` (weight-
compensated). See :func:`assess_session_risk` for the two-phase shape.

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
from collections import OrderedDict
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

#: How many volume windows an exfiltration-ceiling crossing observed on
#: the RATE-LIMITED path stays visible to the gate. The limiter rejects
#: before the gate runs, and with the Redis fixed windows the attacker's
#: next SERVED request often lands in a fresh window whose counter no
#: longer shows the crossing — so the crossing latches a flag that
#: bridges the boundary. Two windows is enough to bridge; deliberately
#: short so a fresh, legitimate re-login after the burst is not chased by
#: stale evidence for long.
_EXFIL_LATCH_WINDOWS = 2

#: How stale ``last_seen_at`` may grow before a clean request refreshes the
#: trail anyway (#67 line review §5). Bounds two things: the idle-timeout
#: granularity error (seconds, against timeouts measured in minutes-to-
#: hours) and how stale a STATIONARY session's geo anchor can get. Small
#: enough to be noise for both consumers; large enough that a busy session
#: costs one trail UPDATE per interval instead of one per request.
_TRAIL_REFRESH_SECONDS = 60

#: Per-process per-user request counters driving the 1-in-N baseline
#: learning cadence (#67 line review §5); LRU-bounded so an instance
#: serving many users cannot grow this without limit.
_LEARN_SAMPLE_MAX_USERS = 4096
_learn_sample_counters: OrderedDict[str, int] = OrderedDict()


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
    # Session-start soft evidence never decays (H10): a session that
    # BEGAN on an unknown device in an unknown country does not stop
    # having begun there, and decaying it let an attacker pace anomalies
    # — wait out the decay, trigger the next signal from a clean score —
    # accumulating below the challenge threshold forever. Decay forgives
    # transient noise only; the floor dies with the session (a step-up
    # mints a fresh session whose absorbed context carries no floor).
    floor = max(session.risk_floor or 0, 0)
    updated_at = _aware(session.risk_updated_at)
    if raw <= 0 or updated_at is None or settings.RISK_DECAY_PER_HOUR <= 0:
        return max(raw, floor, 0)
    elapsed_hours = (now - updated_at).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return max(raw, floor)
    shed = int(settings.RISK_DECAY_PER_HOUR * elapsed_hours)
    return max(raw - shed, floor, 0)


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


def _travel_measurements(
    session: "UserSession",  # noqa: F821
    context: AccessContext,
) -> tuple[float, float, float] | None:
    """``(distance_km, elapsed_hours, speed_kmh)`` of an implausible hop.

    Pure read — shared by the unlocked pre-screen and the locked detector
    so the two can never disagree on what counts as impossible travel.
    Returns ``None`` when nothing fires: no geo signal, no anchor
    (pre-anchor rows or a first-ever fix — fail-safe), a within-jitter
    distance, or a plausible speed.

    Elapsed time is anchored on ``last_geo_at`` — when the stored
    coordinates were CAPTURED — not on ``last_seen_at``, which requests
    (geolocated or not) update. With intermittent geo coverage the latter
    underestimates the elapsed time and so overestimates the speed: a
    user who genuinely travelled while sending non-geolocated requests
    would read as teleporting.
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
        return None

    distance_km = haversine_km(session.last_lat, session.last_lon, geo.lat, geo.lon)
    if distance_km < _MIN_TRAVEL_DISTANCE_KM:
        return None
    elapsed_hours = max(
        (context.requested_at - last_geo_at).total_seconds() / 3600.0,
        _MIN_ELAPSED_HOURS,
    )
    speed_kmh = distance_km / elapsed_hours
    if speed_kmh <= settings.RISK_IMPOSSIBLE_TRAVEL_KMH:
        return None
    return distance_km, elapsed_hours, speed_kmh


def _detect_impossible_travel(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
) -> bool:
    """Score and audit an implausible hop (see :func:`_travel_measurements`).

    Runs under the session row lock against re-checked state: a parallel
    request that already advanced the geo trail makes the re-check come
    out clean, so one hop is never scored twice (review F1).
    """
    measurements = _travel_measurements(session, context)
    if measurements is None:
        return False
    distance_km, elapsed_hours, speed_kmh = measurements
    geo = context.geo

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
        # Session-start deviations are facts about the session, not
        # transient noise: they anchor a non-decaying floor under the
        # score (H10), so pacing later anomalies against the decay clock
        # cannot launder them. The config validator keeps the maximum
        # floor below the terminate threshold, and the M1 clamp still
        # guarantees soft evidence alone never terminates.
        session.risk_floor = (session.risk_floor or 0) + signal.points
        _audit_session_event(
            db,
            session,
            context,
            f"soft_signal_{signal.name}",
            {"points": signal.points, **signal.detail},
        )
    return bool(signals)


def _request_cost(path: str) -> int:
    """Volume units one request charges against the window (#67 H12).

    Export and bulk-read endpoints (``/export``, ``/exports/...``) move
    far more data per request than a point read, so counting them as one
    unit let a slow trickle of full-table exports stay under every
    ceiling. They are charged ``RISK_VOLUME_EXPORT_COST`` units instead,
    consistent with issue #51's "exports bounded + audited" posture.
    """
    segments = path.split("/")
    if "export" in segments or "exports" in segments:
        return settings.RISK_VOLUME_EXPORT_COST
    return 1


def note_rate_limit_rejection(request: Request) -> None:
    """Count a rate-limiter 429 as data-access volume evidence (#67 F8).

    The authenticated rate limiter rejects at RATE_LIMIT_PER_MINUTE
    BEFORE the zero-trust gate runs, so the exfiltration detector — whose
    ceiling sits far above the limiter — could never see the hammering
    that proves exfiltration at scale: the harder an attacker pulled, the
    less the risk model saw. Every rejection now charges the same shared
    volume counters the gate reads (per session and per user), and a
    ceiling crossing observed here latches a short-lived flag so the
    evidence survives the window boundary between the burst and the
    attacker's next SERVED request. No DB access: this runs on the
    middleware's 429 path and must stay cheap and fail-safe.
    """
    from app.common.authz.context import _decode_token_leniently
    from app.common.authz.sensitivity import classify_path

    payload = _decode_token_leniently(request)
    if not payload:
        return
    sid = payload.get("sid")
    if not sid:
        return
    uid = payload.get("sub")

    store = _volume_store()
    window = settings.RISK_VOLUME_WINDOW_SECONDS
    latch_ttl = _EXFIL_LATCH_WINDOWS * window
    cost = _request_cost(request.url.path)
    cls = classify_path(request.url.path).value
    exfil_ceiling = (
        settings.RISK_VOLUME_MAX_REQUESTS * settings.RISK_VOLUME_EXFIL_MULTIPLIER
    )

    # Mild per-class counter: the gate compares it against the adaptive
    # ceiling; here the global ceiling only bounds the bookkeeping.
    store.hit(
        f"risk:vol:{sid}:{cls}", settings.RISK_VOLUME_MAX_REQUESTS, window, cost=cost
    )
    if not store.hit(f"risk:volx:{sid}", exfil_ceiling, window, cost=cost).allowed:
        store.set_flag(f"risk:volx:latch:{sid}", latch_ttl)
    if (
        uid
        and not store.hit(f"risk:volu:{uid}", exfil_ceiling, window, cost=cost).allowed
    ):
        store.set_flag(f"risk:volu:latch:{uid}", latch_ttl)


def _observe_volume(
    session: "UserSession",  # noqa: F821
    context: AccessContext,
    baseline,
) -> tuple[bool, bool, dict, dict]:
    """Charge this request against the shared volume counters (unlocked).

    Returns ``(soft_fired, hard_fired, soft_detail, hard_detail)`` — the
    detail dicts are the audit payloads the locked application step
    records verbatim, so observation and evidence cannot drift.

    Runs OUTSIDE the session row lock, exactly once per request: the
    store's operations are atomic across workers, and the single-slot
    ``fired`` latch keys guarantee a crossing is claimed by exactly ONE
    request per window — so the caller can apply the score under the
    locked re-check without double-charging or double-scoring (review
    F1/§5). ``baseline`` may be ``None`` (unlearned user): the global
    ceiling applies.

    Two ceilings, matching the signal taxonomy (#67):

    - **Mild deviation (SOFT)** — per sensitivity class, adaptive to the
      user's learned typical volume for that class
      (``baselines.volume_ceiling``); low weight, corroborates but never
      challenges alone.
    - **Exfiltration scale (HARD)** — an absolute, class-independent
      ceiling at ``RISK_VOLUME_EXFIL_MULTIPLIER`` times the global limit;
      no legitimate workflow reads at that rate, so it terminates
      directly.
    """
    store = _volume_store()
    window = settings.RISK_VOLUME_WINDOW_SECONDS
    sid = str(session.id)
    uid = str(session.user_id)
    cls = context.sensitivity.value
    cost = _request_cost(context.path)
    soft_fired = False
    hard_fired = False

    mild_ceiling = baselines.volume_ceiling(baseline, cls)
    over_mild = not store.hit(
        f"risk:vol:{sid}:{cls}", mild_ceiling, window, cost=cost
    ).allowed
    if over_mild and store.hit(f"risk:vol:fired:{sid}:{cls}", 1, window).allowed:
        soft_fired = True

    exfil_ceiling = (
        settings.RISK_VOLUME_MAX_REQUESTS * settings.RISK_VOLUME_EXFIL_MULTIPLIER
    )
    # Three exfiltration evidence sources, all HARD:
    # - the session's own served-request window;
    # - the PER-USER aggregate window (H11): volume split over N parallel
    #   sessions of one user still sums against one ceiling, so N logins
    #   cannot divide the reads N ways and stay under it;
    # - the latches set by the rate-limited path (F8): a crossing observed
    #   among 429s survives the window boundary to the next served request.
    over_exfil = not store.hit(
        f"risk:volx:{sid}", exfil_ceiling, window, cost=cost
    ).allowed
    over_user = not store.hit(
        f"risk:volu:{uid}", exfil_ceiling, window, cost=cost
    ).allowed
    latched = store.get_flag(f"risk:volx:latch:{sid}") or store.get_flag(
        f"risk:volu:latch:{uid}"
    )
    if (over_exfil or over_user or latched) and store.hit(
        f"risk:volx:fired:{sid}", 1, window
    ).allowed:
        hard_fired = True

    soft_detail = {
        "window_seconds": window,
        "max_requests": mild_ceiling,
        "sensitivity_class": cls,
    }
    hard_detail = {
        "window_seconds": window,
        "max_requests": exfil_ceiling,
        "scope": "user" if (over_user and not over_exfil) else "session",
        "rate_limited_evidence": latched,
    }
    return soft_fired, hard_fired, soft_detail, hard_detail


def _apply_volume_signals(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
    volume_soft: bool,
    volume_hard: bool,
    soft_detail: dict,
    hard_detail: dict,
) -> None:
    """Score and audit the pre-observed volume signals, under the row lock."""
    if volume_soft:
        _add_risk(session, settings.RISK_SCORE_VOLUME_ANOMALY, context.requested_at)
        _audit_session_event(db, session, context, "volume_anomaly", soft_detail)
    if volume_hard:
        _add_risk(session, settings.RISK_SCORE_EXFILTRATION, context.requested_at)
        _audit_session_event(db, session, context, "exfiltration_volume", hard_detail)


def _terminate(
    db: Session,
    session: "UserSession",  # noqa: F821
    context: AccessContext,
    reason: str,
) -> PolicyVerdict:
    from app.common.token_denylist import revoke_session_access

    session.status = SessionStatus.TERMINATED.value
    # The column is a bounded VARCHAR: clamp to it, because a terminate
    # must NEVER fail for the length of its own explanation — an
    # over-long reason would 500 the request and leave the risky session
    # alive. The audit event below carries the full reason regardless.
    limit = type(session).termination_reason.type.length
    session.termination_reason = reason if limit is None else reason[:limit]
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


def _lifetime_expiry_reason(
    session: "UserSession",  # noqa: F821
    now: datetime,
) -> str | None:
    """Why the session has expired, or ``None`` while it lives (pure read)."""
    created_at = _aware(session.created_at)
    if created_at is not None:
        age_hours = (now - created_at).total_seconds() / 3600.0
        if age_hours > settings.SESSION_MAX_AGE_HOURS:
            return "max session age exceeded"

    last_seen = _aware(session.last_seen_at)
    if last_seen is not None:
        idle_minutes = (now - last_seen).total_seconds() / 60.0
        if idle_minutes > settings.SESSION_IDLE_TIMEOUT_MINUTES:
            return "session idle timeout"

    return None


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
    reason = _lifetime_expiry_reason(session, context.requested_at)
    if reason is not None:
        return _terminate(db, session, context, reason)
    return None


def _status_verdict(
    session: "UserSession",  # noqa: F821
) -> PolicyVerdict | None:
    """The verdict a settled session status dictates (pure read)."""
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
    return None


def _trail_needs_update(
    session: "UserSession",  # noqa: F821
    context: AccessContext,
) -> bool:
    """Whether the session trail changed MATERIALLY this request (§5).

    The trail exists to feed the detectors (geo anchor, device
    fingerprint, idle timeout) — restating identical values every request
    bought nothing and cost a row UPDATE (and its lock) per request.
    ``last_seen_at`` is refreshed at ``_TRAIL_REFRESH_SECONDS``
    granularity, which is noise relative to the idle timeout it feeds
    (minutes to hours) and also bounds how stale the geo anchor of a
    STATIONARY session can grow.
    """
    if session.last_seen_at is None:
        return True
    if context.ip != session.last_ip:
        return True
    if (
        context.device_fingerprint
        and context.device_fingerprint != session.device_fingerprint
    ):
        return True
    geo = context.geo
    if geo is not None:
        if geo.country is not None and geo.country != session.last_country:
            return True
        if geo.has_coordinates and (
            geo.lat != session.last_lat or geo.lon != session.last_lon
        ):
            return True
    last_seen = _aware(session.last_seen_at)
    elapsed = (context.requested_at - last_seen).total_seconds()
    return elapsed >= _TRAIL_REFRESH_SECONDS


def _learn_sampled(db: Session, user_id, context: AccessContext) -> None:
    """Learn from this request at the configured sampling cadence (§5).

    ``learn_request`` used to UPDATE the per-user baseline row on every
    scored request — all sessions of one user serializing on one row,
    plus a row UPDATE per request, purely for statistics. Sampling 1-in-N
    per user (``RISK_BASELINE_LEARN_SAMPLE_N``) with each applied
    observation WEIGHTED by N keeps the learned statistics unbiased in
    expectation while dividing the write rate by N. The counters are
    per-process (each worker samples its own request stream — still
    1-in-N of the total); losing one to a restart merely delays the next
    sample, which only under-counts — the safe direction for statistics
    that SUPPRESS anomalies.
    """
    sample_n = max(1, settings.RISK_BASELINE_LEARN_SAMPLE_N)
    if sample_n > 1:
        key = str(user_id)
        count = _learn_sample_counters.get(key, 0) + 1
        _learn_sample_counters[key] = count
        _learn_sample_counters.move_to_end(key)
        while len(_learn_sample_counters) > _LEARN_SAMPLE_MAX_USERS:
            _learn_sample_counters.popitem(last=False)
        if count % sample_n:
            return
    baseline = baselines.get_or_create_baseline(db, user_id)
    baselines.learn_request(baseline, context, weight=sample_n)


def assess_session_risk(db: Session, context: AccessContext) -> PolicyVerdict | None:
    """Re-score the request's session; return a verdict when not cleared.

    Called by the zero-trust gate AFTER the static rules allowed the
    request (the gate has already published a provisional ALLOW verdict,
    so these reads/writes pass the DB guard). Returns None when the
    session is clean — the static ALLOW stands.

    **Optimistic read, locked re-check** (#67 line review §5). The row
    lock used to be taken on EVERY sid-carrying request and — the clean
    path never committing early — held to end of request, serializing all
    concurrent requests of one session for the duration of the slowest
    one. Now:

    1. The session is read UNLOCKED and a pure pre-screen decides whether
       anything must be written: a detector would fire, the session
       expired, or the trail changed materially. The volume counters are
       charged here (atomic in the shared store, exactly once per
       request; the single-slot ``fired`` latches make each crossing
       claimable by exactly one request per window).
    2. Only when something must change is the row re-loaded with
       ``SELECT ... FOR UPDATE`` (``Session.get`` with ``with_for_update``
       always refreshes) and the evaluation re-run against the
       re-checked, committed state — preserving the F1/M3 guarantee that
       every increment and transition lands on fresh state, with none of
       the clean path's serialization. A transition another request
       committed meanwhile (terminated / challenged) is honoured.

    The clean path — by far the common case — performs NO session-row
    write and takes NO row lock. (SQLite has no FOR UPDATE; its whole-
    database write lock serializes writers anyway. CI runs Postgres.)
    """
    if context.session_id is None:
        # Anonymous, service, or legacy (pre-sid) token: nothing to score.
        return None

    from app.modules.auth.models import UserBaseline, UserSession

    session = db.get(UserSession, context.session_id)
    if session is None:
        # A signed token naming a session we never minted (or one purged):
        # zero trust says refuse, not shrug.
        return PolicyVerdict(
            decision=Decision.TERMINATE,
            rule="session_risk",
            reason="Unknown session",
        )

    identity_mismatch = session.user_id != context.user_id
    if not identity_mismatch:
        settled = _status_verdict(session)
        if settled is not None:
            return settled

    # ---- Unlocked pre-screen (pure reads + atomic store charges) -------
    expired = _lifetime_expiry_reason(session, context.requested_at) is not None
    session_start = session.last_seen_at is None
    travel_hit = _travel_measurements(session, context) is not None
    device_changed = bool(
        context.device_fingerprint
        and session.device_fingerprint
        and context.device_fingerprint != session.device_fingerprint
    )
    # The baseline read is optimistic too: None (never learned) simply
    # means the global ceilings apply and no soft signal can fire.
    baseline = db.get(UserBaseline, session.user_id)
    volume_soft, volume_hard, volume_soft_detail, volume_hard_detail = (
        _observe_volume(session, context, baseline)
    )

    anything_fires = (
        identity_mismatch
        or expired
        or session_start
        or travel_hit
        or device_changed
        or volume_soft
        or volume_hard
    )
    if not anything_fires and not _trail_needs_update(session, context):
        # Clean path: nothing to write on the session row. Baseline
        # learning still proceeds, at its sampled cadence.
        _learn_sampled(db, session.user_id, context)
        return None

    # ---- Locked re-check ------------------------------------------------
    session = db.get(UserSession, context.session_id, with_for_update=True)
    if session is None:  # pragma: no cover — sessions are never deleted
        return PolicyVerdict(
            decision=Decision.TERMINATE,
            rule="session_risk",
            reason="Unknown session",
        )

    if session.user_id != context.user_id:
        return _commit_and_return(
            db, _terminate(db, session, context, "token/session identity mismatch")
        )

    settled = _status_verdict(session)
    if settled is not None:
        return settled

    expiry = _check_lifetime(db, session, context)
    if expiry is not None:
        return _commit_and_return(db, expiry)

    baseline = baselines.get_or_create_baseline(db, session.user_id)

    # Behavioural signals for this request, re-evaluated on locked state.
    # SOFT and HARD evidence are tracked apart: only a batch carrying HARD
    # evidence (exfiltration-scale reads; impossible travel where policy
    # says so) may terminate.
    soft_fired = _evaluate_session_start_softs(db, session, context, baseline)
    travel_fired = _detect_impossible_travel(db, session, context)
    # Whether impossible travel counts as HARD evidence is policy, not
    # taxonomy (#67 line review §3): under the default mobile-heavy
    # profile (RISK_IMPOSSIBLE_TRAVEL_MAX_ACTION="challenge") a firing
    # still scores — and at weight 70 alone still forces a step-up — but
    # is capped at CHALLENGE: carrier-CGNAT geolocation jitter must never
    # corroborate a termination. "terminate" restores the previous
    # behaviour for deployments with a trustworthy end-to-end geo signal.
    travel_is_hard = settings.RISK_IMPOSSIBLE_TRAVEL_MAX_ACTION == "terminate"
    hard_fired = travel_fired and travel_is_hard
    soft_fired |= travel_fired and not travel_is_hard
    soft_fired |= _detect_device_change(db, session, context)
    # The volume signals were observed (and the counters charged) in the
    # unlocked pre-screen; apply their score under the lock. The store's
    # fired-latches guarantee no other request claimed the same crossing.
    _apply_volume_signals(
        db,
        session,
        context,
        volume_soft,
        volume_hard,
        volume_soft_detail,
        volume_hard_detail,
    )
    soft_fired |= volume_soft
    hard_fired |= volume_hard
    fired = soft_fired or hard_fired

    # Continuous learning (typical hours, typical per-class volume) rides
    # the request transaction at the sampled cadence: losing an increment
    # to a rollback only under-counts, which is safe for statistics that
    # SUPPRESS anomalies.
    _learn_sampled(db, session.user_id, context)

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
    # row lock, before the 403 goes out. Pinned semantics (ADR-0012): the
    # crossing request keeps its RBAC 403 — the action was already
    # denied — while the transition governs every subsequent request.
    score = effective_score(session, context.requested_at)
    if score >= settings.RISK_TERMINATE_THRESHOLD:
        _terminate(
            db,
            session,
            context,
            f"risk score {score} crossed terminate threshold (escalation)",
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
