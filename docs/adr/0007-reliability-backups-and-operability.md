# ADR-0007: Reliability, backups, DR & operability

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** Engineering
- **Related:** ADR-0005, ADR-0008, WI-01, WI-03, WI-05, WI-09, WI-11

## Context
The system holds financial records of record. It must survive process crashes, dependency outages (Redis, SES), and data-loss events with a known recovery posture — and operators must be able to see health, detect missed work, and recover. Today several reliability primitives exist, but backup/restore is undocumented/untested and some health signals are broken.

## Decision
We make the app **crash-safe and degradation-tolerant** (transactional writes, idempotent jobs, fail-open caches with alerting, health endpoints) and commit to a **documented, rehearsed backup/restore posture** with defined RPO/RTO. Reliability gaps are tracked as work-items, not left implicit.

## What it does today
- **Transactional integrity**: `get_db()` commits on success / rolls back on error; audit + outbox rows are written in-transaction with their trigger (ADR-0002/0005) — no partial financial state.
- **Idempotent recovery**: internal jobs (transitions, purge, drain) are safe to re-run; the outbox retries then dead-letters (ADR-0005).
- **Soft-delete + FK protection**: customers soft-delete (`status=DELETED`, reads filter it out); hard-delete is gated by `ondelete=RESTRICT` + `passive_deletes` to protect document history; `X-Delete-Type` header distinguishes soft vs hard.
- **Graceful degradation**: rate-limit + token denylist **fail open** on Redis outage (availability over security); export gate sheds load with 503 + Retry-After.
- **Health**: `/health` (liveness) and `/health/detailed` (pool metrics via `get_pool_status()`). Non-root container user, multi-stage image ([api/Dockerfile](../../api/Dockerfile)).
- **Config fail-fast**: prod won't boot misconfigured ([config.py](../../api/app/lib/config.py)).

## Business logic & rules
- **No partial financial state** — every mutation + its audit/outbox row commit together or not at all.
- **Recovery jobs are idempotent** — re-running after a failure is always safe.
- **Document history is protected** — you cannot hard-delete a customer with dependent documents.
- **Degrade, don't crash** — a dependency outage reduces guarantees (with alerting), it doesn't take the app down.

## Consequences
- (+) Strong crash-safety and safe recovery.
- (−) **No documented/tested DB backup, restore, or PITR** (WI-03) — the single biggest reliability gap; untested backups are not backups.
- (−) The container **healthcheck is broken** (`import requests`, not installed) and `render.yaml healthCheckPath` mismatches the Dockerfile path (WI-01).
- (−) **No missed-run detection** for the external scheduler (WI-05); unbounded growth of audit/dead-outbox rows (WI-09).

## Improvements
- **WI-03**: document backup cadence/retention/PITR + a rehearsed restore runbook; set RPO/RTO; optional independent `pg_dump` to object storage.
- **WI-01**: fix the healthcheck + path mismatch.
- **WI-05**: per-job freshness + alerting.
- **WI-09**: bounded retention for audit/outbox/exports.
- **WI-11**: latency/slow-query observability + alerting.

## Resilience & <1s response rules
- **Backups must be tested** — a restore is rehearsed on a schedule, not assumed.
- **Every scheduled job's freshness is monitored** — a job that stops firing raises an alert within one window.
- **Every degraded mode is observable** — fail-open, export-shed, and dead-letter states emit signals, never fail silently.
- Health endpoints stay cheap and fast so they never themselves become a latency/availability problem.
