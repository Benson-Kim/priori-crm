# Runbook: platform-operator account (provisioning, rotation, break-glass)

Audience: the deployment (platform) operator / on-call engineer with direct
database access. Related: ADR-0011, issue #62, QA finding 09.

## Why this exists

ADR-0011 made module entitlements **operator-granted**: every entitlement
write goes through `PATCH /platform/owners/{owner_id}/modules/{module_key}`,
gated on the `platform_operator` role. That role is a **disjoint authority
axis** — it is not a tenant role, is not in `PRIVILEGED_ROLES`, carries no
access to tenant business data, and can never be obtained through
registration or any user-management flow.

> **Operator creation is deliberately NOT exposed through any API.**
> There is no HTTP endpoint that creates, promotes, or self-promotes a
> `platform_operator` user, and none may ever be added (QA finding 09: no
> API-reachable privilege escalation). The only way to obtain an operator
> account is the seed script below, run with direct database access.

Without an operator account, entitlement grants/revocations are impossible
on a deployment (modules still default to enabled, so the app remains
functional — but no entitlement can be changed).

## When to provision

- **Fresh deployment**: immediately after the first `alembic upgrade head`,
  as part of environment bring-up.
- **Existing deployment upgraded past !56**: once, at rollout — entitlement
  writes are blocked until the operator exists.
- **Credential rotation or break-glass recovery**: any time (see below).

## How to run the seed script

The script lives inside the API package and needs the API's environment
(installed dependencies plus a valid `DATABASE_URL`). Run it from the `api/`
directory of the deployed code — e.g. inside the running API container:

```shell
# non-interactive (CI / bring-up automation)
PLATFORM_OPERATOR_EMAIL='ops@your-platform.example' \
PLATFORM_OPERATOR_PASSWORD='<strong password>' \
    python -m app.scripts.create_platform_operator

# interactive (prompts; the password is read hidden and never echoed)
python -m app.scripts.create_platform_operator
```

Rules the script enforces:

- **Credentials are never CLI arguments.** Environment variables or the
  interactive prompt only — argv would land in shell history and process
  lists. The password is bcrypt-hashed (`app.common.security.hash_password`)
  and is never printed back; on success the script logs only the user id,
  the role, and the action taken.
- **Idempotent.** Re-running with the same email updates that single
  `users` row (rotating the password and reactivating the account); it
  never creates a duplicate. Exactly one row per email, satisfying the
  `ck_users_valid_role` CHECK constraint.
- **Tenant users are protected.** If the email already belongs to a tenant
  user (`admin`/`manager`/`member`), the script refuses and exits non-zero.
  Promoting an existing tenant user requires the explicit `--force-role`
  flag — prefer a dedicated operator email over promotion, so platform and
  tenant identities stay separate.
- **Password policy.** The shared account policy applies (8–128 chars, at
  least one letter and one digit); login enforces the same policy, so the
  script rejects a password that could never sign in.
- **Clear failures.** Missing/invalid `DATABASE_URL` or an unreachable
  database exits with code 2 and a clear message; invalid input or a
  refused role change exits with code 1. Nothing is written on failure.

The operator signs in through the normal flow (login → email OTP), so the
operator email must be able to receive OTP mail from the deployment's SES
sender. **Since #73 / ADR-0014 the seed script is only step one:** a freshly
seeded operator holds a *constrained* account until TOTP enrollment is
completed (below) — sign-in works, but the token it yields reaches only the
`/platform/mfa` enrollment endpoints, not the console proper.

## MFA enrollment (mandatory before console use — ADR-0014, issue #73)

Enrollment applies to **existing seeded operators only** and acts on the
authenticated caller — there is still no API that creates or promotes an
operator (QA finding 09). The web console has no MFA UI yet (follow-up
issue), so use the API directly:

1. **Sign in** (login → email OTP → `POST /auth/verify-otp`). As an
   unenrolled operator you receive an enrollment-scoped access token.
2. **Provision a seed**: `POST /platform/mfa/enrollment` (Bearer token from
   step 1). The response contains the base32 `secret` and an `otpauth_uri`
   — **shown exactly once, never logged, never in argv**. Add it to your
   authenticator app (scan/enter; QR rendering arrives with the console UI).
3. **Confirm**: `POST /platform/mfa/enrollment/activate` with
   `{"code": "<live 6-digit code>"}`. The response returns **10 single-use
   recovery codes, exactly once** — store them in the team password
   manager; the server keeps only SHA-256 digests.
4. **Sign in again**, now including the second factor:

   ```shell
   curl -s -X POST "$API/auth/verify-otp" -H 'Content-Type: application/json' \
     -d '{"email":"ops@…","code":"<emailed OTP>","totp_code":"<authenticator code>"}'
   ```

   (or `"recovery_code": "<xxxxx-xxxxx-xxxxx-xxxxx>"` instead of
   `totp_code`; each recovery code works once.) Only this MFA-verified
   sign-in yields a full console token; `GET /platform/mfa` shows your
   enrollment state and remaining recovery codes at any time.

### Step-up on destructive actions

Suspend/reactivate (`PATCH /platform/owners/{id}/status`) and every
entitlement change (`PATCH /platform/owners/{id}/modules/{key}`) demand a
**fresh** proof in the `X-MFA-Code` header on the request itself:

```shell
curl -s -X PATCH "$API/platform/owners/$OWNER/status" \
  -H "Authorization: Bearer $TOKEN" -H "X-MFA-Code: <live TOTP or recovery code>" \
  -H 'Content-Type: application/json' -d '{"status":"suspended"}'
```

Codes are replay-fenced: a code is spent by the action it authorizes, so a
second destructive action within the same 30-second step needs the next
code. Grants **and denials** are audited (`step_up_granted` /
`step_up_denied` in `GET /platform/audit`).

### Rotating the TOTP seed

`POST /platform/mfa/enrollment` while enrolled requires a valid
`X-MFA-Code` step-up proof, returns a fresh secret (pending), and step-up
fails closed until you activate it — activate immediately. Activation
reissues recovery codes and invalidates the previous set.

### MFA break-glass: authenticator AND recovery codes lost

Requires direct database access, mirroring the password break-glass:

1. Delete the enrollment (this also leaves any live session unable to pass
   step-up — fail closed):
   `DELETE FROM operator_mfa_totp WHERE user_id = '<operator user id>';`
   `DELETE FROM operator_mfa_recovery_codes WHERE user_id = '<operator user id>';`
2. Rotate the operator password at the same time (seed script re-run) —
   treat a lost second factor as a potential compromise.
3. Sign in (you are now an unenrolled, constrained operator) and re-enroll
   per the steps above. Record the reset in the operations log.

> Known limitation (ADR-0014, follow-up issue): deleting the enrollment
> does **not** revoke already-issued operator tokens — a live full token
> keeps read access to `/platform` until expiry, though every destructive
> action fails closed. The password rotation bounds this to the access
> token TTL.

### Configuration knobs

- `MFA_ENCRYPTION_KEY` — dedicated Fernet key (urlsafe-base64, 32 bytes)
  for TOTP-seed encryption at rest; **set this in production** (unset =
  derived from `JWT_SECRET_KEY`, a disclosed ADR-0014 trade-off).
- `MFA_TOTP_DRIFT_STEPS` (default 1), `MFA_MAX_ATTEMPTS` (default 5),
  `MFA_ATTEMPT_WINDOW_SECONDS` (default 300).
- `PLATFORM_IP_ALLOWLIST` — optional comma-separated CIDR allowlist for
  the whole `/platform` surface (empty = disabled; malformed config
  refuses to boot; unparseable client addresses are denied — fail closed).

## Credential rotation

Rotate the operator password **every 90 days**, and immediately when a
person with knowledge of the credential leaves the operating team or a
leak is suspected. Rotation is just a re-run with the same email and a new
password:

```shell
PLATFORM_OPERATOR_EMAIL='ops@your-platform.example' \
PLATFORM_OPERATOR_PASSWORD='<new strong password>' \
    python -m app.scripts.create_platform_operator
```

Existing sessions are bounded by the JWT lifetimes (access ≤ 24h, refresh
≤ 30 days as configured); rotation does not revoke tokens already issued.

## Break-glass: the sole operator credential is lost

Losing the operator password (or the operator account being deactivated)
does **not** take the platform down — modules keep their current
entitlement state — but blocks entitlement changes. Recovery requires only
direct database access, no support backdoor:

1. Obtain a shell in the API environment (container/host) with the
   production `DATABASE_URL`.
2. Re-run the seed script with the **same operator email** and a new
   password. The run is an update: same row, new bcrypt hash,
   `is_active` restored. The TOTP enrollment is untouched — the existing
   authenticator keeps working.
3. Sign in through the normal login + OTP + TOTP flow to confirm
   recovery, then record the rotation in the operations log. (Second
   factor also lost? See "MFA break-glass" above.)

If the operator email inbox itself is lost (OTP mail unreceivable), seed a
**new** operator with a reachable email address, then deactivate the old
account (`UPDATE users SET is_active = false WHERE email = '<old>'`).

## What the operator account can and cannot do

- Can: list owner profiles (`GET /platform/owners`), read per-owner
  entitlements (`GET /platform/owners/{owner_id}/modules`), grant/revoke
  toggleable modules (audited `PATCH`). Essential modules (auth, owner,
  health, dashboard) can never be disabled (422).
- Cannot: access any tenant business data — tenant role gates reject the
  operator (403). The role is not in `PRIVILEGED_ROLES` and never will be.

Out of scope (separate ADRs per ADR-0011): operator MFA/step-up auth,
tenant impersonation, tenancy plurality, entitlement-change notifications.
