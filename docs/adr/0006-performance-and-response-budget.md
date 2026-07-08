# ADR-0006: Performance & the <1s response budget

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** Engineering
- **Related:** ADR-0002, ADR-0003, ADR-0005, WI-07, WI-08, WI-11, WI-13

## Context
The product must feel instant: interactive endpoints should return in **under 1 second** at the p95 while remaining correct on financial data. Costs that threaten this are: N+1 queries, unindexed scans, per-request recomputation of aggregates, per-request DB user lookups, and heavy synchronous work (PDF/Excel generation) on the request path.

## Decision
We adopt an explicit **<1s p95 response budget** enforced by a set of standing rules: push work into SQL, index every filter/sort, keep the request path free of heavy CPU and slow I/O, gate exports off the request threads, and cache only derived read aggregates — **never** financial mutations or per-row balances.

## What it does today
- **SQL-side aggregation** everywhere (dashboard, vendor payables) instead of Python loops (ADR-0003) — one grouped query, not N.
- **Indexes for all hot filters/search** (composite btree, trigram/GIN, FTS) so lists and search avoid scans (ADR-0003).
- **Eager loading** of list relationships to kill N+1 (ADR-0003).
- **Export concurrency gate** ([api/app/common/export_limiter.py](../../api/app/common/export_limiter.py)): heavy Excel/PDF runs off-thread through an `anyio.CapacityLimiter` sized by `EXPORT_MAX_CONCURRENCY` (default 4) and **sheds load with 503 + Retry-After** when saturated rather than queueing behind request threads. Row cap via `BATCH_SIZE` with `X-Truncated`/`X-Export-Limit` headers.
- **Short locked critical sections**; slow side-effects (PDF render, SES) happen after commit (ADR-0002/0005).
- **Financial values are computed fresh, never cached on rows** (ADR-0002) — correctness first.

## Business logic & rules
- **Budget: p95 < 1s** for interactive endpoints; heavy/bulk work is async or gated, not inline.
- **No seq scans on user-facing paths** — `EXPLAIN` must show index usage (ADR-0003).
- **Aggregate in the database**, return `Decimal`, don't iterate rows in the service.
- **Cache only derived reads**, with explicit invalidation and bounded TTL — never the write path, never balances (this is what keeps money correct while still going fast).
- **Nothing slow inside a lock** — commit the state change, then do PDF/email.

## Consequences
- (+) Fast, predictable latency that scales with data via indexes + aggregation.
- (+) Exports can't starve interactive traffic.
- (−) Recomputing aggregates per request (no cache yet) costs DB time on the hottest reads (WI-07).
- (−) Per-request user lookup adds one query to every authed call (WI-08).

## Improvements
- **WI-07**: Redis short-TTL cache for hot read aggregates (dashboard, vendor cards) with explicit invalidation.
- **WI-08**: cache the per-request user/role lookup (denylist-aware).
- **WI-11**: per-route latency + slow-query logging so budget breaches are visible; alert on p95 regressions.
- **WI-13**: async delivery for very large exports.

## Resilience & <1s response rules
- Treat the budget as a gate: a new endpoint isn't done until it's `EXPLAIN`-checked and query-count-tested.
- Keep the request path CPU-light and lock-light; gate or defer anything heavy.
- Measure before optimizing — WI-11 makes latency observable so tuning targets the actual p95 tail.
