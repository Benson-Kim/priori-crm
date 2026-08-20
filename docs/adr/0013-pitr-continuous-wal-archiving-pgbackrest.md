# ADR-0013: Point-in-time recovery via continuous WAL archiving with pgBackRest

- **Status:** Proposed
- **Date:** 2026-08-17
- **Deciders:** Engineering
- **Related:** ADR-0007 (reliability & backups), issue #70, MR !74,
  `docs/runbooks/database-backup-restore.md`, `docs/operations/slos.md` (SLO 4)

## Context

MR !74 gave the system its first real disaster-recovery posture: a nightly
`pg_dump` + uploads archive copied offsite, a monthly automated restore test,
and a runbook. That bounded the RPO at **24 hours** — everything written since
last night's dump is lost in a total failure.

That was acceptable as a first step. It is not acceptable going forward: this
is a financial ledger (invoices, payments, an append-only `audit_events`
trail) about to get wider usage. A day of lost payments is not an
inconvenience, it is unreconcilable books. The gap is structural, not
operational — no dump cadence fixes it. Dumping hourly still loses up to an
hour and multiplies load; only **continuous WAL archiving** turns the RPO from
"since the last dump" into "since the last archived WAL segment", i.e.
minutes.

PostgreSQL 16 is self-hosted on the production droplet (no managed-database
PITR to lean on), and the offsite target is a DigitalOcean Spaces bucket
(S3-compatible) already provisioned for the nightly dumps. The tooling
question is which archiving/backup manager to standardise on.

## Decision

We adopt **pgBackRest** for continuous WAL archiving, scheduled physical
backups (weekly full + daily differential), and point-in-time recovery,
targeting **RPO ≤ 5 minutes**. The repository is the existing Spaces bucket
(`repo1-type=s3`), client-side encrypted (`repo1-cipher-type=aes-256-cbc`),
with `repo1-retention-full=2` (two full sets; expiring a full also expires its
dependent differentials and WAL).

The nightly `pg_dump` from MR !74 is **kept, repositioned as the secondary
logical tier**, not deleted:

- a logical dump cannot silently carry forward physical page corruption the
  way a physical backup can — it is the independent recovery path if the
  physical chain is ever poisoned or unreadable (including loss of the
  repo-cipher passphrase);
- it is version-portable (`pg_restore` into any newer PostgreSQL), which the
  physical backups are not;
- it is what the monthly CI restore test and surgical row-extraction
  procedures already exercise.

Why pgBackRest over the alternatives:

| | pgBackRest | WAL-G | barman |
|---|---|---|---|
| Install on Ubuntu | `apt install pgbackrest` (distro/PGDG package) | GitHub release binary — no distro package; our sandbox/host policy also blocks github.com downloads | distro package, but designed around a dedicated backup server |
| Full + differential backups | native, one tool | full + delta, retention handling thinner | native |
| Client-side encryption at rest | built in (`repo1-cipher-type`) | via envelope encryption (libsodium), more configuration surface | not built in (relies on storage-side) |
| Integrated retention/expiry incl. WAL | `repo1-retention-full` handles backups **and** dependent WAL atomically | manual `delete` invocations to compose | native |
| Self-verification | `check` (config + archiving round-trip) and `verify` (repo checksums) built in | no equivalent of `check` | `barman check` |
| S3-compatible endpoints (Spaces) | first-class | first-class | via cloud provider scripts |
| Operational fit here | single droplet, no extra host, cron-driven | fine | wants a backup host we do not have |

The decider is operational: pgBackRest is one apt-installable tool that does
archiving, scheduled backups, retention, encryption, and self-checking with a
single 0600 config file — the smallest surface for a one-droplet team to
operate correctly.

## What it does today

Nothing is live yet; this ADR ships alongside the templates and runbook that
make it installable:

- `deploy/pgbackrest.conf.template` — the repo/stanza config (S3 repo,
  cipher, retention), installed as `/etc/pgbackrest/pgbackrest.conf` (0600,
  `postgres:postgres`).
- `deploy/postgresql-archiving.conf.template` — the exact `postgresql.conf`
  directives: `archive_mode=on`, `wal_level=replica`,
  `archive_command='pgbackrest --stanza=priori archive-push %p'`,
  `archive_timeout=60`.
- Cron lines (runbook bring-up checklist): weekly full, daily differential,
  daily `pgbackrest check`, weekly `pgbackrest verify`.
- `scheduled:backup-freshness` (`.gitlab/ci/scheduled-jobs.yml`) — the daily
  dead-man's switch that goes red if the newest archived WAL segment, physical
  backup, nightly dump, or uploads artifact is stale.
- Restore procedures — PITR walkthrough, droplet-loss rebuild, quarterly
  drill — in `docs/runbooks/database-backup-restore.md`.

## Business logic & rules

- **RPO ≤ 5 minutes.** `archive_timeout=60` forces a WAL segment switch after
  at most 60 s of idle, so even a quiet ledger has its last minute of writes
  offsite within moments; under load segments rotate faster. Worst-case loss
  is the not-yet-archived tail of the current segment — minutes, not a day.
- **Two independent recovery paths, always.** Physical (pgBackRest) is
  primary; logical (nightly `pg_dump`) is the fallback. Neither may be
  decommissioned while the other is the only remaining path.
- **Backups must not be destroyable from the host they protect** —
  anti-tamper posture (bucket versioning, key separation, off-droplet admin
  key) is part of this decision, detailed in the runbook.
- **The repo-cipher passphrase is a single point of total loss**: without it
  every physical backup and WAL segment is unreadable. It must exist in at
  least two secure locations (password manager + the droplet's 0600 config),
  and its loss is treated as a sev-1 requiring an immediate new full backup
  chain under a new passphrase.
- **Untested restores are not backups** (ADR-0007): PITR is rehearsed
  quarterly against the 4 h RTO; the logical tier stays under the monthly
  automated restore test.

## Consequences

- (+) Worst-case data loss drops from 24 h to minutes; accidental-deletion
  recovery becomes surgical (`restore --type=time` to one minute before the
  mistake) instead of "lose everyone's day".
- (+) Defense in depth: physical + logical tiers fail differently.
- (+) `pgbackrest check`/`verify` + the CI dead-man's switch make a silently
  stalled archiver a same-day red pipeline instead of a discovery at restore
  time.
- (−) Enabling `archive_mode=on` requires a PostgreSQL **restart** (one brief
  maintenance window).
- (−) A stalled archive (bucket unreachable, credentials broken) makes WAL
  accumulate in `pg_wal` until archiving resumes — disk must be monitored;
  the freshness job catches the stall within its daily window and
  `archive_timeout=60` adds only trivial padding to WAL volume.
- (−) More storage than dumps alone (2 full sets + diffs + WAL), bounded by
  `repo1-retention-full=2`; the bucket lifecycle rules must **exclude** the
  `pgbackrest/` prefix — pgBackRest owns its own expiry, and an external
  lifecycle deletion corrupts the repo.
- (−) Physical backups are not version-portable; major-version upgrades
  require a new stanza/full backup chain (documented in the runbook).

## Improvements

- Consider a second repository (`repo2`) in a different region/provider once
  usage justifies it — pgBackRest supports multi-repo natively.
- Native metrics/alerting (issue #4) should eventually replace "red scheduled
  pipeline" as the alerting channel for freshness.
- If the droplet outgrows itself, pgBackRest's stanza model extends to a
  standby/replica (`wal_level=replica` is already the right setting).

## Resilience & <1s response rules

- Archiving is asynchronous to transactions — `archive_command` never sits on
  the commit path, so client-visible latency is unaffected.
- Scheduled backups run in the 00:30–01:15 UTC window (before/alongside the
  nightly dump), with `process-max` kept low so backup compression never
  starves the API of CPU.
- `start-fast=y` bounds backup start latency; `delta=y` keeps rehearsal
  restores fast enough to drill quarterly without ceremony.
