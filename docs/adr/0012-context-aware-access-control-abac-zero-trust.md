# ADR-0012: Context-aware access control — ABAC policy engine, zero-trust enforcement, session risk scoring

- **Status:** Accepted
- **Date:** 2026-08-15
- **Deciders:** Backend team (issue #67)
- **Related:** Issue #67, #31 (audit goals), #62 / ADR-0011 (platform
  operator isolation), ADR-0004 (authentication and security)

## Context
Authorization was static RBAC only (`require_role` / `require_privileged`
over `UserRole`). Stolen credentials presented from a new IP in the middle
of the night looked identical to a legitimate login: same role, same
permission, same outcome. The system had no way to weigh *context* — time
of day, geolocation, device fingerprint, IP reputation, or the sensitivity
of the resource being touched — and no per-session behavioural signal
after the initial OTP.

## Decision
1. **We add an ABAC policy engine that runs on EVERY request, layered on
   top of RBAC — never replacing it.** A zero-trust gate is wired as an
   application-level FastAPI dependency, so it executes before module
   gates and before `require_role` / `require_privileged`. It assembles a
   per-request `AccessContext` and evaluates ordered, pure rules that can
   return ALLOW, DENY, CHALLENGE (step-up) or TERMINATE. Rules only ever
   restrict: an ABAC ALLOW is "no contextual objection", not access.
2. **We classify every resource's data sensitivity by route path**
   (PUBLIC / INTERNAL / CONFIDENTIAL / RESTRICTED), with invoices and
   financial documents CONFIDENTIAL; payment recording, owner/platform
   surfaces (ADR-0011) and the audit trail RESTRICTED. Unknown paths
   default to INTERNAL, never PUBLIC.
3. **We enforce zero trust at the database layer too.** `get_db` tags
   request-scoped sessions; a global SQLAlchemy guard refuses ORM reads
   and flushes on such a session unless the gate stored an ALLOW verdict
   for *this* request — no implicit trust carried from a prior successful
   auth, and a route that bypasses the gate fails closed at its first DB
   touch.
4. **We audit every policy decision** (allow / deny / challenge /
   terminate) to the append-only `audit_events` trail (#31). Non-allow
   decisions are committed durably before the rejection they cause rolls
   the request back.
5. **CHALLENGE reuses the existing login → OTP flow.** It surfaces as 401
   `STEP_UP_REQUIRED` with `details.challenge = "otp"`; the fresh token
   pair from `/auth/login` + `/auth/verify-otp` satisfies it.

   **This holds only because `verify_otp` stamps a `sua`
   ("stepped-up-at") claim that the static rules read.** A challenge is
   satisfiable exactly when re-authenticating changes an input the rule
   evaluates. The session-risk CHALLENGE qualifies naturally — it flips
   `session.status`, and re-login mints a new session row. The *static*
   rules do not: `_rule_off_hours` is a pure function of the wall clock,
   the path's sensitivity and the HTTP method, none of which a new token
   moves. Without `sua` this decision was false for them, and the 401 was
   an unconditional lockout for the whole 22:00→06:00 window — the user
   re-authenticated, got the identical refusal, and had no way through.
   Any future rule returning CHALLENGE must honour `sua` for the same
   reason.

   `sua` rides in both tokens and is carried across refresh rotation
   **unchanged**: re-stamping it would let a stolen refresh token launder
   an indefinite step-up without ever proving an OTP. `iat` cannot serve
   this purpose because rotation resets it by design. A missing claim
   reads as "not stepped up" (fail closed). The lease lasts
   `ABAC_STEP_UP_TTL_MINUTES`, defaulted to a work shift (8h) rather than
   a transaction (30min): at 30min a night shift would demand ~16 OTP
   emails, and the security property is identical at any TTL because an
   attacker holding a stolen token has no inbox and so can never mint the
   claim at all.

   For the same reason, **CHALLENGE rules apply only to authenticated
   `user` principals**. A challenge is only coherent for a caller who can
   answer it: a machine-to-machine caller ("service", verified
   `X-Internal-Secret`) has no inbox, and an anonymous caller identifies
   no account to step up — authentication owns its refusal
   (`get_current_user` / `verify_internal_secret` still run and still
   reject). Challenging "anonymous" was doubly wrong: the 401
   `STEP_UP_REQUIRED` invited an OTP round trip that could never satisfy
   the rule, and it made the response contract for the same
   unauthenticated request flip with the tenant-local wall clock
   (step-up at night, plain 401 by day). Nothing widens: every
   RESTRICTED / CONFIDENTIAL surface sits behind an authentication gate,
   a stolen-but-valid token still classifies as `user` (and is
   challenged), and DENY rules (IP reputation, geo blocklist) apply to
   every principal.
6. **Continuous session risk scoring** (second tranche of #67): a
   per-session behavioural score persisted per request, with impossible
   travel, unusual data-access volume and privilege-escalation detection;
   crossing thresholds triggers automatic step-up or session termination.

   **The model is graduated and evidence-weighted** (third tranche),
   anchored on per-user behavioural baselines (`user_behavior_baselines`:
   known devices, known countries, typical active hours, typical
   per-window volume per sensitivity class):

   - **SOFT signals** — new device (25), new country (25), unusual hour
     (10), mild volume deviation (30) — score *deviation from the user's
     own baseline*, once per session, and only when there is history to
     deviate from. Individually each stays below the challenge threshold
     (allow + log); their maximum single-request sum (90) sits below the
     terminate threshold (100), and — **structurally** — a score crossing
     the terminate threshold with no HARD signal in the evaluation clamps
     to a challenge (audited as `soft_clamp`), so soft evidence alone — a
     new place, a new laptop, night work, one busy minute — can at worst
     force a step-up, never a termination, whatever it accumulates to
     across requests.
   - **HARD signals escalate directly**: impossible travel (70 — an
     immediate challenge; HARD only when
     `RISK_IMPOSSIBLE_TRAVEL_MAX_ACTION="terminate"`. The **default is
     `"challenge"`**: this deployment's market is mobile-heavy East
     Africa, where carrier-CGNAT exit-node hopping makes IP geolocation
     jump cities between requests, so a single hop still scores and still
     forces a step-up but can never corroborate a termination — otherwise
     the non-decaying session-start floor of a "new laptop, new place"
     user (50) plus one geolocation hop (70) would hard-lock exactly the
     user the soft-clamp exists to protect),
     exfiltration-scale reads (100 — 5x the global volume ceiling in one
     window terminates outright), repeated privilege-escalation 403s,
     repeated failed step-ups (exhausting the OTP budget terminates the
     challenged sessions it was trying to launder), and token/session
     identity anomalies (terminate).
   - **Threshold crossings act in the crossing request** (review F4): a
     privilege-escalation 403 that pushes the score over a line commits
     the transition (challenge or terminate) and its audit evidence
     atomically with the increment, under the same row lock. The crossing
     request itself still returns its RBAC 403 — the action was already
     denied, and rewriting the in-flight response would obscure the RBAC
     answer without denying anything more; the trail shows the
     `privilege_escalation` event and the `session_terminated` /
     `session_challenged` transition together, and every *subsequent*
     request is refused (401).
   - **Absorption**: a passed OTP step-up folds the verifying request's
     context (device, country, hour) into the user's baseline —
     `verify_otp` proves inbox control from that context, so the same new
     device/place never re-fires. Devices and countries enter the
     baseline **only** through this path; an attacker without the inbox
     can never launder their context in. Hour and volume statistics learn
     continuously from scored requests — they suppress false positives
     rather than grant trust, so poisoning them buys nothing.
     Absorbed trust is **not permanent**: each entry carries a
     `verified_at` stamp (refreshed on every verified touch) and stops
     counting as known after `RISK_BASELINE_TRUST_TTL_DAYS` (default 90)
     — an aged-out context fires the soft signals again, one challenge,
     then re-absorption. Every absorption is audited
     (`baseline_absorbed`), and the account owner is emailed for a new
     **device** and for a new **country** — the latter because replaying
     a known fingerprint was the laundering path with no other
     user-visible tell (line review §4). Migration `a7c31f08d2e4` stamps
     pre-aging rows; unstamped legacy entries count as fresh until their
     next verified touch.
   - **Fail-safe degradation**: an empty baseline, a missing geo signal
     or an unconfigured enrichment source yields *no signal*, never a
     penalty. Nothing is denied or terminated solely because enrichment
     data is unavailable.
   - The mild-volume ceiling adapts per sensitivity class to the learned
     typical volume (EWMA, clamped between `RISK_VOLUME_MIN_CEILING` and
     the global maximum) once enough active windows are observed.

7. **Risk scores decay; sessions expire.** Points shed at
   `RISK_DECAY_PER_HOUR` from the last anomaly. An undecayed score is a
   ratchet: benign noise (a browser auto-update +25, one busy minute +30,
   a stray 403 +25) reaches the 60-point challenge threshold on any
   long-lived session, so a legitimate user is eventually challenged for
   nothing. Decay is computed on read and settled into the column only
   when a detector fires, so a quiet session costs no writes. It applies
   **only to the score** — a session already flipped to
   `challenge_required` or `terminated` is never restored in place, so the
   "trust is re-established only by re-authentication" invariant holds.
   Sessions also expire on `SESSION_MAX_AGE_HOURS` and
   `SESSION_IDLE_TIMEOUT_MINUTES`, each with its own audited reason so an
   expiry never reads as a risk kill.

   **Decay forgives transient noise only; session-start evidence never
   decays.** The session-start soft signals (new device / new country /
   unusual hour) are facts about the session, not noise — a session that
   began on an unknown device in an unknown country does not stop having
   begun there. Their sum anchors a non-decaying `risk_floor` for the
   session's lifetime, so an attacker cannot pace anomalies against the
   decay clock (wait out the session-start batch, then land the next
   signal on a clean score) and accumulate below the challenge threshold
   forever. The floor dies with the session: a passed step-up mints a
   fresh session whose context was absorbed, so a legitimate user's next
   session carries no floor. Startup validation keeps the maximum
   possible floor below the terminate threshold, and the soft-clamp rule
   still guarantees soft evidence alone can never terminate.

8. **Risk evidence is durable, split by what is exploitable.** Score
   increments and status transitions commit explicitly — the 4xx they
   cause would otherwise roll back the evidence for it, making repeated
   probing free. The data-access volume counter lives in the shared
   `RateLimitStore`, not on the session row, for the same reason: a
   Postgres counter is rolled back by any failing request, so an attacker
   probing endpoints that error would reset their own window. Trail state
   (last seen, geo, fingerprint) rides the request transaction.

## What it does today
- `api/app/common/authz/sensitivity.py` — `SensitivityLevel` + ordered
  path classification rules.
- `api/app/common/authz/context.py` — `AccessContext` assembly: client IP
  (trusted-proxy aware), geolocation and device fingerprint from trusted
  edge headers (`ABAC_TRUST_CONTEXT_HEADERS`, off by default; fingerprint
  falls back to a server-derived header hash), config-driven IP-reputation
  denylist (`ABAC_IP_DENYLIST`: IPs / CIDRs / literal identifiers), and
  the tenant-local hour (`REPORTING_TIMEZONE`).
- `api/app/common/authz/engine.py` — ordered rules: IP reputation (DENY),
  geo blocklist (DENY), off-hours sensitive access (CHALLENGE: RESTRICTED
  any method, CONFIDENTIAL writes), unknown-geo RESTRICTED writes when a
  geo signal is configured (CHALLENGE).
- `api/app/common/authz/enforcement.py` — the gate: evaluate, publish the
  verdict, audit the decision, reject non-allow. PUBLIC probes (health,
  ping) bypass evaluation entirely.
- `api/app/common/authz/risk.py` — continuous session risk scoring: the
  graduated detectors, score decay, session lifetime checks, and the
  durability split (evidence commits explicitly; trail state rides the
  request transaction; the volume window lives in the shared
  `RateLimitStore`).
- `api/app/common/authz/baselines.py` — per-user behavioural baselines:
  step-up absorption, session-start soft signals, continuous hour/volume
  learning, adaptive per-class volume ceilings.
- `api/app/common/authz/db_guard.py` — the DB-layer guard and the narrow
  authz-internal bypass used to persist decision evidence.
- `api/app/main.py` — `FastAPI(dependencies=[Depends(zero_trust_gate)])`
  plus guard installation.
- Settings: `ABAC_ENABLED`, `ABAC_TRUST_CONTEXT_HEADERS`,
  `ABAC_IP_DENYLIST`, `ABAC_GEO_BLOCKLIST`, `ABAC_OFF_HOURS_START/END`
  (start == end disables; default 22 → 6 local), and
  `ABAC_AUDIT_ALLOW_DECISIONS`; the `RISK_*` and `SESSION_*` families
  configure every signal weight, threshold, ceiling and learning rate
  (rationale for each default lives in `api/.env.example` and
  `api/app/lib/config.py`).

## Business logic & rules
- Same role + same permission can yield different outcomes in different
  contexts; the authz matrix tests pin day-vs-night, good-vs-bad IP and
  geo variations for identical principals.
- ABAC never widens access: ADR-0011 isolation is untouched (a platform
  operator still fails tenant gates and vice versa), and authentication
  still owns the 401 for missing credentials.
- Off-hours means the organisation's local night (`REPORTING_TIMEZONE`),
  not UTC.
- Context headers are honoured only when the deployment explicitly trusts
  its edge; otherwise they are attacker-controlled and ignored.
- Every decision is one append-only audit row with rule, reason, path,
  method, sensitivity, principal, IP, geo country and device fingerprint.

## Consequences
- Positive: stolen-credential context anomalies now produce a step-up or
  a refusal instead of silent success; the decision trail is auditable;
  the DB guard converts "forgot the gate" bugs into fail-closed errors.
- Negative: one extra audit INSERT per business request (allow-auditing
  can be disabled); a second policy surface to reason about at every
  gate; context signals are only as good as the deployment's edge
  configuration (no outbound geo-IP lookups by design).

## Improvements
1. Owner-configurable policy rules (per-tenant off-hours window and
   sensitivity overrides).
2. Hash-chaining for `audit_events` (#31's end state).
3. Baseline decay for devices/countries not seen in months (currently
   oldest-evicted only at the cap).
4. An ops surface to list/terminate a user's sessions and inspect
   baselines (the data model already supports it).

## Resilience & <1s response rules
- The gate does zero network I/O and zero DB reads on the request path;
  its only DB write is the single audit INSERT inside the existing
  request transaction (`CommitOnSuccessRoute` owns the commit).
- Denylist parsing is cached per settings value; classification is a
  handful of string prefix checks.
- PUBLIC probes bypass evaluation entirely, so health checks stay on the
  fast path and are never throttled, challenged or audited.
- The engine is fail-closed by construction: unknown paths classify to
  INTERNAL and a missing verdict blocks DB access for request-scoped
  sessions.
