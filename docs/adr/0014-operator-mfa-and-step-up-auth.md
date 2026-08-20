# ADR-0014: Operator MFA (TOTP) and step-up re-authentication for the platform console

- **Status:** Proposed
- **Date:** 2026-08-20
- **Deciders:** Platform engineering (agent-drafted; requires human review)
- **Related:** ADR-0004 (authentication & security), ADR-0011 (platform-operator
  role), ADR-0013 (tenancy strategy — § Improvements item 7), issue #73,
  MR !77 (domain + line-level reviews), QA finding 09,
  `docs/runbooks/platform-operations.md`, `docs/runbooks/platform-operator.md`

> Numbering note: `0012` is taken by the context-aware access-control ADR
> (branch `duo/feature/67-*`) and `0013` is taken twice (tenancy strategy on
> !77's branch; PITR on `develop` via !74). `0014` is the first free number
> across every live branch at the time of writing.

## Context

The platform console (`/api/v1/platform/*`) is the highest-privilege surface
in the product: it can suspend tenants, grant/revoke module entitlements, and
read the operator audit trail. Until this ADR, the operator authenticated
exactly like a tenant user — password + emailed 6-digit OTP + 30-minute HS256
JWT + user/IP rate limiting (`api/app/modules/auth/service.py`). Both the
domain review and the line-level review on !77 flagged this as the
highest-priority security deferral: no possession factor beyond the email
inbox, no step-up re-auth on destructive actions, no network-level control.

Operating constraints:

- **~2–5 operators, ever.** The operator population is a handful of named
  platform staff (`docs/runbooks/platform-operator.md`), seeded only via a
  DB-access script — never via API (QA finding 09).
- **Enumeration safety is a hard invariant.** Every unauthenticated auth
  failure surfaces the same generic 401 (`_GENERIC_AUTH_ERROR`); MFA must not
  create a new oracle.
- **The audit trail records successful writes only** (documented in the
  runbook and on `GET /platform/audit`). Auditing *failed* step-up attempts
  is a deliberate extension this ADR must justify.
- The emailed OTP already provides a weak second channel, but it is
  phishable, depends on the inbox (a single credential in practice), and is
  shared with tenant users — it is not an operator-grade possession factor.

## Decision

**We require TOTP (RFC 6238) as the second factor for `platform_operator`
accounts now, with WebAuthn documented as the upgrade path.** Concretely:

1. **TOTP enrollment is mandatory** for operator console access. An
   unenrolled operator can sign in (password + email OTP) but receives only a
   **constrained enrollment-scoped token** (`mfa: "enroll"` claim) that
   reaches nothing under `/platform` except the `/platform/mfa` enrollment
   endpoints. Full console tokens (`mfa: "totp"`) are issued only when the
   sign-in includes a valid TOTP code (or single-use recovery code).
2. **Step-up re-authentication is per-action TOTP.** Destructive console
   writes — `PATCH /platform/owners/{id}/status` (suspend/reactivate) and
   `PATCH /platform/owners/{id}/modules/{module_key}` (entitlement changes,
   revocations *and* grants) — demand a fresh TOTP or recovery code in the
   `X-MFA-Code` header on the request itself. A live session is never
   sufficient.
3. **IP allowlisting for `/platform` is implemented** as an interim
   compensating control: a config-driven CIDR list
   (`PLATFORM_IP_ALLOWLIST`), empty = disabled, malformed = the application
   refuses to start (fail-closed at config validation), unparseable client
   address at runtime = denied (fail-closed at request time).
4. **Enrollment applies to existing seeded operators only.** The MFA
   endpoints operate exclusively on the authenticated caller
   (`current_user`); no route creates, promotes, or demotes any account
   (QA finding 09 intact).

### Decision matrix: second factor for ~2–5 operators

| Criterion | TOTP (RFC 6238) — **chosen now** | WebAuthn / FIDO2 — upgrade path |
|---|---|---|
| Operational cost at 2–5 operators | Minimal: any authenticator app; enrollment is copy-a-secret + confirm a code; nothing to purchase or ship | Requires hardware keys (purchase, shipping, spares) or platform authenticators; per-device registration and attestation policy decisions |
| Recovery / escrow story | Simple and already proven in this repo's patterns: 10 single-use hashed recovery codes issued at activation + DB-access break-glass (delete the MFA row, re-enroll) — same trust anchor as the operator seed script | Lost-key recovery needs ≥2 registered keys per person or falls back to… TOTP/recovery codes anyway; escrow of resident keys is not practical |
| Library / implementation maturity | RFC 6238 is ~30 lines of stdlib `hmac`/`struct` (SHA-1, 6 digits, 30 s) — implemented in `api/app/common/mfa.py` and pinned against the RFC 6238 Appendix B test vectors; **zero new attack surface from a third-party TOTP dependency** | Needs a substantial dependency (`webauthn`/`fido2`), challenge storage, origin/RP-ID configuration per deployment, and browser-side ceremony code in the console |
| Phishing resistance | Moderate: codes are phishable in real time (attacker must proxy within the 30 s window); mitigated here by per-action step-up + replay fence + IP allowlist | Strong: origin-bound, unphishable — the reason it remains the documented upgrade |
| Fit with today's console | Works for API/CLI use immediately (header + JSON fields); console UI needs only text inputs | Browser-only ceremony; blocks any curl/CLI break-glass path unless TOTP is kept anyway |

**Conclusion:** at this operator population, TOTP now delivers nearly all of
the risk reduction (possession factor independent of the email inbox,
fresh-proof on destructive writes, replay rejection) for a small fraction of
WebAuthn's cost, with no third-party crypto dependency for the code path
itself. WebAuthn's phishing resistance is real and is the documented upgrade
(follow-up issue; see Improvements) — its natural trigger is operator
headcount growth or a phishing incident. TOTP infrastructure (recovery
codes, step-up plumbing, audit events) is reused wholesale by a later
WebAuthn phase, so nothing built here is throwaway.

### Step-up model: per-action TOTP over a step-up session claim

Two candidate designs were considered:

- **Short-lived step-up claim**: `POST /platform/mfa/step-up` verifies a code
  and mints a ~5-minute elevated token; destructive routes require the claim.
- **Per-action TOTP (chosen)**: destructive routes require a fresh code in
  the `X-MFA-Code` header of the destructive request itself.

Per-action TOTP wins because: (a) there is no elevated *bearer artifact* to
steal — the proof is consumed by the action it authorizes; (b) no second
token type, expiry bookkeeping, or revocation path; (c) the window in which a
hijacked session can act destructively is exactly one TOTP step, not a
step-up TTL. The accepted cost: each destructive action consumes a TOTP
counter value (replay fence), so two destructive actions within one 30-second
step require waiting for the next code (drift acceptance of ±1 step softens
this). At 2–5 operators performing rare lifecycle/entitlement changes this
is a non-issue, and it is documented in the runbook.

## What it does today

- `api/app/common/mfa.py` — stdlib RFC 6238 TOTP (SHA-1/6-digit/30 s,
  configurable ±drift, RFC test-vector-pinned), otpauth URI builder,
  Fernet-based secret encryption, recovery-code generation/hashing.
- `api/app/modules/platform/models.py` — `operator_mfa_totp` (one row per
  operator: Fernet-encrypted secret, `pending|active` status, monotonic
  `last_used_counter` replay fence) and `operator_mfa_recovery_codes`
  (SHA-256 digests, single-use `used_at`).
- `api/app/modules/platform/service.py` — `OperatorMfaService`: enrollment
  start/activate, login second-factor check, step-up verification, recovery
  code burn; all rate-limited via the shared auth throttle store
  (`mfa:{user_id}` bucket) and audited.
- `api/app/modules/platform/router.py` — `GET /platform/mfa`,
  `POST /platform/mfa/enrollment`, `POST /platform/mfa/enrollment/activate`
  (all operator-gated like every platform route; reachable by
  enrollment-scoped tokens).
- `api/app/modules/auth/service.py` — `verify_otp` demands the TOTP /
  recovery code for **enrolled** operators (missing/wrong ⇒ the same generic
  401 as a wrong password — enumeration-safe) and stamps the `mfa` claim
  (`"totp"` or `"enroll"`) into both tokens; `refresh_access_token`
  propagates the claim, never upgrades it.
- `api/app/common/dependencies.py` — `get_current_user` confines operator
  tokens without `mfa == "totp"` to `/platform/mfa/*` (fail-closed for
  legacy/claimless operator tokens); `require_step_up` enforces the
  `X-MFA-Code` header on destructive routes;
  `enforce_platform_ip_allowlist` applies the CIDR check to every
  `/platform` route.

## Business logic & rules

- **Token issuance rule:** an operator access/refresh token pair carries
  `mfa: "totp"` iff the sign-in verified a TOTP or recovery code against an
  *active* enrollment; otherwise `mfa: "enroll"`. Tenant tokens carry no
  claim. Refresh preserves the claim verbatim — the only way to elevate is a
  fresh sign-in.
- **Constrained enrollment path:** `mfa != "totp"` ⇒ 403 on every `/platform`
  route except the exact `/platform/mfa` segment prefix. Enrollment
  endpoints act on `current_user` only — no account creation/promotion.
- **Enumeration safety:** in the unauthenticated `verify-otp` flow, a
  missing/wrong/replayed second factor raises the identical generic 401 and
  message as a wrong password or OTP. The response never reveals that the
  account is an operator or that MFA exists. (On *authenticated* step-up
  paths a clear 401 is returned — the caller has already proven full
  credentials, mirroring the suspension-403 precedent from !77.)
- **Replay rejection:** each successful TOTP verification records the
  accepted time-step counter; only strictly greater counters are accepted
  thereafter (shared fence across login and step-up).
- **Drift:** ±`MFA_TOTP_DRIFT_STEPS` steps (default 1, i.e. ±30 s) to absorb
  clock skew; anything older/newer is rejected — this is also the "step-up
  expiry": a code is proof for at most one drift window.
- **Rate limiting:** second-factor attempts are throttled per user id via the
  shared `RateLimitStore` (`MFA_MAX_ATTEMPTS` per
  `MFA_ATTEMPT_WINDOW_SECONDS`), on top of the existing per-email login
  throttle.
- **Recovery codes:** 10 per activation, 80-bit random, stored as SHA-256
  digests only (high-entropy ⇒ fast hash is appropriate, same reasoning as
  password-reset tokens), single-use, usable both at sign-in and as step-up
  proof; usage is audited. Re-enrollment (secret rotation) while active
  requires step-up proof and reissues codes, invalidating the old set.
- **Secrets handling:** TOTP seeds are Fernet-encrypted at rest
  (AES-128-CBC + HMAC-SHA-256) with a key from `MFA_ENCRYPTION_KEY`
  (dedicated, recommended in production) or derived via SHA-256 from
  `JWT_SECRET_KEY` when unset (disclosed trade-off: key-separation by
  derivation, not by independent secret). Plaintext seeds exist only in the
  enrollment response body; they are never logged and never appear in argv.
  The `cryptography` package is added for Fernet only.
- **Audit:** `operator_mfa` audit events (entity id = operator user id) are
  written in-transaction for `mfa_enrollment_started`, `mfa_enrolled`,
  `mfa_recovery_code_used`, `step_up_granted` — and, as a **deliberate
  extension of the successful-writes-only trail**, `step_up_denied` and
  `mfa_activation_failed`. Justification: a failed step-up on an
  authenticated operator session is precisely the "unexplained event ⇒
  potential compromise" signal the quarterly review and break-glass
  procedures key on; unlike unauthenticated login failures it carries a
  proven actor identity, cannot be generated anonymously to spam the trail
  (rate-limited, authenticated), and is not an enumeration oracle. Failure
  events are committed *before* the 401 propagates (the same
  explicit-commit-then-raise pattern as OTP attempt counting), so a rolled
  back request cannot erase its own evidence. Unauthenticated login MFA
  failures remain unaudited (no proven actor; the throttle store bounds
  them).
- **IP allowlist:** empty list = disabled (the default, so single-operator
  deployments without stable egress IPs are not locked out); non-empty =
  every `/platform` request's client address must fall inside one CIDR.
  Malformed configuration fails at startup (pydantic validator); a client
  address that cannot be parsed at runtime is denied. `X-Forwarded-For` is
  honoured only when `RATE_LIMIT_TRUST_FORWARDED_FOR` is already enabled
  (same trust decision as the rate limiter — one knob, one meaning).

## Consequences

**Positive**

- Console access now requires a possession factor independent of the email
  inbox; destructive actions additionally require proof freshness measured
  in seconds, not session lifetime.
- Compromise of an operator password + mailbox no longer suffices to suspend
  tenants or strip entitlements.
- Failed step-up attempts become auditable evidence tied to a proven actor.
- No third-party TOTP dependency; the only new package is `cryptography`
  (for at-rest encryption), a first-party PyCA project.

**Negative / accepted trade-offs**

- **The web console has no MFA UI yet.** Operators enroll and pass
  step-up via the API (documented curl flows in
  `docs/runbooks/platform-operator.md`); until the console UI follow-up
  ships, the browser console is read-only-at-best for operators (sign-in
  itself requires the TOTP field). Tracked as a follow-up issue rather than
  absorbed here.
- A `mfa: "totp"` refresh token remains valid for its configured lifetime
  even if the operator's enrollment row is later reset (break-glass): the
  holder keeps read access to `/platform` until expiry, though step-up
  fails closed (no active secret ⇒ no destructive writes). Session-binding
  hardening (revoke operator token families on MFA reset) is a follow-up.
- TOTP is real-time phishable; accepted at this scale, WebAuthn is the
  upgrade.
- Two destructive actions within one 30 s step require waiting for the next
  code (replay fence) — documented operator ergonomics cost.
- SHA-1 inside HOTP is HMAC-SHA-1 per RFC 6238 — not a collision-exposed
  use; noted to pre-empt scanner noise.

## Improvements

1. **Console MFA UX** (follow-up issue): enrollment screen (QR rendering of
   the otpauth URI), TOTP/recovery input at sign-in, step-up prompt on
   suspend/entitlement dialogs.
2. **WebAuthn phase** (follow-up issue): origin-bound second factor for
   sign-in and step-up, keeping TOTP + recovery codes as fallback; trigger =
   operator headcount growth or any phishing incident.
3. **Session binding** (follow-up issue): revoke an operator's refresh-token
   family on MFA reset/rotation, and consider binding `mfa: "totp"` access
   tokens to a `mfa_confirmed_at` timestamp claim checked against the
   enrollment row.
4. Dedicated `MFA_ENCRYPTION_KEY` in production deployments (documented in
   the runbook) so seed encryption is independent of the JWT signing key.
5. Alerting on `step_up_denied` bursts (once per-tenant metrics land, T5).

## Resilience & <1s response rules

- MFA verification adds at most two indexed single-row reads
  (`operator_mfa_totp` by unique `user_id`; recovery codes by `user_id`) plus
  HMAC math — microseconds; only on operator flows, never on tenant hot
  paths.
- The throttle store reuses the existing pluggable backend (in-memory /
  Redis) — fails open on backend outage exactly like the login throttle, so
  a cache outage degrades to "no throttle", never a lockout; the second
  factor itself still gates.
- IP allowlist evaluation is a linear scan over a handful of parsed CIDRs
  per `/platform` request only.
- Fail-closed defaults everywhere: claimless operator tokens are constrained;
  unparseable client IPs are denied; malformed allowlist config refuses to
  boot; a missing/undecryptable secret fails verification (operator falls
  back to recovery codes / break-glass, never silent bypass).
