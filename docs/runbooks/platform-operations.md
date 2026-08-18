# Runbook: platform operations (tenant lifecycle, entitlements, monitoring)

Audience: the platform operator / on-call engineer. Companions:
`docs/runbooks/platform-operator.md` (account provisioning/rotation/break-glass),
ADR-0011 (authority model), ADR-0013 (tenancy strategy),
`docs/operations/tenancy-readiness.md` (subsystem audit),
`docs/operations/email-outbox-dlq.md` (DLQ tooling).

The operator's remit is **platform administration only**: entitlements,
tenant lifecycle, platform monitoring. The role carries no tenant-data
access — an operator token is rejected (403) centrally at authentication
(`get_current_user`, `api/app/common/dependencies.py`) on every route
outside `/platform`, `/auth` and `/health`, in addition to every tenant
role gate, and that isolation is CI-enforced route-table-wide
(`api/tests/test_platform_isolation_contract.py`).

## Duties and cadence

| Duty | Cadence |
|------|---------|
| Review platform audit trail (`GET /platform/audit` / console audit section) | Weekly, and after any incident |
| Outbox dead-letter review (`GET /internal/email-outbox/dead`) | Weekly (with the existing DLQ runbook) |
| Error-rate / SLO review (observability docs) | Weekly |
| Storage growth review (uploads volume + DB size) | Monthly |
| Operator credential rotation (`platform-operator.md`) | Every 90 days |
| Quarterly access review of operator accounts (below) | Quarterly |
| Restore drill (per !74 backup/DR runbooks when merged) | Per that runbook's cadence |

## Tenant lifecycle

### Provision

1. An owner profile is created by the application on first use
   (`OwnerService.get_or_create`); the platform API **never** creates one
   implicitly (writes against unknown ids 404 — ADR-0011).
2. Verify the owner appears in `GET /platform/owners` (console: owner
   list) with status `active`.
3. Set the commercial entitlement baseline: revoke any modules not
   purchased (audited PATCH per module; tenant admins are notified by
   email automatically via the outbox).
4. Record the tenant, plan and entitlement baseline in the operations log.

> Until ADR-0013 Phase T1 ships, one deployment holds exactly one tenant.
> Onboarding a second organisation today means a second deployment
> (ADR-0013's documented bridge tactic), including its own operator seed,
> backups and monitoring.

### Suspend / reactivate

Console: owner → "Tenant lifecycle" → Suspend/Reactivate (confirm dialog),
or `PATCH /platform/owners/{owner_id}/status` with
`{"status": "suspended" | "active"}`.

Semantics (enforced in code, this MR):

- Suspension is **reversible and non-destructive** — no data is touched,
  and nothing is revoked: reactivation restores existing sessions and any
  un-burnt OTP / un-spent refresh token.
- **Suspension takes effect immediately on every authenticated route**
  (`get_current_user`, `api/app/common/dependencies.py`): live access
  tokens are denied with a clear 403 at once — they do not ride out
  their TTL. Only the unauthenticated health probe keeps answering.
- Every non-essential module is additionally denied at the module gate
  (`require_module`) — load-bearing for the JWT-less internal scheduler
  endpoints.
- **Non-operator sign-in and refresh are rejected** with a clear 403:
  login step 1 (after the credential check — enumeration-safe),
  verify-otp and refresh all gate
  (`api/app/modules/auth/service.py::_ensure_owner_not_suspended`). The
  operator remains able to sign in.
- Never touches `users.role` — suspension is org state, not a role
  mutation (QA finding 09 intact; the status endpoint cannot create or
  promote operators).
- Both directions are audited (`owner_suspended` / `owner_reactivated`)
  with actor and before/after state.
- Scheduled document transitions during suspension — **verified in code
  (this MR)**: the overdue/expiry transitions
  (`InvoiceService.bulk_transition_overdue`,
  `ExpenseService.bulk_transition_overdue`,
  `QuoteService.bulk_transition_expired`) are pure bulk status UPDATEs
  and **enqueue no tenant email**. Their internal-secret endpoints live
  on the non-essential invoices/expenses/quotes routers, so while the
  deployment's (only) owner is suspended the module gate denies them
  with the same 403 — the scheduler run is a no-op for that window and
  its pipeline will report the 403; treat that as expected during a
  suspension, not an incident. The outbox drain and OTP purge live on
  ungated infrastructure routes and keep running. Per-tenant scheduler
  skip at N>1 lands with ADR-0013 Phase T5
  (pinned by `api/tests/test_owner_suspension.py`).

Use suspension for: non-payment, suspected compromise of the tenant's
accounts, or as the first step of offboarding.

### Offboard: export → retention hold → deletion

Offboarding is a three-stage, logged procedure. Stages must not be
collapsed: deletion without the hold breaks the ledger's evidentiary
value, and export after deletion is impossible.

1. **Suspend** the tenant (above) so no new data is written.
2. **Export** the tenant's data and hand it over through an agreed secure
   channel:
   - Today (single tenant per deployment): a full logical dump
     (`pg_dump`) plus the uploads directory/bucket **is** the tenant
     export.
   - After ADR-0013 T2–T5: the tenant-scoped export tool (rows
     `WHERE owner_profile_id = X` + `tenants/<owner_id>/…` storage
     prefix).
3. **Retention hold** — default **90 days** suspended-but-retained:
   - Gives the tenant a recovery window and covers dispute/chargeback
     tails.
   - Statutory records may have to outlive the hold: Kenyan tax law
     requires business/VAT records to be kept for at least 5 years —
     agree with the tenant which party carries that obligation after
     hand-over, and record it.
4. **Deletion** after the hold expires and sign-off is recorded:
   - Today: destroy the deployment's database and storage (it serves only
     this tenant) after verifying the export hand-over receipt.
   - After T2–T5: delete tenant-keyed rows and the storage prefix;
     `audit_events` of the deletion itself are platform records and are
     kept.

**Data-protection notes (Kenya DPA 2019 / GDPR):**

- The tenant is the data controller for its customers' personal data; the
  platform is a processor. Offboarding export = the controller retrieving
  its data; process it only on the tenant's documented instruction.
- Kenya DPA 2019: honour data-subject rights routed via the tenant
  (access, correction, erasure — ss. 26, 40); do not retain personal data
  beyond the documented hold without a lawful basis; deletion at the end
  of the hold implements storage limitation.
- GDPR (where EU data subjects are involved): Art. 28 processor duties —
  return-or-delete at end of provision is exactly stages 2–4; keep the
  deletion record.
- Breach duty: notify the affected tenant(s) without undue delay (DPA
  2019 s. 43: 72 hours to the Commissioner where applicable; GDPR
  Art. 33 mirrors this). The platform audit trail is your evidence of
  what the operator *changed* and when — it is append-only at the
  database (a `BEFORE UPDATE OR DELETE` trigger raises), but it records
  **successful writes only**: operator sign-ins, reads (audit/owner
  listings) and failed/denied attempts are not in the trail. Correlate
  with API logs where those matter.

## Entitlement management

- All grants/revocations go through the console or
  `PATCH /platform/owners/{owner_id}/modules/{module_key}` — audited,
  essential modules refuse with 422 (ADR-0011).
- Tenant admins are notified automatically (outbox email naming module,
  new state, actor **role** and timestamp — never the operator's email).
- Treat entitlements as commercial state: change them only against a
  recorded plan change or incident, and say which in the ops log.
- After any entitlement change, verify in the console that the resolved
  state matches intent (the console refetches pessimistically).

## Monitoring obligations

| Obligation | How (today) | Notes |
|------------|-------------|-------|
| Error rates | Existing observability/SLO docs (`docs/operations/observability.md`, `slos.md`), Sentry | Per-tenant split arrives with T5 metrics |
| Outbox dead letters | `GET /internal/email-outbox/dead` weekly; requeue only after root-cause fix (`email-outbox-dlq.md`) | Dead letters are per-recipient today, not per-tenant (readiness audit #7) |
| **Shared SES reputation** | Watch SES bounce/complaint metrics in the AWS console | All tenants send from one `SES_SENDER_EMAIL` (`api/app/lib/config.py:45`): one tenant's bad recipient lists degrade deliverability for everyone. A spike is a platform incident: identify the source recipients via the outbox table, suspend the offending flow, then remediate |
| Storage growth | Disk/bucket usage monthly | Per-tenant attribution requires T4 key prefixes |
| Operator-action review | `GET /platform/audit` weekly: every event should map to a logged commercial/incident reason | Unexplained events are treated as potential compromise → rotate credentials (platform-operator.md break-glass) |
| Scheduler health | Existing GitLab scheduled-pipeline alerts (ADR-0005 scheduling notes) | Per-tenant job isolation lands with T5 |

## Quarterly access review of operator accounts

Once per quarter, with a second person where possible:

1. List operator accounts:
   `SELECT id, email, is_active, updated_at FROM users WHERE role = 'platform_operator';`
2. Confirm each maps to a current, named member of the operating team;
   deactivate leavers (`UPDATE users SET is_active = false …`) — never
   delete (audit attribution).
3. Confirm the last credential rotation is within 90 days
   (platform-operator.md); rotate if not.
4. Re-verify no API-reachable operator creation/promotion exists: the
   isolation contract suite and QA finding 09 are the controls — check
   they are green in CI, and that no route outside `/platform` names
   PLATFORM_OPERATOR (the suite's static leg asserts this).
5. Record the review (date, reviewers, findings) in the operations log.

## Controls: enforced today vs planned

Honest status of every control this runbook relies on:

| Control | Enforced today (code/CI) | Planned |
|---------|--------------------------|---------|
| Operator-only /platform surface | ✅ `require_role(PLATFORM_OPERATOR)` on every route; CI route-table contract (this MR) | — |
| Operator excluded from tenant data | ✅ central 403 at authentication on every non-platform/auth/health route, plus tenant role gates; CI contract incl. static + full-surface legs (this MR) | — |
| No API-reachable operator creation/promotion (QA 09) | ✅ seed script only (`platform-operator.md`); status endpoint never touches roles | — |
| Entitlement writes audited (before/after) | ✅ `record_audit_event` in owner service | — |
| Tenant lifecycle audited + reversible | ✅ this MR | — |
| Suspension: immediate denial on every authenticated route + module denial + login/refresh rejection | ✅ this MR | Per-tenant at N>1 needs owner resolution (T1, #75) |
| Entitlement-change notifications to tenant admins | ✅ this MR (outbox, same-transaction) | Org-membership recipient scoping (T1) |
| Operator audit read surface | ✅ this MR (`GET /platform/audit` + console view) | Real `owner_profile_id` column filter (T5, #78) |
| Audit trail append-only (tamper resistance) | ✅ DB trigger raises on UPDATE/DELETE (this MR); records **successful writes only** — no sign-ins, reads or denied attempts | REVOKE via role split (#80); sign-in/read auditing unplanned |
| Suspension pauses scheduled transitions for that tenant | ❌ not enforced (schedulers are tenant-global) | T5 |
| Per-tenant error/metric split | ❌ | T5 |
| Per-tenant outbox/DLQ attribution | ❌ | T5 |
| Per-tenant storage attribution/quota | ❌ | T4/T5 |
| Per-tenant rate limits / quotas | ❌ (user/IP buckets only) | T5 |
| Tenant-scoped export/offboarding tool | ❌ (whole-DB dump stands in while single-tenant) | T5 |
| Row-Level Security backstop | ❌ (no tenant keys yet; role-creation SQL documented, `docs/operations/sql/create-db-roles.sql`) | Role split at T1, per-wave keys + FORCE RLS at T2–T4, policies at T6 (#80) |
| Operator MFA / step-up auth | ❌ (login+OTP only, like all users) | Separate ADR — follow-up issue #73 |
| Quarterly access review | ❌ procedural only (this runbook) | Consider automation after first review |
