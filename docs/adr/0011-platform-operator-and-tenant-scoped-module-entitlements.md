# ADR-0011: Platform-operator role and operator-granted module entitlements

- **Status:** Accepted
- **Date:** 2026-08-14
- **Deciders:** Backend + frontend team (QA finding 09, Design/High, `sales-desk-go-no-go.html`)
- **Related:** Issue #58 (split from #51), ADR-0001 (modular monolith), ADR-0004 (authz)

## Context
Module entitlements were self-service, not granted by a platform operator:

- `UserRole` held only `ADMIN`/`MANAGER`/`MEMBER` — no platform-level role
  existed above the tenant's own administrator.
- `GET`/`PATCH /owner/modules` took no owner id; the service resolved the
  single owner profile (`owner_profiles` holds exactly one row, and the
  export audit stamps `"deployment_boundary": "single_organization"`).
- Both endpoints were gated on `require_role(UserRole.ADMIN)`, so the
  tenant's own admin could re-enable any module the platform had disabled —
  entitlements were a preference, not a grant.
- `settings/modules` was the only settings route; owner profile and logo
  editing existed only inside the document editor (`OwnerProfileModal`).

One piece already fit a multi-tenant future: `owner_module_settings` is
keyed by `owner_profile_id`, so entitlement **storage** survives a move to
real multi-tenancy unchanged.

We had to decide how to separate *platform-granted entitlements* from
*tenant-owned settings* before building any operator tooling or a real
owner Settings section on top of the wrong authority model.

## Decision
1. **We add a `PLATFORM_OPERATOR` role above `ADMIN`.** It represents the
   platform (deployment) operator, not a member of any tenant
   organisation. It is deliberately **not** added to `PRIVILEGED_ROLES`:
   operating the platform grants no implicit access to tenant business
   data (customers, invoices, financial reports). Platform authority and
   tenant authority are disjoint axes, checked explicitly.
2. **Module entitlements become operator-granted.** Write access moves to
   `PATCH /platform/owners/{owner_id}/modules/{module_key}`, gated on
   `PLATFORM_OPERATOR` only. The `/platform` surface is explicitly
   owner-id-scoped so it needs no changes when `owner_profiles` grows past
   one row. The tenant-facing `PATCH /owner/modules/{module_key}` is
   removed; `GET /owner/modules` remains (admin, read-only) to power the
   owner Settings screen.
3. **Owners get a real `/settings` section** (business details, branding
   and logo, document defaults) in the main shell; module entitlements
   render there **read-only** with a "granted by your platform operator"
   affordance instead of toggles.
4. **We stay a single-tenant deployment for now.** The singleton
   `owner_profiles` row, the `require_module` gate reading
   `SINGLETON_PROFILE_ID`, and the `deployment_boundary:
   single_organization` export stamp are unchanged. This ADR fixes the
   *authority model* (who may grant what) ahead of tenancy *plurality*
   (how many owner rows exist), which is a separate, later decision
   involving data partitioning of every business table.

## What it does today
- `api/app/constants/enums.py` — `UserRole.PLATFORM_OPERATOR`
  (`"platform_operator"`), documented above `ADMIN`; `PRIVILEGED_ROLES`
  unchanged (`ADMIN`/`MANAGER`).
- `api/alembic/versions/2026_08_14_1200-d1e2f3a4b5c6_*.py` — widens
  `ck_users_valid_role` to include `'platform_operator'`; downgrade demotes
  any operator rows to `admin` before restoring the old constraint.
- `api/app/modules/platform/router.py` — the operator surface:
  `GET /platform/owners` (list owner profiles),
  `GET /platform/owners/{owner_id}/modules`,
  `PATCH /platform/owners/{owner_id}/modules/{module_key}`; every route is
  gated on `require_role(UserRole.PLATFORM_OPERATOR)`; unknown owner ids
  are 404, never auto-created.
- `api/app/modules/owner/service.py` — owner-id-scoped entitlement reads
  and writes (`module_settings_for_owner` / `set_module_enabled_for_owner`);
  the audited upsert logic is shared with the singleton path.
- `api/app/modules/owner/router.py` — `GET /owner/modules` is read-only
  (admin); the tenant PATCH route is gone.
- `frontend/src/pages/settings/` — the owner Settings section
  (`/settings/organisation`, `/settings/documents`, `/settings/modules`)
  under `frontend/src/components/layout/settings-layout.tsx`; module
  entitlements render read-only.

## Business logic & rules
- Only a `PLATFORM_OPERATOR` may change module entitlements; a tenant
  admin can view them but cannot re-enable a module the operator disabled.
- A platform operator is rejected (403) by tenant-scoped role gates
  (`require_role(ADMIN)`, `require_privileged`) — no implicit data access.
- Entitlement semantics are unchanged: missing `owner_module_settings`
  row = enabled; essential modules (auth, owner, health, dashboard) can
  never be disabled (422); every change is audited with actor, before and
  after state.
- `/platform` routes carry no `require_module` gate: platform
  administration is infrastructure and must keep working even when every
  toggleable module is disabled.
- The operator API never creates owner profiles implicitly: writes against
  an unknown `owner_id` 404 instead of get-or-create, so entitlement rows
  can never precede their tenant.

## Consequences
- Positive: the authority model matches the product intent (entitlements
  are commercial grants); the operator API is already shaped for many
  owners, so real multi-tenancy later only changes owner resolution, not
  this surface; owners finally get a Settings home outside the document
  editor.
- Negative: a second authorization axis (platform vs tenant) must be kept
  explicit at every gate; single-tenant deployments now need one
  out-of-band `platform_operator` user (seed/ops task) to change
  entitlements; the frontend offers no operator console yet — grants are
  API-only.

## Improvements
1. A minimal operator console (list owners, toggle entitlements) once
   operator authentication/hosting is decided.
2. Real tenancy plurality: owner resolution from the authenticated user's
   organisation instead of `SINGLETON_PROFILE_ID`, per-tenant data
   partitioning, and flipping the export `deployment_boundary` stamp —
   each a separate ADR.
3. Entitlement change notifications to tenant admins (email outbox,
   ADR-0005 pattern).

## Resilience & <1s response rules
- Entitlement reads stay two indexed queries (profile PK + override rows
  by `owner_profile_id`); the `require_module` hot-path probe is
  unchanged.
- Entitlement writes are single-row upserts + one audit insert inside the
  request transaction (`CommitOnSuccessRoute`), no network I/O.
- The read-only Settings screen reuses the bootstrap `enabledModules` map
  and the existing `GET /owner/modules` — no new hot-path queries.
