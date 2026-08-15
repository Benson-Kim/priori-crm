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
sender.

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
   `is_active` restored.
3. Sign in through the normal login + OTP flow to confirm recovery, then
   record the rotation in the operations log.

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
