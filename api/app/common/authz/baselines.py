"""Per-user behavioural baselines: learning, absorption, soft signals (#67).

The graduated, evidence-weighted risk model rests on knowing what is
NORMAL for each user, so the detectors can weigh *deviation from this
user's own behaviour* instead of firing on absolutes:

- **Known devices / countries** enter the baseline exclusively through a
  passed OTP step-up (:func:`absorb_context`, called by
  ``AuthService.verify_otp`` with the verifying request's context). This
  is the absorption rule the issue demands: a passed challenge folds the
  triggering soft signals into the baseline, so a genuine user is
  challenged for a new device or place at most once — while an attacker
  without inbox access can never launder their context into the victim's
  baseline. Absorbed trust is NOT permanent (#67 line review §4): each
  entry carries a ``verified_at`` stamp, refreshed on every verified
  touch, and stops counting as known after
  ``RISK_BASELINE_TRUST_TTL_DAYS`` — an aged-out context fires the soft
  signals again (one challenge, then re-absorption on success). Legacy
  entries stored as plain strings (pre-aging rows) count as fresh until
  their next verified touch stamps them; migration ``a7c31f08d2e4``
  stamps existing rows in place.
- **Typical hours** and **typical per-window volume per sensitivity
  class** learn continuously from scored requests
  (:func:`learn_request`). They exist to suppress false positives, never
  to grant trust, so poisoning them buys an attacker nothing.

Soft-signal evaluation (:func:`evaluate_session_start_signals`) runs once
per session — on its first scored request — and each signal fires only
when the user HAS a baseline to deviate from (fail-safe degradation:
absence of history or of geo enrichment must never read as anomaly).

All baseline writes ride the request transaction: losing a learning
increment to a rollback is not exploitable, unlike the risk score itself
(see ``risk.py`` for the durability split).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.common.authz.context import AccessContext
from app.lib.config import settings

logger = logging.getLogger(__name__)

#: Cap on remembered devices / countries: oldest evicted first. Bounded so
#: the JSON stays small; 8 devices is generous for one human.
_MAX_KNOWN_DEVICES = 8
_MAX_KNOWN_COUNTRIES = 8

#: When the total hour observations reach this, all buckets are halved so
#: the histogram tracks CURRENT habits instead of fossilizing early ones.
_HOUR_HISTOGRAM_CAP = 5000


@dataclass(frozen=True, slots=True)
class SoftSignal:
    """One baseline-deviation signal with its provenance (for auditing)."""

    name: str
    points: int
    detail: dict


def get_or_create_baseline(db: Session, user_id) -> "UserBaseline":  # noqa: F821
    """Load the user's baseline row, creating an empty one on first sight.

    Idempotent upsert semantics (#67 review H14/L3): two concurrent
    first-scored requests can both observe "no row" and both INSERT — the
    loser's primary-key violation must not surface as a 500. The INSERT
    runs inside a SAVEPOINT so an IntegrityError rolls back only the
    attempted insert (never the surrounding request transaction), and the
    winner's committed row is re-fetched instead.
    """
    from app.modules.auth.models import UserBaseline

    baseline = db.get(UserBaseline, user_id)
    if baseline is not None:
        return baseline

    try:
        with db.begin_nested():
            baseline = UserBaseline(
                user_id=user_id,
                known_devices=[],
                known_countries=[],
                hour_counts={},
                hour_observations=0,
                volume_baselines={},
            )
            db.add(baseline)
            db.flush()
    except IntegrityError:
        # A concurrent request won the first-INSERT race; use its row.
        baseline = db.get(UserBaseline, user_id)
        if baseline is None:  # pragma: no cover — the winner's row exists
            raise
    return baseline


def _entry_value(entry) -> str | None:
    """The trusted value inside one baseline entry.

    Entries are ``{"value": ..., "verified_at": ...}`` dicts; legacy rows
    (pre-aging) stored plain strings. Anything else is ignored.
    """
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        value = entry.get("value")
        if isinstance(value, str):
            return value
    return None


def _entry_is_fresh(entry, now: datetime) -> bool:
    """Whether the entry still counts as known (inside the trust TTL).

    Legacy entries without a ``verified_at`` stamp count as fresh:
    failing them wholesale would re-challenge every existing user at
    once on deploy. They are stamped on their next verified touch, and
    migration ``a7c31f08d2e4`` stamps stored rows in place.
    """
    verified_at = None
    if isinstance(entry, dict):
        verified_at = _parse_instant(entry.get("verified_at"))
    if verified_at is None:
        return True
    ttl = timedelta(days=settings.RISK_BASELINE_TRUST_TTL_DAYS)
    return now - verified_at < ttl


def entry_values(entries: list) -> list[str]:
    """All entry values, fresh or aged-out (introspection/audit/tests)."""
    return [v for v in (_entry_value(e) for e in entries or []) if v]


def fresh_values(entries: list, now: datetime) -> set[str]:
    """The values that still count as known at ``now``."""
    return {
        value
        for entry in entries or []
        if (value := _entry_value(entry)) and _entry_is_fresh(entry, now)
    }


def describe_entries(entries: list, now: datetime) -> list[dict]:
    """``{value, verified_at, fresh}`` view of the raw entries (issue #85).

    The ops surface's read model: exposes each absorbed device/country
    with its ``verified_at`` stamp and whether it still counts as known
    at ``now`` — the same freshness rule the soft signals evaluate
    (``_entry_is_fresh``), so what the operator sees is exactly what the
    detectors will do. Legacy string entries read as fresh with no
    stamp, matching their runtime semantics.
    """
    described: list[dict] = []
    for entry in entries or []:
        value = _entry_value(entry)
        if value is None:
            continue
        verified_at = None
        if isinstance(entry, dict):
            verified_at = _parse_instant(entry.get("verified_at"))
        described.append(
            {
                "value": value,
                "verified_at": verified_at,
                "fresh": _entry_is_fresh(entry, now),
            }
        )
    return described


def expire_entry(entries: list, value: str, now: datetime) -> dict | None:
    """Mark one absorbed entry aged-out IN PLACE (issue #85).

    Operator remediation for absorbed-but-suspect trust (an inbox
    compromise that passed a step-up): the entry is not deleted — that
    would edit history invisibly — its ``verified_at`` is rewritten to
    just past ``RISK_BASELINE_TRUST_TTL_DAYS``, the exact aged-out shape
    the TTL machinery already handles. The next use of that context
    re-fires the soft signal (one challenge), and a passed re-step-up
    re-absorbs it as NEW trust (owner notification included — the
    ``_remember`` path treats aged-out values as never known).

    Returns the entry's previous state (for the audit ``before``), or
    ``None`` when no entry carries ``value``. The caller owns
    ``flag_modified`` and the audit row.
    """
    expired_stamp = (
        now - timedelta(days=settings.RISK_BASELINE_TRUST_TTL_DAYS, seconds=1)
    ).isoformat()
    for index, entry in enumerate(entries or []):
        entry_value = _entry_value(entry)
        if entry_value != value:
            continue
        before = {
            "value": entry_value,
            "verified_at": entry.get("verified_at")
            if isinstance(entry, dict)
            else None,
            "was_fresh": _entry_is_fresh(entry, now),
        }
        entries[index] = {"value": entry_value, "verified_at": expired_stamp}
        return before
    return None


def _remember(entries: list, value: str, cap: int, now: datetime) -> bool:
    """Record ``value`` most-recent-last with a fresh ``verified_at``;
    True when the value was not currently known.

    - A still-fresh known value is re-stamped and MOVED to the
      most-recent slot (#67 H13): the cap then evicts the least-recently-
      VERIFIED entry, never a device or country still in active,
      re-verified use.
    - An entry past ``RISK_BASELINE_TRUST_TTL_DAYS`` no longer counts as
      known (#67 line review §4): re-absorbing its value reads as NEW —
      trust re-entering the baseline is notification-worthy exactly like
      the first time. Aged-out entries are pruned here so dead trust
      cannot linger in the JSON.
    """
    was_fresh = False
    kept: list = []
    for entry in entries:
        entry_value = _entry_value(entry)
        if entry_value is None:
            continue
        if entry_value == value:
            if _entry_is_fresh(entry, now):
                was_fresh = True
            continue  # re-appended below with a fresh stamp either way
        if _entry_is_fresh(entry, now):
            kept.append(entry)
    kept.append({"value": value, "verified_at": now.isoformat()})
    entries[:] = kept[-cap:]
    return not was_fresh


@dataclass(frozen=True, slots=True)
class AbsorptionResult:
    """What a step-up absorption actually added to the baseline."""

    new_device: bool = False
    new_country: bool = False


def absorb_context(
    db: Session, user_id, context: AccessContext | None
) -> AbsorptionResult:
    """Fold a step-up-verified request's context into the user's baseline.

    Called by ``verify_otp`` on SUCCESS only: completing the login → OTP
    round trip proves inbox control from this device, at this place, at
    this hour — exactly the evidence a step-up challenge asks for. The
    absorbed device/country stop firing as soft signals, so the user is
    never repeatedly challenged for the same context.

    Absorption is long-lived trust (bounded by
    ``RISK_BASELINE_TRUST_TTL_DAYS``), so it leaves a durable trace (#67
    H13): every absorption writes a ``baseline_absorbed`` audit event,
    and a NEW device — or a NEW country, even on a known fingerprint
    (#67 line review §4: fingerprint replay is the laundering path with
    no other tell) — additionally triggers a user notification (sent by
    the caller, which knows the user's email); a compromised inbox is
    exactly the scenario where that mail is the victim's only tell.
    Known entries are LRU-touched and re-stamped so the cap evicts the
    least-recently-verified context, never one still in active use.

    Flush-only: rides the verify-otp request transaction, atomic with the
    session mint (``CommitOnSuccessRoute`` owns the commit).
    """
    if context is None:
        # No gate context (direct service call outside a request): there is
        # nothing trustworthy to absorb, and inventing a context would let
        # non-request code paths write trust. Skip quietly.
        return AbsorptionResult()

    baseline = get_or_create_baseline(db, user_id)
    new_device = False
    new_country = False
    # The verified request's evaluation instant — the same clock every
    # other baseline read/write uses (learn_request, session-start
    # signals), so TTL freshness and verified_at stamps stay coherent.
    now = context.requested_at

    if context.device_fingerprint:
        new_device = _remember(
            baseline.known_devices,
            context.device_fingerprint,
            _MAX_KNOWN_DEVICES,
            now,
        )
        # Both a new entry and an LRU touch reorder the list: persist.
        flag_modified(baseline, "known_devices")

    if context.geo is not None and context.geo.country:
        new_country = _remember(
            baseline.known_countries, context.geo.country, _MAX_KNOWN_COUNTRIES, now
        )
        flag_modified(baseline, "known_countries")

    _bump_hour(baseline, context.local_hour)
    flag_modified(baseline, "hour_counts")

    baseline.updated_at = datetime.now(UTC)

    # Durable trace of trust entering the baseline (append-only trail,
    # #31): what was absorbed, from where, and whether it was new.
    from app.common.audit import record_audit_event

    record_audit_event(
        db,
        actor_id=user_id,
        entity_type="baseline",
        entity_id=user_id,
        action="baseline_absorbed",
        after={
            "device_fingerprint": context.device_fingerprint,
            "country": context.geo.country if context.geo else None,
            "local_hour": context.local_hour,
            "ip": context.ip,
            "new_device": new_device,
            "new_country": new_country,
            "known_devices": len(baseline.known_devices),
            "known_countries": len(baseline.known_countries),
        },
    )
    db.flush()
    if new_device or new_country:
        logger.info(
            "Baseline absorbed step-up context for user %s",
            user_id,
            extra={
                "devices": len(baseline.known_devices),
                "countries": len(baseline.known_countries),
            },
        )
    return AbsorptionResult(new_device=new_device, new_country=new_country)


def _bump_hour(baseline, local_hour: int, weight: int = 1) -> None:
    """Count ``weight`` observations in the tenant-local hour bucket.

    ``weight`` compensates for the learning sample cadence (#67 line
    review §5): one applied observation stands for ``weight`` requests,
    keeping the histogram unbiased in expectation.
    """
    key = str(local_hour)
    baseline.hour_counts[key] = baseline.hour_counts.get(key, 0) + weight
    baseline.hour_observations += weight
    if baseline.hour_observations >= _HOUR_HISTOGRAM_CAP:
        baseline.hour_counts = {
            hour: count // 2
            for hour, count in baseline.hour_counts.items()
            if count // 2 > 0
        }
        baseline.hour_observations = sum(baseline.hour_counts.values())


def _hour_is_usual(baseline, local_hour: int) -> bool:
    """Whether the hour (or an adjacent one) has ever been observed.

    Adjacent hours count so a habitual 08:00-17:00 worker starting at
    07:40 one morning is not an anomaly — bucket edges are not habits.
    """
    for offset in (-1, 0, 1):
        if baseline.hour_counts.get(str((local_hour + offset) % 24), 0) > 0:
            return True
    return False


def evaluate_session_start_signals(
    baseline, context: AccessContext
) -> list[SoftSignal]:
    """SOFT signals for a session's first scored request, vs the baseline.

    Each fires only when there is history to deviate from — an empty
    baseline (first-ever session, or a signal source that is not
    configured) yields no signal, never a penalty. Weights are configured
    so any single soft signal, and any pair, stays below the challenge
    threshold; even the full set stays below terminate.
    """
    signals: list[SoftSignal] = []

    # Membership is checked against the FRESH entries only (#67 line
    # review §4): an entry past RISK_BASELINE_TRUST_TTL_DAYS has aged out
    # of the baseline and its value deviates again. "History to deviate
    # from" stays anchored on the raw list — a user whose entries all
    # expired HAS a history, and their return from an aged-out context is
    # exactly the one-challenge-then-reabsorb moment aging exists for.
    now = context.requested_at
    known_devices = fresh_values(baseline.known_devices, now)
    known_countries = fresh_values(baseline.known_countries, now)

    if (
        context.device_fingerprint
        and baseline.known_devices
        and context.device_fingerprint not in known_devices
    ):
        signals.append(
            SoftSignal(
                name="new_device",
                points=settings.RISK_SCORE_NEW_DEVICE,
                detail={
                    "device_fingerprint": context.device_fingerprint,
                    "known_devices": len(known_devices),
                },
            )
        )

    if (
        context.geo is not None
        and context.geo.country
        and baseline.known_countries
        and context.geo.country not in known_countries
    ):
        signals.append(
            SoftSignal(
                name="new_country",
                points=settings.RISK_SCORE_NEW_COUNTRY,
                detail={
                    "country": context.geo.country,
                    "known_countries": sorted(known_countries),
                },
            )
        )

    if (
        baseline.hour_observations >= settings.RISK_BASELINE_MIN_HOUR_OBSERVATIONS
        and not _hour_is_usual(baseline, context.local_hour)
    ):
        signals.append(
            SoftSignal(
                name="unusual_hour",
                points=settings.RISK_SCORE_UNUSUAL_HOUR,
                detail={
                    "local_hour": context.local_hour,
                    "observations": baseline.hour_observations,
                },
            )
        )

    return signals


def learn_request(baseline, context: AccessContext, weight: int = 1) -> None:
    """Learn hour and per-class volume from one scored request.

    Rides the request transaction (flush by the caller): losing an
    increment to a rollback only under-counts a failed request, which is
    the safe direction for statistics that exist to SUPPRESS anomalies.

    ``weight`` compensates for the 1-in-N learning sample cadence (#67
    line review §5, ``RISK_BASELINE_LEARN_SAMPLE_N``): each applied
    observation stands for ``weight`` requests, so hour histograms and
    per-window volume counts stay unbiased in expectation while the
    baseline row is written N times less often.
    """
    weight = max(1, weight)
    _bump_hour(baseline, context.local_hour, weight=weight)
    flag_modified(baseline, "hour_counts")

    window = settings.RISK_VOLUME_WINDOW_SECONDS
    now = context.requested_at
    cls = context.sensitivity.value
    entry = baseline.volume_baselines.get(cls)
    if entry is None:
        entry = {
            "window_started_at": now.isoformat(),
            "count": weight,
            "ewma": 0.0,
            "windows": 0,
        }
        baseline.volume_baselines[cls] = entry
    else:
        started = _parse_instant(entry.get("window_started_at"))
        if started is None or (now - started).total_seconds() >= window:
            # Fold the finished window into the EWMA and start a new one.
            count = int(entry.get("count", 0))
            windows = int(entry.get("windows", 0))
            alpha = settings.RISK_VOLUME_LEARNING_ALPHA
            previous = float(entry.get("ewma", 0.0))
            if windows == 0:
                entry["ewma"] = float(count)
            else:
                entry["ewma"] = alpha * count + (1 - alpha) * previous
            entry["windows"] = windows + 1
            entry["count"] = weight
            entry["window_started_at"] = now.isoformat()
        else:
            entry["count"] = int(entry.get("count", 0)) + weight
    flag_modified(baseline, "volume_baselines")
    baseline.updated_at = now


def volume_ceiling(baseline, sensitivity_class: str) -> int:
    """Mild-volume ceiling for one sensitivity class, from learned habits.

    Adapts only after enough active windows are observed; before that the
    global ceiling applies (including ``baseline is None`` — a user whose
    baseline row does not exist yet, read optimistically by the gate's
    unlocked pre-screen, #67 line review §5). Clamped so a very quiet
    user is never flagged for ordinary activity (floor) and learning can
    never RAISE the ceiling past the configured global maximum.
    """
    if baseline is None:
        return settings.RISK_VOLUME_MAX_REQUESTS
    entry = (baseline.volume_baselines or {}).get(sensitivity_class)
    min_windows = settings.RISK_VOLUME_MIN_LEARNED_WINDOWS
    if not entry or int(entry.get("windows", 0)) < min_windows:
        return settings.RISK_VOLUME_MAX_REQUESTS
    adaptive = int(
        float(entry.get("ewma", 0.0)) * settings.RISK_VOLUME_DEVIATION_MULTIPLIER
    )
    return max(
        settings.RISK_VOLUME_MIN_CEILING,
        min(adaptive, settings.RISK_VOLUME_MAX_REQUESTS),
    )


def _parse_instant(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
