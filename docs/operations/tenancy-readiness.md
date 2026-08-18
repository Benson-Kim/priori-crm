# Tenancy-readiness audit

Subsystem-by-subsystem inventory of what breaks the moment `owner_profiles`
holds more than one row (N>1), with code citations verified on this branch.
Companion to ADR-0013 (the tenancy decision and phased blueprint); every
"Required change" points at its blueprint phase.

Severity scale — **Critical**: cross-tenant data exposure or corruption;
**High**: wrong-tenant behaviour or broken core flow; **Medium**: degraded
operations/quality; **Low**: cosmetic or informational.

| # | Subsystem | Current state (citation) | What breaks at N>1 | Severity | Required change | ADR-0013 phase |
|---|-----------|--------------------------|--------------------|----------|-----------------|----------------|
| 1 | Schema / business tables | No business table carries a tenant key: `customers`, `invoices`, `quotes`, `deals`, `vendors`, `expenses`, `purchase_orders`, `prospects`, `onboardings` and children have no `owner_profile_id` (`api/app/modules/*/models.py`). Only `owner_module_settings` is owner-keyed (`api/app/modules/owner/models.py:230-247`) | Every list/detail/report query returns **all tenants' rows** — immediate cross-tenant exposure of the entire ledger | **Critical** | Add `owner_profile_id` FK per table: expand (nullable) → backfill from `SINGLETON_PROFILE_ID` → NOT NULL + composite indexes; then RLS policies | T2 (masters), T3 (documents), T4 (children), T6 (RLS) |
| 2 | Owner resolution | `SINGLETON_PROFILE_ID` hardcoded (`api/app/modules/owner/models.py:40`); `require_module` filters on it (`api/app/common/dependencies.py:132`, suspension check at `:116`); `OwnerService.get_or_create` manages only that row (`api/app/modules/owner/service.py`) | Every request resolves to owner #1 regardless of who is calling; tenants 2..N read/write tenant 1's settings, entitlements and suspension state | **Critical** | A single `resolve_owner_id()` dependency fed by the JWT org claim replaces every literal read | T1 |
| 3 | Auth / JWT | Access token payload is `{"sub", "exp", "type"}` only (`api/app/common/security.py:35`); `users` has no org membership (`api/app/modules/auth/models.py:20-59`); suspension gate reads the singleton (`api/app/modules/auth/service.py:421`) | No way to know which tenant a user belongs to; login/refresh gates and entitlements cannot be tenant-correct | **Critical** | `user_org_memberships` table + `org` claim minted at verify-otp (`create_access_token` already accepts `extra`, `security.py:31`) | T1 |
| 4 | Reference sequences | Global scope keys: `invoice_number`, `quote_number`, `po_number`, `expense_number` (+ date-scoped variants) with a single high-water mark per scope (`api/app/common/reference_sequence.py:28`, callers `api/app/modules/invoices/service.py:1205`, `quotes/service.py:1021`, `purchase_orders/service.py:157`, `expenses/service.py:101`, customer profile codes `customers/service.py:162`) | All tenants share one `INV-…` counter: numbering **collides** with per-tenant uniqueness expectations and **leaks volume** (tenant B infers tenant A's issuance from sequence gaps); tenants serialize on each other's advisory locks | **High** | Prefix scope keys (and advisory-lock keys) with the owner id: `"{owner_id}:invoice_number_…"`; migrate existing rows to the singleton's prefix so no number is reused | T3 |
| 5 | Uniqueness constraints | Reference numbers globally unique (`invoices.invoice_number` unique, `quotes.quote_number`, `purchase_orders.po_number`, `expenses.expense_number`; see `api/app/modules/*/models.py` and `docs/database.md` §1); vendor email checks global (`GET /vendors/check-email`) | Tenant B cannot create `INV-20260817-001` because tenant A already has it; email "already exists" leaks other tenants' contacts | **High** | Make uniques composite: `(owner_profile_id, number)`; email uniqueness per tenant | T3 (documents), T2 (masters) |
| 6 | Uploads / storage keys | Keys are module-scoped with no tenant segment: `expenses/<id>/<hex>_name` (`api/app/modules/expenses/router.py:545`), `purchase_orders/<po_id>/…` (`api/app/modules/purchase_orders/router.py:890`), `owner/logo/…` (`api/app/modules/owner/service.py:66`); key building via `sanitize_storage_key` (`api/app/lib/storage.py:31`) | Objects of all tenants interleave in one namespace: per-tenant usage, export and deletion (offboarding) require a full DB join; `owner/logo` collides outright (one logo directory for everyone) | **High** | Tenant-prefix all new keys (`tenants/<owner_id>/…`); resolution shim + lazy migration for existing keys; logo keyed per owner | T4 |
| 7 | Email outbox | `email_outbox` rows carry recipient/subject/document ref only — no tenant column (`api/app/common/email_outbox.py:52-100`); DLQ tooling lists globally (`list_dead`) | Cannot attribute queued/dead mail to a tenant: per-tenant DLQ triage, offboarding purge and "who is hurting our SES reputation" are impossible | **Medium** | Nullable `owner_profile_id` column (platform mail stays NULL); DLQ views/filters per tenant | T5 |
| 8 | Audit events | `audit_events` has entity/actor but no tenant column (`api/app/common/audit.py:35-77`) | Tenant attribution of business audit rows requires per-entity-type joins; offboarding export of "this tenant's trail" is unreliable | **Medium** | Nullable `owner_profile_id` on `audit_events`, backfilled; platform surface filters on the real column | T5 |
| 9 | Rate limiting / denylist | Buckets are `user:{sub}` or client IP (`api/app/common/middleware.py:205-226`); auth throttle keys hash the email (`api/app/modules/auth/service.py::_enforce_attempt_throttle`); refresh denylist is jti/user-fence keyed (`api/app/common/token_denylist.py`) | No per-tenant ceiling: one tenant's burst consumes the shared worker pool (noisy neighbor); per-tenant quotas/billing impossible. Denylist itself is user-scoped and survives N>1 correctly | **Medium** | Add org dimension to the limiter identity (`org:{org}:user:{sub}`) + per-tenant quota config | T5 |
| 10 | CORS / FRONTEND_BASE_URL / SES identity | Single global config: `CORS_ORIGINS`/regex (`api/app/lib/config.py:49-56`), `FRONTEND_BASE_URL` used in reset links (`config.py:70`, `auth/service.py::_send_password_reset_email`), one `SES_SENDER_EMAIL` (`config.py:45`) | Works for a shared-domain SaaS; breaks only if tenants get their own domains (wrong reset-link host, wrong sender). Shared SES identity means one tenant's bounce behaviour damages all tenants' deliverability | **Low** (shared domain) / **High** (per-tenant domains) | Keep platform-global for the shared-domain model (ADR-0013 decision); revisit per-tenant domains as a separate ADR; monitor SES reputation per runbook | T6 note; runbook |
| 11 | Exports | Report + Sales Desk export audits stamp `"deployment_boundary": "single_organization"` (`api/app/modules/reports/audit.py:114`, `api/app/modules/sales_desk/audit.py:57`); export queries are unscoped (see #1) | Stamp becomes a lie; exports mix tenants' financials — a compliance incident, not a bug | **Critical** (data), **Low** (stamp) | Scope export queries by owner (falls out of #1/#2); flip stamp to `multi_tenant` + owner id | T3/T6 |
| 12 | Scheduled jobs | Nightly transitions are whole-table UPDATEs with no tenant loop: `bulk_transition_overdue` (`api/app/modules/invoices/service.py:1152`, `expenses/service.py:1063`), quote expiry (`api/app/modules/quotes/router.py:515`), outbox drain (`api/app/modules/health/router.py:147`), OTP purge (`auth/router.py`) | Functionally they'd still transition correct rows (predicates are date/status-based), but: no per-tenant failure isolation (one tenant's poison row aborts everyone), no per-tenant skip for `suspended`, and job metrics are tenant-blind | **Medium** | Iterate active tenants inside the job with per-tenant try/except and per-tenant result metrics; skip suspended tenants | T5 |
| 13 | Backups / DR | MR !74 (separate branch, not this MR) builds whole-DB backup/monitoring; nothing tenant-granular exists | Offboarding one tenant ("give us our data, delete the rest") requires hand-written queries against an unkeyed schema — practically impossible pre-T2; restoring one tenant means restoring everyone to that point in time | **High** (for offboarding), depends on !74 | Tenant-scoped logical export tool (rows `WHERE owner_profile_id = X` + storage prefix) once keys exist; whole-DB PITR remains the disaster path | T5 |
| 14 | Module entitlements | Already owner-keyed: `owner_module_settings.owner_profile_id` (`api/app/modules/owner/models.py:230-247`); `/platform` API owner-id-scoped (ADR-0011) | Storage survives N>1 unchanged; only the resolution side (#2) is wrong | **Low** | None beyond #2 | — (done) |
| 15 | Tenant lifecycle | `owner_profiles.status` + audited PATCH + enforcement (this MR) | Enforcement reads the singleton — at N>1 suspension of owner #1 would lock **everyone** out and suspension of others would do nothing | **High** (at N>1) | Re-point the two enforcement reads at the resolved owner (falls out of #2; no schema change) | T1 |

## Verified unknowns (read from code for this audit)

- **Uploads key format**: relative, sanitized, module-scoped keys —
  `expenses/<expense_id>/<16-hex>_<basename>`,
  `purchase_orders/<po_id>/…`, owner logo under `owner/logo/`
  (`api/app/lib/storage.py` docstring + call sites above). No tenant
  segment anywhere.
- **Rate limiter key derivation**: JWT `sub` when a valid bearer token is
  presented (`user:{sub}`), else first `X-Forwarded-For` hop (only when
  `RATE_LIMIT_TRUST_FORWARDED_FOR=true`), else socket IP
  (`api/app/common/middleware.py:205-232`). No tenant dimension.
- **JWT claim contents**: access = `sub`, `exp`, `type` (+optional
  `extra`, unused today) — `api/app/common/security.py:35`; refresh adds
  `iat`, `jti`. No org/tenant claim.
- **Outbox tenant dimension**: none — columns are id/recipient/subject/
  body/document ref/status bookkeeping
  (`api/app/common/email_outbox.py:52-100`).
- **Audit tenant dimension**: none — actor_id/entity_type/entity_id/
  action/before/after (`api/app/common/audit.py:35-77`).
- **Entitlement audit entity_type**: `"owner_module_setting"` — written in
  `OwnerService.set_module_enabled_for_owner`
  (`api/app/modules/owner/service.py`), asserted in
  `api/tests/test_module_entitlements.py::TestAudit`.
