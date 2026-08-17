# Service Level Objectives (SLOs)

SLOs define what *good* looks like **before** something breaks. They turn
vague expectations (“the API should be fast and reliable”) into measurable
commitments — and we must know the system is breaking **before the
customers do** (see the synthetic monitoring section below).

## Availability reference table

| Target | Allowed downtime / year | / 30-day month |
|---|---|---|
| 99% | 3.65 days | ≈ 7.3 hours |
| 99.9% | 8.77 hours | ≈ 43.8 minutes |
| 99.99% | 52.59 minutes | ≈ 4.4 minutes |

## Our SLOs

### 1. API availability — **99.9%** (monthly window)
- **SLI**: share of successful synthetic probes against
  `GET /api/v1/health` (200 + `"status":"healthy"` within the latency
  budget).
- **Measured by**: `scheduled:synthetic-critical-path` (every 5 minutes,
  `.gitlab/ci/scheduled-jobs.yml`). ≤ 8 failed probes per 30-day month
  keeps the SLO.
- **Error budget**: ≈ 43.8 minutes of downtime per month.

### 2. API latency — **95% of requests < 500 ms** (monthly window)
- **SLI**: `duration_ms` from the structured request logs
  (`RequestLoggingMiddleware`); the same value is returned to clients as
  `X-Response-Time`.
- Synthetic probes enforce a hard 5 s budget on the critical path.

### 3. Email delivery — **zero dead-lettered emails; queued mail delivered within 15 minutes**
- **SLI**: the drain summary (`delivered` / `failed` / `dead`) reported by
  `POST /api/v1/internal/email-outbox/drain` on the ~5-minute schedule.
- Any `dead > 0` fails the scheduled job immediately (alert). See
  `email-outbox-dlq.md`.

### 4. Data durability — **RPO ≤ 24 h, RTO ≤ 4 h**
- **RPO** (max data loss): bounded by the nightly `pg_dump` + uploads archive
  (`deploy/db_backup.sh` on the droplet, copied offsite). Anything written
  after the last nightly backup is at risk; the pre-deploy dump narrows the
  window only on deploy days.
- **RTO**: ≤ 4 hours from total droplet loss to serving again, following the
  runbook.
- **SLI**: the monthly `scheduled:db-restore-verify` run
  (`.gitlab/ci/scheduled-jobs.yml`) restores the newest offsite dump into a
  scratch Postgres and verifies the migration stamp, key table counts, and
  that the newest `audit_events` row is inside the RPO window. A red run is
  the alert.
- Procedures and infrastructure checklist:
  [`../runbooks/database-backup-restore.md`](../runbooks/database-backup-restore.md).

## Error-budget policy

- Budget intact → normal feature velocity.
- Budget > 50% burned mid-window → reliability work is prioritised in
  planning; risky deploys need explicit sign-off.
- Budget exhausted → feature freeze on the affected service until the
  window resets; only reliability fixes ship.

## Synthetic monitoring

The critical path runs **every 5 minutes** via a GitLab pipeline schedule
(`SCHEDULED_TASK=synthetic`, cron `*/5 * * * *`):

1. `GET /api/v1/health` — expect 200 + `"status":"healthy"`.
2. `GET /api/v1/ping` — expect 200 + `"ping":"pong"`.
3. Frontend root (when `FRONTEND_BASE_URL` is configured) — expect 2xx.

Any non-2xx, unexpected body, or response slower than
`SYNTHETIC_MAX_SECONDS` (default 5 s) fails the job and turns the
scheduled pipeline red. Configure pipeline-failure notifications on that
schedule so a red run pages on-call — that is the alerting channel until
native metrics/alerts land (issue #4).
