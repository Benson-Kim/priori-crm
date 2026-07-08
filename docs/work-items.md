# Work Items — Optimization, Reliability, Security & Scalability

> Living backlog for platform hardening across `priori-crm` (FastAPI API under `api/`, React/Vite UI under `frontend/`).
> Each item states **Where / Why / How / Priority / Effort / Acceptance**. Ordered by priority band.
> Priority: **P0** (correctness/data-loss/security risk), **P1** (reliability/scale before growth), **P2** (optimization/hygiene).
> Effort: **S** (<½ day), **M** (1–3 days), **L** (>3 days).

The platform already ships strong foundations — connection pooling with tuning knobs, Redis-backed shared rate-limit + token denylist (enforced in prod), trigram/GIN + composite indexes, N+1 mitigation via eager loading and SQL-side aggregation, optimistic locking, a transactional email outbox with dead-lettering, an append-only audit trail, export-concurrency gating, path-traversal-safe pluggable storage, and fail-fast prod config validation. The items below close the remaining gaps.

> **Status legend:** ✅ Done · ⏳ Open. Implemented this pass: **WI-01, WI-02, WI-04, WI-10**.

---

## P0 — Correctness / data-safety / security

### WI-01 · Fix the broken container healthcheck (and path mismatch)
- **Where:** [api/Dockerfile:37-38](api/Dockerfile#L37-L38), [render.yaml:32](render.yaml#L32)
- **Why:** `HEALTHCHECK` runs `python -c "import requests; ..."` but `requests` is **not** in [api/requirements.txt](api/requirements.txt) — the check errors on every run, so Docker reports the container `unhealthy` (or orchestrators kill/refuse it). Separately, the Dockerfile probes `/api/v1/health` while `render.yaml` sets `healthCheckPath: /health` — one of them is wrong; they must agree with the actual health route.
- **How:** Replace the `requests` call with a stdlib probe: `CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health').status==200 else 1)"`. Confirm the real health path in [api/app/modules/health/router.py](api/app/modules/health/router.py) and make `render.yaml healthCheckPath` match it exactly.
- **Priority:** P0 · **Effort:** S
- **Acceptance:** `docker inspect` shows `healthy`; Render health checks pass; both paths reference the same route.

### WI-02 · Nightly scheduler omits the expenses overdue transition
- **Where:** [render.yaml:147-152](render.yaml#L147-L152) vs the internal endpoint `POST /expenses/internal/transition-overdue` in [api/app/modules/expenses/router.py](api/app/modules/expenses/router.py)
- **Why:** The nightly cron calls invoices `transition-overdue`, quotes `transition-expired`, and `purge-otps` — but **not** the expenses overdue transition that exists in code. Vendor payables/overdue figures (driven by `ExpenseStatus.OVERDUE`, [vendors/service.py:58](api/app/modules/vendors/service.py#L58)) will never flip to OVERDUE in production, silently understating "Overdue" on the vendor detail and dashboards.
- **How:** Add the expenses call to the nightly `dockerCommand`; mirror it in the GitLab scheduled pipeline [.gitlab/ci/scheduled-jobs.yml](.gitlab/ci/scheduled-jobs.yml). Add a test asserting all internal transition endpoints are wired to a scheduler.
- **Priority:** P0 · **Effort:** S
- **Acceptance:** Overdue expenses transition nightly in prod; vendor "Overdue" reflects reality.

### WI-03 · Document & rehearse DB backup / restore / PITR
- **Where:** [render.yaml:6-14](render.yaml#L6-L14) (managed `pserv`), new `docs/operations/backups.md`
- **Why:** There is **no in-repo backup or restore procedure**. Backups are implicitly delegated to the managed provider, but with no documented cadence, retention, PITR window, or **tested restore**, a data-loss event has no known RTO/RPO. Untested backups are not backups.
- **How:** Document snapshot cadence + PITR window + retention; add a quarterly restore-rehearsal runbook (restore to a scratch DB, run `alembic current`, smoke-test). Consider a scheduled logical `pg_dump` to object storage as a provider-independent second copy. State RPO/RTO targets.
- **Priority:** P0 · **Effort:** M
- **Acceptance:** Written runbook + at least one rehearsed restore recorded; RPO/RTO agreed.

### WI-04 · Enable SAST (bandit) in CI
- **Where:** [api/pyproject.toml](api/pyproject.toml) (ruff `S` rules deferred), [.gitlab-ci.yml](.gitlab-ci.yml)
- **Why:** Bandit/`S` security lints are noted as **not enforced**. Managed GitLab SAST/secret/dependency scanning runs, but repo-level Python security lint (hardcoded secrets, weak crypto, `subprocess`, tempfile misuse) is off — cheap coverage left on the table.
- **How:** Turn on ruff `S` (or run bandit) in the lint stage; triage/inline-suppress with justification; gate the pipeline.
- **Priority:** P0 · **Effort:** S
- **Acceptance:** `S` rules run in CI and pass with an explicit, reviewed suppression list.

---

## P1 — Reliability / scalability before growth

### WI-05 · Missed-run detection for the external scheduler
- **Where:** internal endpoints (invoices/quotes/expenses/auth) + [api/app/common/email_outbox.py:15-19](api/app/common/email_outbox.py#L15-L19); schedulers in [render.yaml:118-160](render.yaml#L118-L160), [.gitlab/ci/scheduled-jobs.yml](.gitlab/ci/scheduled-jobs.yml)
- **Why:** By design there is **no in-process scheduler** — a misconfigured/paused cron silently stops overdue transitions, OTP purge, and email delivery. The only current mitigation is "alert on job failure," which does not catch a job that never fires.
- **How:** Persist a `last_success_at` per job (small table or Redis key) written by each internal endpoint; expose via `/health/detailed`; alert when any job is overdue for its window. Optionally add an APScheduler in-process fallback guarded by a leader lock.
- **Priority:** P1 · **Effort:** M
- **Acceptance:** A paused cron raises an alert within one window; `/health/detailed` shows per-job freshness.

### WI-06 · Move uploads to object storage (unblock horizontal scale)
- **Where:** [render.yaml:83-111](render.yaml#L83-L111) (`STORAGE_BACKEND=local`, 10 GB disk), [api/app/lib/storage.py](api/app/lib/storage.py)
- **Why:** Local-disk storage pins the API to a single node with a finite 10 GB volume — you **cannot** run >1 web instance or autoscale, and the disk is a capacity/backup liability. The storage abstraction already supports S3 with presigned URLs.
- **How:** Provision an S3 bucket + least-privilege IAM + lifecycle/retention policy; set `STORAGE_BACKEND=s3`; migrate existing files; verify presigned download paths.
- **Priority:** P1 · **Effort:** M
- **Acceptance:** API runs ≥2 instances with no local-disk dependency; uploads/downloads work via S3.

### WI-07 · Add a response/query cache for hot read aggregates
- **Where:** [api/app/modules/dashboard/queries.py](api/app/modules/dashboard/queries.py), [api/app/modules/vendors/service.py](api/app/modules/vendors/service.py), Redis (already provisioned)
- **Why:** Redis is used only for rate-limit + denylist. Hot, read-mostly aggregates (dashboard KPIs, vendor summary cards) recompute on every request. A short-TTL cache cuts p95 latency and DB load — directly supporting the <1s budget.
- **How:** Add a small cache wrapper (Redis, 30–120 s TTL) for read aggregates with **explicit invalidation** on the relevant mutations. Keep the "never cache authoritative financial mutations" rule — cache derived reads only, never the write path or per-row balances.
- **Priority:** P1 · **Effort:** M
- **Acceptance:** Dashboard/vendor-card p95 drops measurably; stale windows bounded by TTL; mutations invalidate.

### WI-08 · Cache the per-request user lookup
- **Where:** `get_current_user` in [api/app/common/dependencies.py:19-34](api/app/common/dependencies.py#L19-L34)
- **Why:** Every authenticated request does a DB round-trip to load the user/role — pure overhead on the hot path at scale.
- **How:** Short-TTL cache (Redis, ~30–60 s) keyed by user id, **invalidated by the token denylist fence** so role changes/revocations take effect promptly. Fall back to DB on miss.
- **Priority:** P1 · **Effort:** M
- **Acceptance:** Authed endpoints drop one query each; revocation/role change still enforced within the fence window.

### WI-09 · Retention / purging beyond OTPs
- **Where:** OTP purge in [api/app/modules/auth/service.py](api/app/modules/auth/service.py); audit in [api/app/common/audit.py](api/app/common/audit.py); outbox in [api/app/common/email_outbox.py](api/app/common/email_outbox.py)
- **Why:** Only OTPs/reset tokens are purged. `audit_events` (append-only), `email_outbox` `dead` rows, and stored exports grow unbounded — degrading query planning and inflating storage/backup size over time.
- **How:** Add bounded, batched retention jobs (behind internal endpoints, driven by the nightly cron): archive/prune old audit events per a compliance-approved window; alert-then-purge long-dead outbox rows; TTL/cleanup for generated export artifacts. Keep audit legally-retained data archived, not deleted, where required.
- **Priority:** P1 · **Effort:** M
- **Acceptance:** Table growth bounded; retention windows documented; nightly job runs within budget.

### WI-10 · Index / N+1 audit for read paths (incl. new vendor cards)
- **Where:** `Customer.invoices` (`lazy="dynamic"`), `quotes` (`lazy="select"`) in [api/app/modules/customers/models.py](api/app/modules/customers/models.py); new vendor card queries in [api/app/modules/vendors/service.py](api/app/modules/vendors/service.py)
- **Why:** Eager loading is used well in most modules, but `lazy` relationships can N+1 in list contexts, and the new vendor PO/payment/bill aggregates must be `EXPLAIN`-verified to hit the existing composite indexes (e.g. `ix_purchase_orders_vendor_status`) rather than scanning.
- **How:** `EXPLAIN (ANALYZE)` the card queries on seeded data; add covering/composite indexes where a filter+sort isn't served; add a regression test asserting query counts for the vendor detail + list endpoints.
- **Priority:** P1 · **Effort:** M
- **Acceptance:** Card queries use index scans; no N+1 in vendor/customer list contexts; query-count test guards it.

---

## P2 — Optimization / operability hygiene

### WI-11 · Observability: slow-query logging, request metrics, saturation visibility
- **Where:** middleware in [api/app/common/middleware.py](api/app/common/middleware.py); `/health/detailed`; export gate [api/app/common/export_limiter.py](api/app/common/export_limiter.py)
- **Why:** To hold a <1s budget you must **see** latency. Today there's structured logging but no per-route latency histogram, no slow-query log, and export-saturation 503s aren't surfaced as a signal.
- **How:** Emit per-route timing + status metrics; log queries over a threshold (SQLAlchemy event hook); count export-limiter rejections; wire to the monitoring stack (`SENTRY_DSN` already plumbed) with dashboards + alerts on p95 breach.
- **Priority:** P2 · **Effort:** M
- **Acceptance:** p95 per route visible; slow queries logged; export saturation alertable.

### WI-12 · Ratify the fail-open rate-limit / denylist behavior
- **Where:** [api/app/common/rate_limit_store.py](api/app/common/rate_limit_store.py), [api/app/common/token_denylist.py](api/app/common/token_denylist.py)
- **Why:** Both **fail open** when Redis is down (availability over security). That's a legitimate tradeoff but must be an explicit, documented decision — during a Redis outage, revoked tokens are briefly honored and limits lapse.
- **How:** Record the decision in an ADR (see ADR-0004/0006); add a metric/alert on Redis-unavailable so fail-open windows are observed; optionally a stricter degraded mode for auth-sensitive routes.
- **Priority:** P2 · **Effort:** S
- **Acceptance:** Documented decision + alert on fail-open activation.

### WI-13 · Export scalability for large vendors/datasets
- **Where:** [api/app/common/excel.py](api/app/common/excel.py), [api/app/common/pdf.py](api/app/common/pdf.py), [api/app/common/export_limiter.py](api/app/common/export_limiter.py), `BATCH_SIZE=1000` ([render.yaml:98](render.yaml#L98))
- **Why:** Exports run in-process, gated to `EXPORT_MAX_CONCURRENCY` and capped at `BATCH_SIZE` rows with truncation headers. Fine now, but large tenants will hit truncation silently if the UI ignores `X-Truncated`, and heavy PDF/Excel builds compete with request threads.
- **How:** Surface `X-Truncated`/`X-Export-Limit` in the UI; for large exports consider async job + emailed/download-link delivery; keep heavy generation off the request event loop (already via `run_export`).
- **Priority:** P2 · **Effort:** M
- **Acceptance:** Users see truncation; large exports don't degrade request latency.

### WI-14 · DB pool sizing vs worker count
- **Where:** `--workers 4` in [api/Dockerfile:44](api/Dockerfile#L44); `DB_POOL_SIZE=20` / `DB_MAX_OVERFLOW=10` in [api/app/lib/config.py:24-26](api/app/lib/config.py#L24-L26)
- **Why:** 4 uvicorn workers × (pool 20 + overflow 10) = up to 120 connections per instance — can exceed a starter-plan Postgres connection ceiling once instances scale, causing `pool_timeout` errors under load.
- **How:** Right-size pool per worker against the Postgres `max_connections`; consider a PgBouncer transaction pooler when scaling instances; document the formula.
- **Priority:** P2 · **Effort:** S
- **Acceptance:** Peak connections stay under the DB ceiling with headroom; documented sizing.

---

## Suggested sequencing
1. **P0 batch** (WI-01, 02, 04 are hours; WI-03 is the one L-ish reliability must-do).
2. **P1 scale-enablers** (WI-06 storage, WI-05 scheduler safety, WI-07/08 caching, WI-10 index audit).
3. **P2 hygiene** as capacity allows.
