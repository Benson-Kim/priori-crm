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

### 4. Data durability — **RPO ≤ 5 min (WAL archiving), RTO ≤ 4 h**
- **RPO** (max data loss) — primary tier: **≤ 5 minutes**, bounded by
  continuous WAL archiving (pgBackRest `archive-push`, `archive_timeout=60`
  — see ADR-0013) plus weekly full / daily differential physical backups.
- **RPO** — fallback tier: ≤ 24 h via the nightly `pg_dump` + uploads
  archive (`deploy/db_backup.sh`, age-encrypted client-side with its own
  escrowed key, copied offsite). This tier is kept
  deliberately: it is the independent recovery path if the physical chain is
  unusable (page corruption carried forward, lost repo-cipher passphrase),
  and it is version-portable. Uploads additionally get an hourly offsite
  mirror (`deploy/uploads_sync.sh`), bounding document loss at ~1 h.
- **RTO**: ≤ 4 hours from total droplet loss to serving again, following the
  runbook; rehearsed by a quarterly timed DR drill.
- **SLIs**:
  - `scheduled:backup-freshness` (dead-man's switch) fails red if the
    newest archived WAL segment (> 60 min), physical backup
    (full > 8 d / diff > 2 d), nightly dump (> 26 h), uploads archive
    (> 26 h), or hourly-sync heartbeat (> 120 min) is stale. The WAL check
    runs **hourly** (`backup-freshness-wal` schedule), so a stalled WAL
    archive is detected within **~2 h** (hourly cadence + 60-min budget);
    the remaining checks run **daily**, so their worst-case detection
    latency is **~1 day**. The ≤ 5 min RPO is a *design* property that
    holds while archiving works — what is *monitored* is that a broken
    archiver cannot stay silent for more than ~2 h;
  - the monthly `scheduled:db-restore-verify` run restores the newest
    offsite dump into a scratch Postgres and verifies the migration stamp,
    key table counts, `audit_events` freshness, referential spot-checks, and
    index validity. Its `RPO_HOURS=26` deliberately reflects the **nightly
    tier's** cadence (24 h + scheduling slack), not the 5-minute WAL RPO —
    WAL freshness is the hourly freshness run's 60-minute check. A red run
    is the alert, in both cases.
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
