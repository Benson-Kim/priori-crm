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
  baseline.
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
from datetime import UTC, datetime

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


def _remember(values: list, value: str, cap: int) -> bool:
    """Append ``value`` (most-recent-last, capped); True when it was new."""
    if value in values:
        return False
    values.append(value)
    del values[:-cap]
    return True


def absorb_context(db: Session, user_id, context: AccessContext | None) -> None:
    """Fold a step-up-verified request's context into the user's baseline.

    Called by ``verify_otp`` on SUCCESS only: completing the login → OTP
    round trip proves inbox control from this device, at this place, at
    this hour — exactly the evidence a step-up challenge asks for. The
    absorbed device/country stop firing as soft signals, so the user is
    never repeatedly challenged for the same context.

    Flush-only: rides the verify-otp request transaction, atomic with the
    session mint (``CommitOnSuccessRoute`` owns the commit).
    """
    if context is None:
        # No gate context (direct service call outside a request): there is
        # nothing trustworthy to absorb, and inventing a context would let
        # non-request code paths write trust. Skip quietly.
        return

    baseline = get_or_create_baseline(db, user_id)
    changed = False

    if context.device_fingerprint and _remember(
        baseline.known_devices, context.device_fingerprint, _MAX_KNOWN_DEVICES
    ):
        flag_modified(baseline, "known_devices")
        changed = True

    if (
        context.geo is not None
        and context.geo.country
        and _remember(
            baseline.known_countries, context.geo.country, _MAX_KNOWN_COUNTRIES
        )
    ):
        flag_modified(baseline, "known_countries")
        changed = True

    _bump_hour(baseline, context.local_hour)
    flag_modified(baseline, "hour_counts")

    baseline.updated_at = datetime.now(UTC)
    db.flush()
    if changed:
        logger.info(
            "Baseline absorbed step-up context for user %s",
            user_id,
            extra={
                "devices": len(baseline.known_devices),
                "countries": len(baseline.known_countries),
            },
        )


def _bump_hour(baseline, local_hour: int) -> None:
    """Count one observation in the tenant-local hour bucket (bounded)."""
    key = str(local_hour)
    baseline.hour_counts[key] = baseline.hour_counts.get(key, 0) + 1
    baseline.hour_observations += 1
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

    if (
        context.device_fingerprint
        and baseline.known_devices
        and context.device_fingerprint not in baseline.known_devices
    ):
        signals.append(
            SoftSignal(
                name="new_device",
                points=settings.RISK_SCORE_NEW_DEVICE,
                detail={
                    "device_fingerprint": context.device_fingerprint,
                    "known_devices": len(baseline.known_devices),
                },
            )
        )

    if (
        context.geo is not None
        and context.geo.country
        and baseline.known_countries
        and context.geo.country not in baseline.known_countries
    ):
        signals.append(
            SoftSignal(
                name="new_country",
                points=settings.RISK_SCORE_NEW_COUNTRY,
                detail={
                    "country": context.geo.country,
                    "known_countries": list(baseline.known_countries),
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


def learn_request(baseline, context: AccessContext) -> None:
    """Continuously learn hour and per-class volume from one scored request.

    Rides the request transaction (flush by the caller): losing an
    increment to a rollback only under-counts a failed request, which is
    the safe direction for statistics that exist to SUPPRESS anomalies.
    """
    _bump_hour(baseline, context.local_hour)
    flag_modified(baseline, "hour_counts")

    window = settings.RISK_VOLUME_WINDOW_SECONDS
    now = context.requested_at
    cls = context.sensitivity.value
    entry = baseline.volume_baselines.get(cls)
    if entry is None:
        entry = {
            "window_started_at": now.isoformat(),
            "count": 1,
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
            entry["count"] = 1
            entry["window_started_at"] = now.isoformat()
        else:
            entry["count"] = int(entry.get("count", 0)) + 1
    flag_modified(baseline, "volume_baselines")
    baseline.updated_at = now


def volume_ceiling(baseline, sensitivity_class: str) -> int:
    """Mild-volume ceiling for one sensitivity class, from learned habits.

    Adapts only after enough active windows are observed; before that the
    global ceiling applies. Clamped so a very quiet user is never flagged
    for ordinary activity (floor) and learning can never RAISE the ceiling
    past the configured global maximum.
    """
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
