# ADR-0003: Data layer & query performance

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** Engineering
- **Related:** ADR-0002, ADR-0006, `../database.md`, WI-10, WI-14

## Context
The application is read-heavy (lists, detail pages, dashboards, statements) with correctness-critical writes (payments, status transitions). It must hold a <1s response budget as data grows, avoid full-table scans on search, and prevent N+1 explosions — all on a single managed PostgreSQL instance.

## Decision
We use **PostgreSQL via SQLAlchemy 2.0 + Alembic**, with a **tuned connection pool**, **purpose-built indexes** (composite btree, trigram/GIN for search, FTS), **eager-loading / SQL-side aggregation** to kill N+1, **DB sequences** for race-free reference numbers, and **optimistic locking** for concurrent writes.

## What it does today
- **Connections/pool** ([api/app/common/database.py](../../api/app/common/database.py)): `pool_pre_ping=True`, `pool_size=20`, `max_overflow=10`, `pool_timeout=30s`, `pool_recycle=3600s`, `READ COMMITTED`. Bounds are validated in [config.py](../../api/app/lib/config.py). `get_pool_status()` exposes live metrics via `/health/detailed`. `get_db()` commits on success / rolls back on exception.
- **Migrations**: Alembic is authoritative (`create_all` removed); the container runs `alembic upgrade head` before boot. A constraint naming convention aids autogenerate.
- **Indexing** (a deliberate strength):
  - Composite btree on `customers` (`ix_customers_status_created`, `ix_customers_type_status`) and `purchase_orders` (`ix_purchase_orders_vendor_status`) — the latter directly serves vendor-scoped PO reads.
  - **Trigram GIN** (`pg_trgm`) on all `ILIKE '%term%'` search columns across invoices/quotes/expenses/vendors/customers (migration `…add_trgm_search_indexes`) — avoids leading-wildcard full scans.
  - **FTS `to_tsvector` GIN** on customers; OTP hot-path index; audit-events composite index; CI-unique customer email.
- **N+1 mitigation**: invoices/quotes use `joinedload`/`selectinload` for line_items/payments/customer; the dashboard uses correlated subqueries + SQL aggregation (`func.sum`, `group_by`, `union_all`) and explicitly avoids N+1; vendor list/detail payables use a single grouped aggregate query.
- **Reference numbers**: DB sequences ([api/app/common/reference_sequence.py](../../api/app/common/reference_sequence.py) + triggers) generate invoice/quote/PO numbers without race conditions.
- **Optimistic locking**: `assert_version()` does a `SELECT … FOR UPDATE` on a `version` column and raises HTTP 409 on conflict.

## Business logic & rules
- **Aggregation happens in SQL, not Python** — sums/counts/splits (e.g. vendor paid-vs-pending) are computed with `func.sum`/`case`, returning `Decimal`, never by iterating rows in the service.
- **Every user-facing filter/sort must be index-served** — a new read path is not done until `EXPLAIN` shows an index scan, not a seq scan.
- **Reads that render lists eager-load their relationships**; `lazy` relationships are only for detail contexts.

## Consequences
- (+) Fast search and lists without scans; predictable write concurrency; race-free numbering.
- (−) Index maintenance cost on writes (acceptable — write volume is low relative to reads).
- (−) Pool × worker math needs watching as instances scale (WI-14).

## Improvements
- `EXPLAIN`-audit the new vendor card queries and add covering indexes if a filter+sort isn't served (WI-10).
- Verify `Customer.invoices` (`lazy="dynamic"`) / `quotes` (`lazy="select"`) don't N+1 in any list context (WI-10).
- Add a short-TTL Redis cache for hot read aggregates with explicit invalidation (WI-07), and consider PgBouncer when scaling out (WI-14).

## Resilience & <1s response rules
- **Index-or-it-doesn't-ship**: every new query is `EXPLAIN`-checked; a query-count regression test guards N+1 on the hot detail/list endpoints.
- Keep transactions short; never hold a row lock across a network/PDF/email call.
- Pool sized so peak (`workers × (pool + overflow)`) stays under the Postgres connection ceiling with headroom.
