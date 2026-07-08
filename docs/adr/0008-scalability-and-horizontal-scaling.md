# ADR-0008: Scalability & horizontal scaling

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** Engineering
- **Related:** ADR-0001, ADR-0003, ADR-0004, WI-06, WI-07, WI-14

## Context
The app runs today as a single Render web service (4 uvicorn workers) with managed Postgres and Redis. Growth will require running more workers/instances behind a load balancer. To scale horizontally, every request must be servable by any worker — i.e. **no node-local state** — and shared state (limits, denylist, files, cache) must be externalized.

## Decision
We keep the API **stateless** and push all shared state to **Postgres and Redis** (plus object storage for files). The only thing blocking multi-instance deployment today is local-disk file storage, which the storage abstraction already supports switching to S3.

## What it does today
- **Stateless request handling** — no in-memory session; JWTs carry identity; any worker serves any request.
- **Shared state in Redis** ([config.py](../../api/app/lib/config.py), [render.yaml](../../render.yaml)): rate-limit store and token denylist are **enforced to Redis in production** (`RATE_LIMIT_BACKEND=redis`, `TOKEN_DENYLIST_BACKEND=redis`) — the prod validator rejects the in-memory backend, so per-worker limits can't silently multiply. Redis uses `allkeys-lru`.
- **Shared state in Postgres** — all durable state (documents, payments, audit, outbox, reference sequences) is in the DB with pooled connections (ADR-0003).
- **Pluggable storage** ([api/app/lib/storage.py](../../api/app/lib/storage.py)): local FS (single-node) or S3 with presigned URLs (multi-node). Production currently runs `STORAGE_BACKEND=local` on a 10 GB Render disk.
- **Idempotent, externally-scheduled jobs** (ADR-0005) — no in-process scheduler to duplicate across instances.

## Business logic & rules
- **No node-local durable/shared state** — anything that must be consistent across requests lives in Postgres or Redis.
- **In-memory backends are dev-only** — prod fails fast if configured with them.
- **Scheduled jobs run once, centrally** — they are endpoints hit by one scheduler, not per-instance timers.

## Consequences
- (+) The web tier can scale to N instances as soon as storage is externalized.
- (−) **Local-disk storage pins the API to one node** (WI-06) — the current hard blocker to horizontal scale, and a capacity/backup liability.
- (−) Connection math (`workers × pool`) can exceed the DB ceiling as instances grow (WI-14).
- (−) An in-process scheduler fallback (WI-05) would need a leader lock to stay single-fire across instances.

## Improvements
- **WI-06**: switch to S3 storage (+ lifecycle/IAM), migrate existing files — unblocks multi-instance.
- **WI-14**: right-size pool per worker vs `max_connections`; add PgBouncer (transaction pooling) when scaling out.
- **WI-07**: shared Redis cache for hot aggregates so added instances don't multiply DB read load.

## Resilience & <1s response rules
- Statelessness keeps latency flat as instances are added — no cross-node affinity or sticky sessions.
- Shared limiter/denylist in Redis keep security guarantees consistent across the fleet (ADR-0004).
- Connections are pooled and bounded so scaling out never exhausts the DB and pushes latency past budget (ADR-0003/0006).
