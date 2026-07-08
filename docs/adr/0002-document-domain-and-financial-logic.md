# ADR-0002: Document domain & financial business logic

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** Engineering
- **Related:** ADR-0001, ADR-0003, `../database.md`

## Context
The core of the product is a set of **financial documents** — quotes, invoices, purchase orders, expenses — plus vendor/customer statements derived from them. Money must be correct, auditable, and safe under concurrency. Business rules (what state a document can move to, how balances are computed, who owns the branding on an issued document) must be enforced consistently and never be bypassable by a client.

## Decision
We centralize all document behavior in the **service layer** behind three pillars: **per-document state machines**, **server-authoritative financial math**, and **optimistic locking + an append-only audit trail**. The database is authoritative; clients never compute money.

## What it does today
- **State machines.** Each document has an explicit lifecycle enforced by a shared `StateMachineMixin` (e.g. Purchase Orders: `DRAFT → SENT → PAID` only; vendors: `ACTIVE ↔ INACTIVE`, see [api/app/modules/vendors/service.py](../../api/app/modules/vendors/service.py)). Illegal transitions are rejected, not silently coerced.
- **Financial math is server-side and typed `Decimal`.** For PO-level VAT (feature `PO-27`): `line_total = qty × unit_price`, `subtotal = Σ line_total`, `tax_total = round(subtotal × vat_rate)` when `vat_enabled` else 0, `total = subtotal + tax_total`. VAT is a **single PO-level charge on the subtotal, not per-line** — line items always persist `tax_type=no_tax` (see `purchase_orders/models.py`, `common/financial.calculate_subtotal_vat`). CHECK constraints enforce `vat_rate ∈ [0,1]` and `vat_enabled=false OR vat_rate IS NOT NULL`.
- **Balances are signed and never clamped.** `balance_due = total − amount_paid`; a negative balance is a legitimate recorded overpayment/credit, not an error. `is_paid` is derived (`status == PAID` or a `SENT` doc with `balance_due ≤ 0`); a `DRAFT` doc with zero total must **not** count as paid.
- **Payment settlement** advances `amount_paid`, recomputes `balance_due`, and transitions `SENT → PAID` when cleared (stamping `paid_at`); editing/deleting a payment re-syncs and can reopen `PAID → SENT`. Multi-currency payments apply the converted (document-currency) amount.
- **Vendor payables** are computed fresh from `Expense` rows (`OPEN_PAYABLE_STATUSES = PENDING|OVERDUE`; overdue = `OVERDUE`) in a single grouped SQL query ([vendors/service.py `_compute_payables_for_vendor`](../../api/app/modules/vendors/service.py)) — **never cached on the vendor row**. Purchase orders are non-payable commitments in the vendor context and report a `0.00` balance.
- **Issued documents snapshot owner branding** (immutable) so historical PDFs never change if company details change later.
- **Audit trail.** `audit_events` ([api/app/common/audit.py](../../api/app/common/audit.py)) is append-only, written in the **same transaction** as the mutation it records (`payment_recorded | canceled | soft_deleted | hard_deleted`); never updated or deleted by app code.

## Business logic & rules
- The **server is the sole authority** for status and money; the client's `balance_due`/`is_paid` are display copies of server truth.
- **Transitions are guarded**: record-payment only on a `SENT` doc with positive balance; delete only in `DRAFT`; privileged/financial actions require ADMIN/MANAGER (ADR-0004).
- **Overpayment is legal** and recorded, not blocked — the ledger reflects reality.
- **Every financial mutation emits an audit event in-transaction** — no mutation without a trail.

## Consequences
- (+) Money is correct, concurrency-safe, and fully auditable.
- (+) Rules can't be bypassed by a malicious/buggy client.
- (−) More server round-trips (no client-side optimistic money math) — deliberate, and cheap given the query design in ADR-0003.

## Improvements
- Land the planned Bills module (PO→Bill conversion currently stubbed via `converted_bill_id`, no FK yet) so vendor "bills" stop overloading Expenses.
- Regenerate the OpenAPI schema for PO-VAT fields and drop the temporary `readPoVat` client cast.

## Resilience & <1s response rules
- All money math runs in SQL/`Decimal` on the server; **financial values are never cached** (WI-07 caches only derived read aggregates, never balances or the write path).
- Mutations take row locks for the minimal critical section, then commit before any slow side-effect (PDF render, email) — see ADR-0005/0006.
- Audit + state-machine invariants are covered by tests; a transition or balance regression fails CI, not production.
