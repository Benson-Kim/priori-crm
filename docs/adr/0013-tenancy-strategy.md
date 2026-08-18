# ADR-0013: Tenancy strategy — shared schema with tenant keys and RLS, delivered in phases

- **Status:** Proposed
- **Date:** 2026-08-17
- **Deciders:** Backend + frontend team, platform operations (issue #71)
- **Related:** ADR-0011 (platform operator & entitlements), ADR-0001 (modular monolith), ADR-0004 (authz), ADR-0005 (email outbox), ADR-0007 (reliability & backups, extended by MR !74), `docs/operations/tenancy-readiness.md`, `docs/runbooks/platform-operations.md`

## Context

The product is deliberately single-tenant today, and ADR-0011 explicitly
deferred tenancy *plurality* after fixing the authority model. The current
state, verified in code:

- `owner_profiles` is a singleton: a fixed primary key
  (`SINGLETON_PROFILE_ID`, `api/app/modules/owner/models.py:37`) and a
  `get_or_create()` that only ever manages that row
  (`api/app/modules/owner/service.py`). Only `owner_module_settings` is
  owner-keyed (`owner_profile_id` FK, `models.py` `OwnerModuleSetting`).
- **No business table carries a tenant key**: `customers`, `invoices`,
  `quotes`, `deals`, `vendors`, `expenses`, `purchase_orders`, `prospects`,
  `onboardings` and all their children have no `owner_profile_id`
  (`api/app/modules/*/models.py`).
- **Users have no org membership**: `users` holds only identity + role
  (`api/app/modules/auth/models.py:20-59`), and the JWT access token
  carries only `sub`/`exp`/`type` (`api/app/common/security.py:31-40`) —
  there is nothing to resolve a tenant from.
- `require_module` resolves entitlements against the hardcoded
  `SINGLETON_PROFILE_ID` (`api/app/common/dependencies.py`).
- `reference_sequences` scope keys are global — `invoice_number`,
  `quote_number`, `po_number`, `expense_number` (+ their date-scoped
  variants) with no tenant dimension
  (`api/app/common/reference_sequence.py`, callers in
  `api/app/modules/*/service.py`).
- Report and Sales Desk exports stamp
  `"deployment_boundary": "single_organization"`
  (`api/app/modules/reports/audit.py:114`,
  `api/app/modules/sales_desk/audit.py:57`).
- Cross-cutting tables have no tenant dimension: `audit_events`
  (`api/app/common/audit.py`), `email_outbox`
  (`api/app/common/email_outbox.py`), uploads under module-scoped keys
  (`expenses/<id>/…`, `purchase_orders/<id>/…`, `owner/logo/…` —
  `api/app/lib/storage.py`, `api/app/modules/expenses/router.py:545`,
  `api/app/modules/purchase_orders/router.py:890`).
- Rate limiting buckets by user id or client IP only
  (`api/app/common/middleware.py`, `_client_identity`).
- Scheduled jobs operate on whole tables with no tenant loop
  (`bulk_transition_overdue`, `api/app/modules/invoices/service.py:1152`).

The commercial target is **~10 tenants** on the current operational
footprint: a single droplet, GitHub Actions deploys, GitLab scheduled
pipelines for monitoring/drains, and the MR !74 backup/DR stack. We must
choose the tenancy architecture now so every intermediate change (this
MR's Phase A included) moves toward it instead of away from it.

### Options considered

**(A) Deployment-per-tenant.** One droplet/stack (API, Postgres, uploads,
scheduler, backup jobs) per tenant.

**(B) Shared application, `tenant_id` on every row, PostgreSQL
Row-Level Security as defense-in-depth.** App-level scoping is primary
(every query filtered by the resolved owner id); RLS policies keyed on a
per-request session GUC (set from the JWT org claim) are the backstop
that turns a forgotten filter into zero rows instead of a cross-tenant
leak.

**(C) Schema-per-tenant.** One Postgres schema per tenant, shared
application, `search_path`/connection routing per request, Alembic run
per schema.

### Decision matrix

| Criterion | (A) Deployment-per-tenant | (B) Shared + tenant_id + RLS | (C) Schema-per-tenant |
|---|---|---|---|
| Isolation strength | **Strongest** — physical; no shared process, DB or storage | Logical; two independent layers (app scoping + RLS) must both fail to leak | Strong-ish logical; schema boundary, but one DB/process |
| Blast radius of a bug/outage | One tenant | All tenants (one app, one DB) | All tenants (one app, one DB) |
| Migration cost from today's schema | **None** — today's code runs as-is per tenant | One expand/contract wave per table group (~9 master + ~8 document + ~10 child tables), backfill trivial (everything belongs to the singleton) | No column changes, but Alembic must run per schema; every deploy becomes N migrations with partial-failure states |
| Ops cost at 10 tenants | **~10×**: 10 droplets, 10 deploy targets, 10 cert/DNS/SES/CORS configs, and it **multiplies the entire MR !74 backup/monitoring stack per tenant** (10 backup schedules, 10 restore drills, 10 alert sets) — untenable for this team | ~1×: one stack, one backup, one monitoring set; marginal per-tenant cost is a row + entitlements | ~1.5×: one stack, but N-schema Alembic orchestration, N-schema backups/restore tests, connection routing |
| Ops cost at 50 tenants | Out of the question (50 stacks) | Still ~1× + per-tenant quotas | Alembic-per-schema and `search_path` management become the dominant failure source |
| Noisy neighbor | **None** | Real: shared DB pool, worker processes, rate limiter must gain a tenant dimension (Phase T5); acceptable at 10 tenants of this size | Same as B (shared compute), slightly better lock isolation |
| Per-tenant backup / restore / offboarding | **Trivial** — the stack *is* the tenant; restore = restore the droplet | Surgical: per-tenant logical export (`COPY … WHERE owner_profile_id = …`) built in Phase T5; whole-DB PITR stays tenant-global | Easy-ish: `pg_dump --schema`, but consistent cross-schema PITR is awkward |
| Billing / metering readiness | Per-stack cost is obvious but manual | **Best**: every row is tenant-keyed; usage queries are one GROUP BY | Possible but per-schema aggregation queries are clumsy |
| Fit for the team's actual maturity (single droplet, GH Actions deploys, GitLab scheduled monitoring, no dedicated ops) | Poor beyond 2–3 tenants: every runbook, monitor and DR drill multiplies | **Good**: keeps the one-stack operating model the team already runs | Poor: introduces the operational complexity of A *and* the shared blast radius of B |

## Decision

**We adopt option (B): a shared application with an `owner_profile_id`
tenant key on every business row and PostgreSQL Row-Level Security as
defense-in-depth behind app-level scoping**, delivered as the phased
blueprint below. Rationale in one line: at ~10 tenants, (A) multiplies
the team's entire operational surface (including the MR !74 backup/DR
stack) by tenant count, (C) buys weaker isolation than RLS for more
migration machinery, and (B) is the standard SaaS shape whose costs are
one-off engineering rather than recurring operations.

Two explicit riders:

1. **(A) remains the documented bridge tactic**: if a second tenant must
   be onboarded commercially before Phase T3 completes, we clone the
   deployment (today's code supports it unchanged) and fold that tenant
   back in after Phase T6. This is a stopgap, not the architecture.
2. **RLS is defense-in-depth, not the primary control.** Every service
   query is scoped by the resolved owner id (app layer); RLS policies
   (`USING (owner_profile_id =
   NULLIF(current_setting('app.current_org', true), '')::uuid)`)
   exist so a forgotten filter fails closed. Neither layer may be relied
   on alone. Two pitfalls are load-bearing and specified in Phase T6:
   RLS does **not** bind table owners unless `FORCE ROW LEVEL SECURITY`
   is set (and the runtime role owns the tables today), and the GUC must
   be `SET LOCAL` per transaction with explicit fail-closed NULL
   handling, or pooled connections and unset GUCs quietly disable the
   backstop.

### Phased migration blueprint

Each phase is independently shippable, expand/contract-safe per
`docs/operations/deployment.md` (§ "Write expand/contract migrations",
line 919), and leaves the deployment fully functional as a single tenant.

**Phase A — platform console hardening (this MR).**
Operator audit read surface, entitlement-change notifications, owner
listing hardening, tenant lifecycle status (`owner_profiles.status`),
and the CI-enforced platform/tenant isolation contract. All
single-tenant-safe.

**Phase T1 — identity & resolution seam (no schema change to business
tables).** Small/medium.
- Org membership: `user_org_memberships (user_id, owner_profile_id,
  role)` — replaces the implicit "every user belongs to the singleton".
  Backfill: one row per existing user pointing at `SINGLETON_PROFILE_ID`.
- **Single membership vs M:N — resolved: single membership enforced
  initially.** The table is shaped M:N for the future, but T1 ships with
  `UNIQUE(user_id)` on it, so every user belongs to exactly one org and
  a single JWT `org` claim is *the* enforced membership, not a lossy
  projection of several. Org switching (dropping the UNIQUE, a
  choose-org step at sign-in, re-minting the claim on switch) is
  deferred until a real multi-org user exists; nothing in the claim
  design has to change for it — only issuance.
- JWT org claim: `create_access_token` gains an `org` claim
  (`api/app/common/security.py:31` already accepts `extra`); issued at
  verify-otp from the user's (single) membership.
- **User identity stays global — email unique across the platform
  (decided).** Login is email+OTP on one shared domain, so the email
  alone must resolve to one user; per-tenant email uniqueness would
  require tenant-scoped sign-in (org picker or per-tenant domains) that
  this product does not have. The cross-tenant **email-existence leak**
  this creates is handled at the flows, not the constraint: the
  login/OTP/reset paths are already enumeration-safe (generic responses,
  `api/app/modules/auth/service.py`), and any future user-invite path
  MUST be too (identical response whether the address exists anywhere,
  delivery by email) — a duplicate-email error surfaced to tenant B
  about tenant A's user is the leak and is a release blocker. Revisit
  per-tenant identity only together with a per-tenant-domains ADR.
- A single `resolve_owner_id(request/user)` dependency replaces every
  literal `SINGLETON_PROFILE_ID` read (`require_module` in
  `api/app/common/dependencies.py`, auth suspension gate, owner service).
  Falls back to the singleton when no claim exists (backward-compatible
  tokens during rollout).

**Phase T2 — schema wave 1: masters.** Medium.
- Tables: `customers`, `customer_billing_profiles`, `vendors`, `deals`,
  `prospects` (nurture), `onboardings`, plus `users` via T1 membership.
- Per table: add **nullable** `owner_profile_id` FK → backfill
  `UPDATE … SET owner_profile_id = SINGLETON_PROFILE_ID` → add
  `NOT NULL` + composite indexes (`(owner_profile_id, <existing hot
  columns>)`) in a follow-up migration (expand → backfill → contract, one
  deploy apart).
- Uniqueness that must become composite here: customer/vendor email
  uniqueness (`vendors.check-email` semantics) becomes unique **per
  tenant**.

**Phase T3 — schema wave 2: documents + numbering.** Large; the riskiest
phase, sequenced behind T2 on purpose.
- Tables: `invoices`, `quotes`, `purchase_orders`, `expenses`.
- **`owner_profile_snapshots` become tenant-attributable (decided:
  per-tenant hash scope).** They cannot stay content-addressed-and-shared
  across tenants: a snapshot IS tenant PII (name, address, email, phone,
  tax PIN, logo key), so offboarding/deletion and the tenant export
  (T5, runbook stages 2–4) must be able to find and remove *this
  tenant's* rows, and RLS (T6) needs a tenant key on the row. Mechanism:
  add `owner_profile_id` (expand → backfill to the singleton → NOT NULL)
  and change the unique constraint from global `content_hash` to
  composite `(owner_profile_id, content_hash)`; `snapshot_current()`
  scopes its lookup by the owner. Dedup tradeoff, accepted: dedup
  narrows from global to per-tenant, so two tenants with byte-identical
  headers store two rows — in practice never, since the hashed fields
  are the tenant's own identity, which is exactly why sharing them was
  wrong. The rejected alternative (a snapshot↔owner mapping table
  keeping rows shared) preserves global dedup but makes deletion
  reference-counted and RLS join-dependent — policies cannot join
  (see T4) — for no real saving.
- Same expand → backfill → contract discipline.
- **Uniqueness goes composite**: `invoice_number`/`invoice_reference`,
  `quote_number`, `po_number`, `expense_number` unique become
  `(owner_profile_id, number)` — reference numbers are unique per tenant,
  not globally.
- **`reference_sequences` re-scoping**: scope keys gain an owner prefix —
  `"{owner_id}:invoice_number_INV-YYYYMMDD"` instead of
  `"invoice_number_INV-YYYYMMDD"` (`api/app/common/reference.py`
  `lock_key` call sites). Existing global rows are migrated to the
  singleton's prefix so no number is ever reused. Documented risk of NOT
  doing this: a single global `INV-` counter both **collides** with
  composite uniqueness intentions and **leaks cross-tenant volume**
  (tenant B can infer tenant A's invoice volume from the gaps in its own
  sequence). The advisory-lock key inherits the same prefix, so tenants
  never serialize on each other's numbering.

**Phase T4 — schema wave 3: line items, payments, attachments,
storage.** Medium.
- Children (`*_line_items`, `payments`, `purchase_order_payments`,
  `expense_documents`, `purchase_order_documents`, deal activities,
  onboarding tasks) get a **denormalized** `owner_profile_id` (needed for
  RLS — policies cannot join) backfilled from their parents.
- **Storage keys gain a tenant prefix**: `tenants/<owner_id>/expenses/…`
  via `sanitize_storage_key` (`api/app/lib/storage.py:31`); existing
  singleton keys are lazily migrated or grandfathered behind a
  key-resolution shim. Per-tenant storage usage becomes a prefix listing.

**Phase T5 — cross-cutting services.** Medium.
- `audit_events` and `email_outbox` gain nullable `owner_profile_id`
  (platform-scope events stay NULL); the operator audit surface
  (`GET /platform/audit`, Phase A) starts filtering on the real column
  instead of resolving through `owner_module_settings`.
- **Scheduled jobs iterate tenants with per-tenant failure isolation**:
  `bulk_transition_overdue` (`api/app/modules/invoices/service.py:1152`),
  quote expiry and the outbox drain loop per active tenant inside
  try/except so one tenant's failure cannot starve the rest, and skip
  `suspended` tenants.
- **Per-tenant rate-limit keys**: `_client_identity`
  (`api/app/common/middleware.py`) gains the org claim →
  `org:{org}:user:{sub}`, enabling per-tenant quotas (noisy-neighbor
  control). Safe precondition **verified in code** (readiness audit #9):
  the limiter's identity derivation signature-verifies the JWT
  (`decode_access_token` — pinned HS256 against `JWT_SECRET_KEY`) before
  trusting any claim, and invalid tokens fall through to IP identity, so
  an attacker cannot forge an `org` claim to consume another tenant's
  quota.
- **Per-tenant backup/offboarding export**: a tenant-scoped logical
  export (all rows `WHERE owner_profile_id = X` + storage prefix) as an
  operator tool — required for offboarding (runbook) and surgical
  restore; complements, not replaces, the MR !74 whole-DB stack.

**Phase T6 — RLS + cutover.** Medium.
- **Enable AND force RLS** on every tenant-keyed table:
  `ALTER TABLE … ENABLE ROW LEVEL SECURITY` followed by
  `ALTER TABLE … FORCE ROW LEVEL SECURITY`. Plain `ENABLE` does **not**
  apply policies to the table owner, and today the single application
  role owns every table — without `FORCE`, the entire backstop is a
  no-op for exactly the connections that matter.
- **Migration-role / runtime-role split (decided):** introduce
  `app_migrator` (owns all tables and sequences; the only role Alembic
  runs as; used by deploys and never by request traffic) and
  `app_runtime` (the API's connection role: `NOSUPERUSER`, **no**
  `BYPASSRLS`, no ownership, table privileges granted explicitly).
  `FORCE ROW LEVEL SECURITY` stays on regardless, as belt-and-braces
  against ownership drift; only roles with `BYPASSRLS`/superuser (used
  exclusively for DBA maintenance) bypass policies, and the runtime role
  must never be one.
- **GUC semantics:** the request dependency sets the org from the JWT
  claim (T1) with `SET LOCAL app.current_org = '<owner uuid>'` inside
  the request's transaction. `SET LOCAL` reverts automatically at
  commit/rollback, so a pooled connection can never leak one request's
  org into the next; session-scoped `SET` is forbidden for this GUC
  (it would poison the pool). Any code path that runs multiple
  transactions per request (e.g. the report snapshot dependency) must
  re-issue `SET LOCAL` per transaction.
- **Fail closed on a missing org, at both layers:**
  - Policies read the GUC with `current_setting('app.current_org',
    true)` (returns NULL instead of erroring when unset) and compare via
    `NULLIF(…, '')::uuid` — a NULL/empty GUC therefore matches **zero
    rows** for `USING` (reads) and rejects every row via `WITH CHECK`
    (writes). An unset GUC can never mean "all rows".
  - The request dependency refuses to run tenant queries at all when no
    org is resolved (hard 401/403 before any SQL), so the zero-row
    policy behaviour is the backstop, not the interface: a missing org
    claim is an authentication bug to surface, not an empty list to
    return.
- Flip the export stamp: `deployment_boundary` becomes
  `"multi_tenant"` + the owner id (`api/app/modules/reports/audit.py:114`,
  `api/app/modules/sales_desk/audit.py:57`).
- CORS/`FRONTEND_BASE_URL`/SES sender stay platform-global for the
  shared-domain model (per-tenant subdomains are a separate, later
  decision).
- Onboard tenant #2 behind a checklist in
  `docs/runbooks/platform-operations.md`.

## What it does today

Phase A only (this MR): operator audit read surface, entitlement-change
notifications, owner listing hardening, `owner_profiles.status` with
audited PATCH and suspension enforcement, and the isolation contract
suite. Everything tenancy-plural (org claim, tenant keys, RLS) is
**not** implemented yet; the singleton resolution paths cited in Context
are unchanged.

## Business logic & rules

- Platform and tenant authority remain disjoint axes (ADR-0011);
  PLATFORM_OPERATOR stays out of `PRIVILEGED_ROLES` and is rejected by
  every tenant role gate — now CI-enforced route-table-wide.
- Tenant lifecycle: `active ⇄ suspended` only, operator-set, audited,
  reversible, never touching `users.role` (QA finding 09). Suspension
  denies non-essential modules and new non-operator tokens; essential
  modules (auth, owner, health, dashboard) keep serving existing
  sessions.
- App-level tenant scoping is the primary isolation control; RLS is the
  mandatory backstop from Phase T6 on. A query path with neither is a
  release blocker.
- Reference numbers are per-tenant identifiers from Phase T3 on; a
  sequence value is never reused within a scope (existing high-water-mark
  invariant is preserved per tenant scope).
- Every operator write is audited with actor, before and after state.

## Consequences

- Positive: one operational stack at any tenant count the team can
  realistically serve; per-tenant restore/offboarding becomes a query +
  prefix copy instead of a droplet clone; billing/metering is a GROUP BY;
  the ADR-0011 owner-id-scoped `/platform` API needs no changes.
- Negative: a long migration program (T1–T6) against a live financial
  ledger; shared blast radius until (and after) cutover; every future
  table must remember its tenant key + RLS policy (mitigated by tests
  and review checklists); noisy-neighbor control is our job now
  (quotas, per-tenant rate limits) instead of the hypervisor's.
- Accepted tradeoff: we give up (A)'s physical isolation; for this
  product tier the two-layer logical isolation is proportionate, and (A)
  remains the documented bridge for early commercial pressure.

## Improvements

Tracked as follow-up issues (see this MR's description):

1. Phase T1 — org membership model + JWT org claim + owner-resolution
   seam.
2. Phase T2 — masters partitioning wave.
3. Phase T3 — documents partitioning + per-tenant numbering
   (`reference_sequences` re-scoping).
4. Phase T4 — children/storage partitioning wave.
5. Phase T5 — cross-cutting: per-tenant quotas/metering, scheduled-job
   tenant iteration, tenant-granular export.
6. Phase T6 — RLS enablement + `deployment_boundary` flip.
7. Operator MFA/step-up auth ADR (out of ADR-0011 scope, needed before
   tenant count grows).

## Resilience & <1s response rules

- Owner resolution (T1) is one indexed PK/membership read per request,
  cacheable per request-scope; the `require_module` hot path stays two
  indexed reads (status + override) as measured today.
- RLS predicates are single-column equality on an indexed
  `owner_profile_id` GUC comparison — no joins in policies (hence the
  denormalized keys in Phase T4).
- Backfills run batched (`BATCH_SIZE`, `api/app/lib/config.py`) behind
  nullable columns, never locking a table for more than one batch;
  `NOT NULL` is applied only after backfill verification queries return
  zero.
- Scheduled jobs keep single-UPDATE semantics per tenant; the tenant loop
  bounds each tenant's failure to its own try/except (Phase T5).
- The entitlement/notification write path does no network I/O
  (outbox enqueue only), preserving ADR-0011's write-path rules.
