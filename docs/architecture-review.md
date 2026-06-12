#### 1. Unimplemented Stubs

| ID | Location | What it should do | What it actually does |
|---|---|---|---|
| STUB-001 | `frontend/src/pages/products/index.tsx` (entire file) | Products catalog page ("Manage your catalog of products and services", routed at `/products` in `frontend/src/components/router.tsx`) | Fully hardcoded mock: renders a `CUSTOMERS_DATA` dummy array (an interface named `Customer` inside a Products page), fake tab counts (`160/100/60`), hardcoded `totalPages={9}`, and every action handler is `console.log`. No products backend module exists at all. |
| STUB-002 | `api/app/modules/invoices/service.py` → `send_invoice(attach_pdf: bool = True)`; `api/app/modules/quotes/service.py` → `send_quote(attach_pdf: bool = True)` | Attach the rendered PDF to the outgoing email | The parameter is accepted, defaulted to `True`, logged (`"attached_pdf": attach_pdf`) — and never used. No attachment is ever sent. Worse, the shared email body in `DocumentEmailMixin._generate_email_body` (`api/app/common/document_service.py`) tells customers *"Please find attached invoice {ref}…"* — a false statement in every customer email. |
| STUB-003 | `api/app/modules/vendors/service.py` → `search_contacts()`, `_verify_contact_exists()` | Search CRM contacts and validate `contact_id` on vendor creation | Both import `app.modules.contacts.models`, which **does not exist** in the repo. `search_contacts` silently returns an empty list forever; `_verify_contact_exists` silently no-ops, so `VendorCreate.contact_id` is persisted unvalidated. |
| STUB-004 | `api/app/modules/vendors/service.py` (multiple methods) | Bills module integration | ~60 lines of commented-out `Bill` queries (`_compute_payables_for_vendor`, `_compute_payables_bulk`, `get_vendor_transactions`, `_has_open_transactions`) plus `ImportError` fallbacks for modules that exist — dead placeholder code. |
| STUB-005 | `frontend/src/lib/features.ts` | Feature flags documenting intent | `expense.downloadPdf: false`, `vendor.exportExcel: false` — declared, unimplemented endpoints (at least these are honestly flagged). |
| STUB-006 | `frontend/src/lib/enums/index.ts` | Shared enums | Empty file (0 bytes). |
| STUB-007 | `api/check_db.py` | One-off connectivity probe | Ad-hoc script with a hardcoded connection string at the repo root; duplicates `check_database_connection()` in `api/app/common/database.py`. |

---

#### 2. Principle Violations & Risks

**Reliability**

- **ISSUE-001 (Critical)** — `QuoteService.send_quote` (`api/app/modules/quotes/service.py`): combines `joinedload(Quote.customer)` with `.with_for_update()`. `joinedload` emits a LEFT OUTER JOIN, and PostgreSQL rejects `FOR UPDATE` on the nullable side of an outer join. `InvoiceService._prepare_and_mark_sent` explicitly documents and avoids this with `options(lazyload("*"))` — the quote path was never given the same fix. **Impact:** sending a quote likely fails with a 500 on PostgreSQL. **Fix:** mirror the invoice pattern: lock the bare row with `lazyload("*")`, let `customer` load lazily.

- **ISSUE-002 (High)** — `send_quote` dispatches the synchronous SES email (with tenacity retries, up to ~20–30s) *while holding the row lock*, and inside the request transaction. `InvoiceService` deliberately splits this into `_prepare_and_mark_sent` (commit, release lock) + unlocked dispatch. **Impact:** a slow/failing SES call holds a write lock on the quote and a DB connection from the pool; under email-provider degradation this exhausts the pool. **Fix:** extract the invoice's two-phase pattern into `BaseDocumentService` (e.g. `_prepare_and_mark_sent_generic`) and reuse it for quotes — this is also a DRY fix.

- **ISSUE-003 (High)** — Synchronous email in the request path generally (`send_invoice`, `send_quote`, `AuthService._send_otp_email` via `api/app/lib/email.py`). Retries with exponential backoff block a worker thread for the duration. For invoices, `_prepare_and_mark_sent` commits SENT *before* dispatch, so an email failure leaves the invoice marked sent with no record that delivery failed and no retry. **Fix:** introduce a transactional outbox table (`email_outbox`) written in the same transaction as the status change, drained by a background worker/scheduler with retry + dead-letter; surface delivery state on the document.

- **ISSUE-004 (High)** — `QuoteService.convert_to_invoice` has no row lock and no version assertion. Two concurrent conversions of the same APPROVED quote both pass `can_convert_to_invoice`, both create invoices, and `related_invoice_id` is last-write-wins → duplicate invoices for one quote. **Fix:** load the quote `with_for_update()` (bare row, per ISSUE-001) before checking `can_convert_to_invoice`; optionally add a partial unique index enforcing one non-canceled invoice per source quote.

- **ISSUE-005 (Medium)** — `mark_as_sent` (invoices and quotes) transitions via plain `get_by_id` without `FOR UPDATE`, unlike `send_*`/`record_payment`. A concurrent send/payment can race the transition. **Fix:** route all transitions through one locked-load helper.

- **ISSUE-006 (Medium)** — Expense document upload (`attach_expense_document`, `api/app/modules/expenses/router.py`): storage write happens *before* the DB insert; if `attach_document` or the outer commit fails, the object is orphaned in storage with no cleanup. `OwnerService._schedule_object_cleanup` already solves exactly this with an `after_commit` hook. **Fix:** generalize the owner-service hook into a shared `storage_tx` utility and use it here (delete-new-key-on-rollback).

- **ISSUE-007 (Medium)** — The nightly jobs (`/internal/transition-overdue`, `/internal/purge-otps`, quote expiry) exist only as secret-protected endpoints; there is no scheduler definition anywhere in the repo (no cron, no CI schedule, nothing in `docker-compose.yml`). **Impact:** overdue/expired statuses and OTP-table growth silently depend on an undocumented external caller. **Fix:** add a scheduler (GitLab scheduled pipeline, k8s CronJob, or APScheduler sidecar) and document it; add a monitoring alert if a job hasn't run within its window.

**SOLID**

- **ISSUE-008 (Medium)** — Routers reach into service privates: `service._actor_id`, `service._current_user` (`expenses/router.py`, `invoices/router.py`). Encapsulation break; the role check in `attach_expense_document` even re-implements `require_privileged` inline against `service._current_user.role`. **Fix:** expose `actor_id` as a public property on `BaseDocumentService`; move the `payment_modal` privilege rule into the service or a dependency.

- **ISSUE-009 (Medium)** — `InvoiceService` / `QuoteService` / `ExpenseService` are God-ish classes (~900–1,000 lines each) mixing CRUD, state machine, statistics SQL, export loading, PDF orchestration, and email composition. `BaseDocumentService` extracted some mechanics, but stats/export/PDF concerns should split into `*StatisticsRepository` / `*ExportQuery` / a shared `DocumentPdfRenderer` (invoices' and quotes' `_render_pdf`/`generate_pdf` are near-identical).

- **ISSUE-010 (Low)** — `VendorService` re-implements its own `_transition` instead of using `StateMachineMixin`; `CustomerService` doesn't use the base at all despite an identical `__init__`. Inconsistent abstraction usage.

**DRY**

- **ISSUE-011 (Medium)** — Filter application is copy-pasted between `list_invoices` ↔ `list_for_export` (and again in quotes and expenses) — six near-identical blocks. A drift bug here means the Excel export silently disagrees with the list view. **Fix:** one `apply_filters(query, filters)` helper per module (or a shared generic).

- **ISSUE-012 (Medium)** — `calculate_totals` is duplicated almost verbatim in `InvoiceService` and `QuoteService`; the duplicate/create savepoint-retry loops are repeated 6× across the three document services. **Fix:** hoist a `_with_reference_retry(build_entity_fn)` template method into `ReferenceRetryMixin`.

- **ISSUE-013 (Low)** — `api/export_openapi.py` and `api/scripts/export_openapi.py` are duplicate scripts. `frontend/src/lib/api.ts` `apiDownload` duplicates `handleResponse`'s error parsing. Two CI systems are defined (`.github/workflows/*` and `.gitlab-ci.yml`) and will drift.

**Scalability**

- **ISSUE-014 (Medium)** — Excel exports cap at `settings.BATCH_SIZE` (default 1,000) rows and **silently truncate** (`list_for_export(limit=settings.BATCH_SIZE)` in all three export endpoints). A finance user exporting 5,000 invoices gets 1,000 with no warning. **Fix:** stream with `yield_per`/keyset pagination into the workbook writer, or at minimum add a truncation banner row + `X-Truncated` header.

- **ISSUE-015 (Medium)** — `ReferenceGenerator.generate` (global, non-date-scoped branch, `api/app/common/reference.py`): the `MAX(CAST(SUBSTRING(column, offset)))` query has **no filter** and scans the whole table while holding the advisory lock — creation throughput degrades linearly with table size, serialized. **Fix:** since `reference_sequences` already persists the high-water mark, drop the table-max scan entirely (or add a functional index and a `LIKE 'PREFIX-%'` filter).

- **ISSUE-016 (Low)** — Every list endpoint executes `query.count()` plus the page query; fine now, expensive at tens of millions of rows. Plan keyset pagination (`PaginatedResponse` already tolerates `total=None`). Four stacked `BaseHTTPMiddleware` layers also add measurable latency under load; consider pure-ASGI middleware.

**Maintainability**

- **ISSUE-017 (Low)** — Stale/contradictory docs: `mark-paid` endpoint description says "without creating a payment record" while the service explicitly creates one; `delete_expense` discards the service's soft-vs-hard boolean and always returns 204, hiding which happened from the client.
- **ISSUE-018 (Low)** — Repo hygiene: a real uploaded customer document is committed at `api/uploads/expenses/a77fa981-…/Quotation Benson Kimathi Gikungute.pdf` (also a **privacy/PII leak** — a named individual's quotation in version control); `check_db.py` with a hardcoded DSN; empty `frontend/src/lib/enums/index.ts`. Remove the PDF from history (not just HEAD), gitignore `api/uploads/`.

**Data Integrity**

- **ISSUE-019 (High)** — `Customer` has no `version` column / optimistic locking and `CustomerService.update` performs no `assert_version`, unlike vendors, invoices, quotes, and expenses. Concurrent edits to customer master data (including `currency`, which every invoice pins to) are silent last-write-wins. **Fix:** add `version` + `expectedVersion` query param, reuse `assert_version`. Also: nothing prevents changing a customer's `currency` after invoices exist — add a guard.

- **ISSUE-020 (High)** — `ExpenseService.create` accepts `data.currency` without checking it against the vendor's currency, while invoices/quotes strictly enforce single-currency-per-customer. Vendor payables (`_compute_payables_for_vendor`) and vendor statements then **sum mixed currencies into one number**. **Fix:** mirror the customer-currency pin/reject logic from `InvoiceService.create`.

- **ISSUE-021 (Medium)** — Statement opening-balance asymmetry: `CustomerService._calculate_balance_at_date` excludes DRAFT/CANCELED invoices from the debit side but counts **all** payments (including ones against canceled invoices) on the credit side; `VendorService._calculate_balance_at_date` doesn't filter canceled expenses at all. Opening balances can drift or go negative after cancellations. **Fix:** apply the same status predicate to both sides, in both services, sourced from one shared helper.

- **ISSUE-022 (Medium)** — No durable audit trail. Financial mutations (payments, cancellations, hard deletes — including `delete(force=True)` on customers) are recorded only in application logs. For a CRM/accounting platform, add an append-only `audit_events` table (actor, entity, action, before/after) written in-transaction.

- **ISSUE-023 (Medium)** — Refresh-token rotation (`AuthService.refresh_access_token`) has no **reuse detection**: presenting an already-revoked token returns 401 but does not revoke the descendant family, so a thief who refreshes first keeps a valid session while the victim is silently logged out. **Fix:** on revoked-jti reuse, revoke all of that user's refresh tokens (track family/`sub` in the denylist). Related (Low): OTP codes are stored in plaintext in `otp_codes.code` — store a hash.

- **ISSUE-024 (Low)** — Mutating service methods commit outside the `get_db()` contract in places (`verify_otp` failure path — justified and documented; `_prepare_and_mark_sent` — mid-request commit means later request failure can't roll the SENT transition back). Document this explicitly per method or move post-commit work to `after_commit` hooks.

---

#### 3. Prioritized Remediation Roadmap

**Phase 1 — Critical correctness (this sprint)**
1. ISSUE-001 (quote send 500 under Postgres) → unblocks ISSUE-002.
2. ISSUE-004 (duplicate invoices from concurrent quote conversion).
3. ISSUE-020 (mixed-currency vendor payables) and ISSUE-019 (customer locking + currency freeze).
4. STUB-002 short-term: stop saying "attached" in email bodies (one-line template fix) until attachment ships.
5. ISSUE-018 PII PDF purge from git history.

**Phase 2 — High-priority reliability/security (next sprint)**
6. ISSUE-002 + ISSUE-003: shared two-phase send in `BaseDocumentService`, then email outbox + scheduler. *Depends on Phase 1 item 1.* The outbox scheduler also resolves ISSUE-007 (one scheduling mechanism for outbox, overdue transitions, OTP purge).
7. ISSUE-023 (refresh-token reuse detection).
8. STUB-002 full fix: implement PDF attachment (renderers already exist in `_render_pdf`).
9. ISSUE-014 (silent export truncation).

**Phase 3 — Structural improvements**
10. ISSUE-009/ISSUE-011/ISSUE-012 service decomposition + filter/retry-loop dedup (do ISSUE-011 before ISSUE-009 — extracting filters first makes the split mechanical). ISSUE-008, ISSUE-010 alongside.
11. ISSUE-021, ISSUE-022 (statement parity, audit table), ISSUE-005, ISSUE-006, ISSUE-015.

**Phase 4 — Cleanup & product decisions**
12. STUB-001: either implement the Products module end-to-end (model → service → router → page) or remove the route; the mock page must not ship.
13. STUB-003/STUB-004: decide Contacts/Bills roadmap; delete the dead code and `contact_id` field if not planned.
14. STUB-005/006/007, ISSUE-013, ISSUE-016, ISSUE-017, ISSUE-024, remaining ISSUE-018 hygiene.