# ADR-0010: Billing-profile "sync" is an internal posting/readiness flag

- **Status:** Accepted
- **Date:** 2026-08-09
- **Deciders:** Backend team (open question raised to PM)
- **Related:** Issue #43, `deal-desk-prototype.html`, ADR-0002 (document domain)

## Context
The Sales Desk prototype registers each company once with two billing
profiles (USD + KES) and shows a per-profile "synced / not synced" pill,
originally conceived as the state of a push from a standalone Sales Desk
into the accounting system. The Sales Desk has since moved **inside
Business Central**, so there is no longer an external system boundary to
push across — yet the workflow still needs to distinguish "profile settings
finalized and posted/ready for billing" from "profile edited and awaiting
review/posting".

We had to decide what `synced` means now, before the deals module, quotes
integration and Companies UI start building on it.

## Decision
We keep the `synced`/`synced_at` pair on `customer_billing_profiles` but
define it as an **internal posting/readiness flag**, not an integration
status: it records that a human (or automation) has confirmed the profile's
payment terms, tax treatment and credit limit as posted to accounting.
There is no outbound API call behind the sync endpoints; they only flip the
flag, stamp the time and write an audit event. This interpretation was
raised to the PM as an open question; renaming the field was deferred to
avoid diverging from the prototype vocabulary the sales team already uses.

## What it does today
- `api/app/modules/customers/models.py` — `CustomerBillingProfile.synced`
  (bool, default false) and `synced_at` (timestamptz, nullable).
- `api/app/modules/customers/service.py` — any accepted profile edit sets
  `synced = false` server-side under a row lock and version check;
  `sync_profile` / `sync_all_profiles` set `synced = true` and stamp
  `synced_at` (idempotent re-sync refreshes the stamp). Every mutation is
  audited via `app/common/audit.py` in the same transaction.
- `api/app/modules/customers/router.py` —
  `PATCH /customers/{id}/profiles/{currency}`,
  `POST /customers/{id}/profiles/{currency}/sync`,
  `POST /customers/{id}/profiles/sync-all`, and the
  `GET /customers?unsynced=true` hygiene filter.

## Business logic & rules
- A profile is born unsynced (both at customer creation and via backfill).
- **Any** edit to a profile's billable settings flips it back to unsynced —
  server-side, race-safe (row lock + optimistic-lock version), never left
  to the client.
- Sync is a deliberate, audited act; `synced_at` is evidence of *when* the
  posted state was last confirmed, and survives later edits (the flag, not
  the timestamp, is the source of truth for readiness).
- The `unsynced=true` customer list powers hygiene views and notifications.

## Consequences
- Positive: the deal-desk workflow (edit → review → push) survives the move
  into Business Central unchanged; downstream modules can gate billing on
  `synced` without caring what "sync" physically does.
- Negative: the name `synced` now under-describes its meaning; if a real
  outbound integration ever returns, a second, genuinely external status
  must not be conflated with this flag.

## Improvements
1. Revisit the field name (`posted`/`ready_for_billing`) with PM once the
   Companies UI vocabulary settles.
2. If an outbound Business Central API push is ever added, model it as a
   separate outbox-driven process (ADR-0005 pattern) rather than
   overloading this flag.

## Resilience & <1s response rules
- Sync endpoints are single-row updates behind a row lock; no network I/O.
- The unsynced filter is an indexed `EXISTS` probe
  (`ix_customer_billing_profiles_synced` + customer FK index), safe on the
  list hot path.
