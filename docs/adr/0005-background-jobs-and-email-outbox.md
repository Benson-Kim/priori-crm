# ADR-0005: Background jobs & transactional email outbox

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** Engineering
- **Related:** ADR-0001, ADR-0006, ADR-0007, WI-02, WI-05

## Context
The system needs periodic work (transition overdue invoices/expenses, expire quotes, purge OTPs) and reliable outbound email tied to state changes (e.g. invoice `DRAFT → SENT`). It must never lose an email on a delivery failure, must keep the send UX synchronous on the happy path, and should avoid the operational weight of an in-process scheduler or a broker like Celery.

## Decision
We use a **transactional email outbox** plus **internal HTTP job endpoints driven by an external scheduler**. There is deliberately **no in-process scheduler**; jobs are idempotent, secret-gated endpoints that any external cron can call.

## What it does today
- **Email outbox** ([api/app/common/email_outbox.py](../../api/app/common/email_outbox.py)): a document email is enqueued in the **same DB transaction** as the state change that announces it, so a delivery failure can't lose it. `deliver_now` does best-effort immediate send after the locked phase commits (synchronous happy path); `drain` retries pending/failed rows. Rows retry up to `MAX_DELIVERY_ATTEMPTS = 5`, then dead-letter (`dead`) for operator attention. PDF attachments are rendered at **delivery time** from the document reference, so the locked send phase does no PDF work and every retry attaches fresh bytes.
- **Internal job endpoints** (all `include_in_schema=False`, gated by `X-Internal-Secret` via `verify_internal_secret`, which **fails closed** if the secret is unset and compares in constant time):
  - `POST /internal/email-outbox/drain`
  - `POST /invoices/internal/transition-overdue`
  - `POST /quotes/internal/transition-expired`
  - `POST /expenses/internal/transition-overdue`
  - `POST /auth/internal/purge-otps`
- **Schedulers** (in-repo): Render cron services ([render.yaml](../../render.yaml)) — outbox drain `*/5 * * * *`, nightly `15 0 * * *`; and GitLab scheduled pipelines ([.gitlab/ci/scheduled-jobs.yml](../../.gitlab/ci/scheduled-jobs.yml)) with a helper that fails (alerts) on any non-2xx or dead-lettered rows.

## Business logic & rules
- **Outbox writes are in-transaction with their trigger** — the queued row is the durable record of intent.
- **Jobs are idempotent** — safe to run twice; a duplicate drain/transition is a no-op on already-processed rows.
- **Internal endpoints fail closed** — no secret, no run; the secret is shared only with the scheduler.
- **Locked critical sections stay short** — no PDF/email work while a row lock is held.

## Consequences
- (+) No lost emails; no broker to operate; jobs are just endpoints (testable, curl-able).
- (+) Happy-path send stays synchronous for good UX.
- (−) **Correctness depends on the external scheduler actually firing.** A paused/misconfigured cron silently stops all periodic work — and today the nightly cron **omits the expenses overdue transition** (WI-02), so vendor "Overdue" never advances in prod.
- (−) No missed-run detection yet (WI-05).

## Improvements
- **WI-02**: add the expenses overdue transition to the nightly scheduler (both Render and GitLab).
- **WI-05**: persist `last_success_at` per job, surface via `/health/detailed`, alert when overdue; optionally an APScheduler in-process fallback behind a leader lock.

## Resilience & <1s response rules
- Send endpoints commit the business change first, then deliver — a slow SES call never extends the locked transaction or blocks the state change.
- The drain cadence bounds redelivery latency; dead-lettering bounds retry storms.
- Alert if a job hasn't succeeded within its window (the scheduler is the single point of failure, so it must be watched).
