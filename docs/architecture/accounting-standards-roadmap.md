# Accounting Standards & Resilience Roadmap

This document is the improvement report for bringing priori-crm to very high
adherence to accounting systems and standards, ordered: **sales modules,
purchase modules, reports, dashboard**, followed by **resilience under tight
hardware/software constraints** and a **penetration-test plan**.

Every workstream is tracked as an issue; the table at the end maps each item
to its issue and status.

## 1. Sales modules (invoices, quotes, customers, receipts)

The strongest area today: draft-only editing, optimistic locking (`version` +
conflict tests), state machines with no illegal edges, reference generators
that never reuse numbers after deletion, and document-level VAT with DB CHECK
constraints. To reach high standards adherence:

- **Credit notes instead of cancellation** (#9): a *sent* invoice should never
  be mutated or merely "canceled"; issue a credit note that reverses it. This
  preserves the audit trail required by IFRS/GAAP and most tax authorities.
  Today `cancel` exists but there is no reversing document.
- **Persist `vatComplianceRef` for invoices/quotes** (implemented, this MR):
  the editor sends it but the server ignored it. A tax-registered seller must
  print its PIN/VAT number on the invoice as issued. Explicit value wins;
  when omitted and VAT is enabled it defaults from the owner tax PIN,
  mirroring the PO-27 contract.
- **VAT rounding policy** (#12): VAT is currently computed once on the
  discounted subtotal. Some jurisdictions mandate per-line VAT rounding. Make
  the rounding basis (`line` vs `document`) an owner setting and record which
  was used on each document.
- **Owner `vat_rate` precision** (implemented, this MR): widened
  `Numeric(5,2)` to `Numeric(5,4)` so defaults like 16.5% are representable.
- **Payment application and unapplied cash** (#9): overpayments currently
  drive balances negative. Introduce customer credits/unapplied receipts that
  can be applied to future invoices, so receivables never show negative
  liabilities in disguise.
- **Period locking** (#8): prevent creating/editing documents dated inside a
  closed accounting period. This is the single biggest control gap for
  backdating.

## 2. Purchase modules (POs, expenses, vendors, payments)

- **Three-way matching** (#10): today a PO goes draft > sent > paid directly.
  Insert a *goods receipt* and a *vendor bill* step so payment requires
  PO = receipt = bill match. POs correctly do not hit vendor statements
  (tested), but the liability should be recognised at bill time, not payment
  time (accrual basis).
- **FX gains/losses** (#10): PO payments already carry currency/fx-rate with
  resync logic. Add explicit realised FX gain/loss recognition (IAS 21) when
  payment currency differs from document currency, rather than silently
  absorbing the difference in balances.
- **Approval workflow / segregation of duties** (#11): `require_privileged()`
  gates destructive and financial routes, but the same privileged user can
  create a vendor, create a PO, and pay it. Add a two-person rule: creator
  cannot approve/pay their own document above a configurable threshold.
- **Vendor master controls** (#11): require payment-detail changes to be
  re-approved, and audit them via the existing audit-event infrastructure.

## 3. Reports / statements

- **The structural gap: a true double-entry general ledger** (#7). All
  statements (overview, income statement, cashflow, customer/vendor
  statements) are derived directly from documents. This works but cannot
  produce a trial balance or balance sheet, and cannot *prove*
  Assets = Liabilities + Equity. Post immutable journal entries on every
  document event (issue, payment, cancel/credit) into a
  `journal_entries`/`journal_lines` pair with a chart of accounts; derive
  every report from the ledger. Existing parity tests (statement
  opening-balance parity, draft/canceled exclusion) become ledger invariants
  enforced by a single CHECK: every entry balances to zero.
- **Period close** (#8): closing process with retained-earnings roll-forward
  and locked periods; reports for a closed period must be byte-stable
  forever.
- **Tamper-evident audit trail** (#15): `audit_events` is append-only; add
  hash-chaining (each row includes the previous row's hash) so deletion or
  mutation is detectable - a common certification requirement.
- **Cash vs accrual toggle** (#7): the income statement is document-dated
  (accrual-ish) while cashflow is payment-dated. A ledger makes both bases
  first-class and reconcilable.
- **Aging reports** (#13): AR/AP aging (30/60/90+) directly from the same
  query objects as statements so totals tie out.

## 4. Dashboard

- **Tie-out guarantee** (#13): dashboard KPIs must be computed by the *same*
  query objects as the statements module, with parity tests asserting
  dashboard totals equal statement totals for the same period. Nothing erodes
  trust in an accounting system faster than a dashboard that disagrees with
  the reports.
- **Fiduciary KPIs** (#13): DSO/DPO, AR/AP aging buckets, current VAT
  liability (output minus input VAT for the open period), and SLO/error-budget
  status.
- **Server-side aggregation cache** (#13): cache aggregates per period with
  short TTL + ETag so repeat loads cost one 304.

## 5. Versatility under tight hardware/software constraints

Already in place and worth preserving: export concurrency caps
(`EXPORT_MAX_CONCURRENCY`), exports off the event loop, fail-open rate
limiting with LRU-bounded memory stores, `has_next` pagination that avoids
COUNT, constant-query-count guarantees (N+1 tests), route-level code
splitting + vendor chunks + immutable asset caching for 3G, and the email
outbox absorbing SES outages. To harden further:

- **Database**: `statement_timeout` per session so one runaway report cannot
  starve the pool (implemented, this MR: `DB_STATEMENT_TIMEOUT_MS`, default
  disabled); keyset (cursor) pagination for large lists (#14);
  partition/archive `audit_events` as it grows (#14); pool sizes remain
  env-tunable for small hosts.
- **Exports** (#14): `openpyxl` write-only mode and streamed rows for large
  statements so memory stays flat; cap statement row counts with the existing
  `X-Truncated` convention.
- **API** (#14): gzip compression; ETag/If-None-Match on list and dashboard
  endpoints; OTel metrics stay off by default so telemetry costs nothing on
  constrained hosts.
- **Frontend**: retry-with-backoff on idempotent GETs; stale-while-revalidate
  for the dashboard so a slow API never blanks the screen.
- **Degradation ladder** (#14): document in `docs/operations/slos.md` what
  switches off first under pressure - synthetics catch it, rate limiter sheds
  load, exports queue, email defers to the outbox - so behaviour under stress
  is designed, not accidental.

## 6. Targeted penetration-test plan (#15)

For the Security Analyst Agent or a manual tester, once DAST is wired to a
review/staging deployment:

1. **IDOR sweep**: enumerate `customer_id`/`vendor_id`/`invoice_id` UUIDs
   across all `/{id}/...` routes with a valid low-privilege token - the model
   is single-org today; document that assumption before multi-tenancy lands.
2. **AuthZ matrix**: replicate `test_po_security_po16` (every route requires
   auth; destructive/financial routes require privileged) for customers,
   invoices, quotes, expenses, statements, dashboard - POs are the only
   module with that test today.
3. **Internal endpoints**: verify `/internal/email-outbox/*` and transition
   endpoints reject missing/wrong `X-Internal-Secret`, and confirm the secret
   is set in production (fails closed if not).
4. **Upload paths**: fuzz logo/document uploads (polyglot files, oversize,
   traversal keys) - strong unit coverage exists (`test_storage_security`);
   confirm it holds end-to-end over HTTP.
5. **Auth lifecycle**: token replay after logout, refresh-token reuse (family
   revocation is tested), OTP brute-force against `AUTH_MAX_OTP_ATTEMPTS`.
6. **Rate limiting and headers**: confirm 429 behaviour, HSTS in production,
   explicit CORS lists, and that `/health` never leaks internals.

## Tracking

| # | Item | Issue | Status |
|---|------|-------|--------|
| 1 | Persist `vatComplianceRef` on invoices/quotes | - | Implemented (this MR) |
| 2 | Owner `vat_rate` precision Numeric(5,4) | - | Implemented (this MR) |
| 3 | Postgres statement timeout guard | - | Implemented (this MR) |
| 4 | Edit pages preserve persisted VAT state | - | Implemented (this MR) |
| 5 | Double-entry general ledger, trial balance, balance sheet | #7 | Open |
| 6 | Period locking + period close | #8 | Open |
| 7 | Credit notes + customer credits (unapplied cash) | #9 | Open |
| 8 | Vendor bills, goods receipts, three-way match, FX gain/loss | #10 | Open |
| 9 | Approval workflow / segregation of duties | #11 | Open |
| 10 | VAT rounding policy (line vs document) | #12 | Open |
| 11 | Aging, dashboard tie-out, server-side caching | #13 | Open |
| 12 | Keyset pagination, streamed Excel, compression, audit archival | #14 | Open |
| 13 | AuthZ matrix tests, hash-chained audit, DAST, pen test | #15 | Open |
