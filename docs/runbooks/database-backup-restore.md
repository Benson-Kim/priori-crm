# Runbook: database backup & restore (disaster recovery)

Audience: the deployment operator / on-call engineer. Related: issue #70,
MR !74, `docs/operations/deployment.md` §5.3, `docs/operations/slos.md` (SLO 4),
`docs/adr/0013-pitr-continuous-wal-archiving-pgbackrest.md` (why pgBackRest).

## Targets

| Objective | Target | Meaning |
|---|---|---|
| **RPO** (max data loss) — primary | **≤ 5 minutes** | Continuous WAL archiving (pgBackRest, `archive_timeout=60`) keeps the ledger's write-ahead log flowing offsite; worst-case loss is the un-archived tail of the current segment. |
| **RPO** — fallback tier | ≤ 24 hours | If the physical chain is ever unusable (lost cipher passphrase, poisoned repo), the nightly `pg_dump` bounds the loss instead. |
| **RTO** (time to restore) | **≤ 4 hours** | From "droplet is gone" to the app serving again on a rebuilt host, following §Restore scenario 1. Rehearsed quarterly (§Quarterly DR drill). |

## Backup architecture — the tiers, do not confuse them

### Database: three tiers

| | Tier 1: pgBackRest (physical, PITR) | Tier 2: nightly DR dump (logical) | Tier 3: pre-deploy dump |
|---|---|---|---|
| Made by | `archive_command` (continuous) + cron full/diff backups | `deploy/db_backup.sh` (cron, nightly) | `deploy/production_release.sh` (during a production deploy) |
| When | WAL: continuously, ≤ 60 s idle lag. Full: weekly. Diff: daily | every night, `15 1 * * *` UTC | only when a deploy runs |
| Contains | exact physical cluster + every WAL segment → restore to **any second** in the retention window | database snapshot (also: `shared/uploads` tar) | database only |
| Lives | Spaces bucket, `pgbackrest/` prefix, **AES-256-CBC encrypted client-side** | droplet `backups/` + bucket `db/`, `uploads/` | `/srv/priori/backups/pre-<sha>.dump`, droplet only |
| Purpose | primary recovery: droplet loss, corruption, accidental deletion — with minutes of loss | independent fallback path; version-portable restores; source for surgical row extraction | undo for a bad migration right after a deploy |
| Tested | daily `pgbackrest check` + daily CI freshness + quarterly drill | **monthly**, by `scheduled:db-restore-verify` + daily CI freshness | never automatically |

**Tier 2 is not redundant with Tier 1 — never decommission it.** A physical
backup faithfully copies whatever is on disk, including silently corrupted
pages, and it is only readable by the same major version with the same
cipher passphrase. The logical dump reads rows through the SQL layer (it
cannot carry page corruption forward), restores into any newer PostgreSQL,
and does not depend on the pgBackRest passphrase. The two tiers fail
differently — that is the point.

### Uploads: two tiers

Proof-of-payment documents (`STORAGE_BACKEND=local`) are ledger evidence and
get the same treatment:

| | Hourly mirror | Nightly archive |
|---|---|---|
| Made by | `deploy/uploads_sync.sh` (cron, hourly): `rclone sync` to bucket `uploads-sync/` | `deploy/db_backup.sh`: tar to bucket `uploads/` |
| Exposure | ≤ ~1 hour | ≤ 24 hours |
| Purpose | freshness — droplet loss costs at most an hour of documents | point-in-time archive |
| Deletion safety | deletions propagate — **bucket versioning is required** so any deleted/overwritten file is recoverable as a prior version; `--max-delete` refuses mass deletions | tars are immutable snapshots |

Retention: newest 14 nightly dumps / 7 uploads tars locally (script
defaults); bucket lifecycle expires `db/` and `uploads/` objects after 35
days. pgBackRest manages its own retention (`repo1-retention-full=2` ≈ two
weeks of PITR range) — the lifecycle rules **must not touch the
`pgbackrest/` prefix** (external deletion corrupts the repo) nor
`uploads-sync/` or `heartbeats/`.

## Tier 1: continuous WAL archiving + physical backups (pgBackRest)

Config lives in two committed templates — fill placeholders on the droplet,
never commit filled copies:

- [`deploy/pgbackrest.conf.template`](../../deploy/pgbackrest.conf.template)
  → `/etc/pgbackrest/pgbackrest.conf` (0600, `postgres:postgres`). S3 repo =
  the Spaces bucket under `pgbackrest/`, `repo1-cipher-type=aes-256-cbc`,
  `repo1-retention-full=2`.
- [`deploy/postgresql-archiving.conf.template`](../../deploy/postgresql-archiving.conf.template)
  → `/etc/postgresql/16/main/conf.d/archiving.conf`:

  ```ini
  wal_level = replica
  archive_mode = on          # requires a postgres RESTART
  archive_command = 'pgbackrest --stanza=priori archive-push %p'
  archive_timeout = 60       # idle periods still ship WAL every 60 s
  ```

**RPO math.** A WAL segment is pushed when it fills or after at most 60 s of
idle (`archive_timeout`). Worst case lost on total droplet loss: the
un-archived tail of the current segment — normally well under a minute,
bounded in minutes even under pathological push latency. Target: **≤ 5 min**
(SLO 4). If the bucket becomes unreachable, PostgreSQL retains WAL in
`pg_wal` until archiving resumes — no data-loss exposure grows silently, but
**disk does**: the daily `pgbackrest check` and the CI freshness job turn a
stalled archive red the same day.

**Cron — `postgres` user's crontab** (`sudo -u postgres crontab -e`); the
00:30 slot finishes before the 01:15 nightly dump:

```cron
# weekly full backup — Sunday 00:30 UTC
30 0 * * 0    pgbackrest --stanza=priori --type=full backup
# daily differential — Monday–Saturday 00:30 UTC
30 0 * * 1-6  pgbackrest --stanza=priori --type=diff backup
# daily self-check: config sane, archiving round-trip works, repo reachable
15 6 * * *    pgbackrest --stanza=priori check
# weekly deep verification of repository checksums
45 6 * * 0    pgbackrest --stanza=priori verify
```

(pgBackRest writes its own logs to `/var/log/pgbackrest`; cron failures also
surface through the CI freshness job below.)

**The cipher passphrase is a single point of total loss.** Everything under
`pgbackrest/` is encrypted client-side with `repo1-cipher-pass`. Lose it and
every physical backup and WAL segment is permanently unreadable — the
nightly dump becomes the only recovery path. It must exist in **at least two
secure locations**: the password manager (canonical) and the droplet's 0600
config. It must never appear in CI variables, the repo, or on argv.

## Monitoring — a failed backup is discovered in hours, not at restore time

Three layers, all alerting through the established channel (a red scheduled
pipeline pages on-call):

1. **`scheduled:backup-freshness`** (daily, `.gitlab/ci/scheduled-jobs.yml`)
   — the dead-man's switch. Lists the bucket with **read-only** credentials
   and fails red if any tier is stale:
   - newest archived WAL segment older than 60 min (`WAL_MAX_AGE_MIN`);
   - newest pgBackRest **full** older than 8 days;
   - newest pgBackRest full/**diff** older than 2 days;
   - newest `nightly-*.dump` older than 26 h;
   - newest `uploads-*.tar.gz` older than 26 h;
   - uploads-sync heartbeat (`heartbeats/uploads-sync`, touched only after a
     successful hourly sync) older than 120 min.

   Schedule setup (CI/CD > Schedules): `SCHEDULED_TASK=backup-freshness`,
   cron `0 7 * * *`, on `develop`, pipeline-failure notifications on. It
   reuses the four `BACKUP_S3_*` variables — no new credentials. During
   bring-up, set `CHECK_PGBACKREST=0` / `CHECK_UPLOADS_SYNC=0` on the
   schedule until those tiers are live, then **remove the overrides** — a
   permanently red schedule trains everyone to ignore it.

2. **`scheduled:db-restore-verify`** (monthly, cron `0 3 1 * *`,
   `SCHEDULED_TASK=restore-verify`) — proves the logical tier actually
   restores. Downloads the newest offsite dump (read-only key), restores
   into a throwaway Postgres service, and fails unless **all** of:
   1. `pg_restore --exit-on-error` completes;
   2. `alembic_version` carries a migration stamp;
   3. key tables (`users`, `customers`, `invoices`, `payments`,
      `audit_events`) are non-empty;
   4. newest `audit_events.created_at` is within `RPO_HOURS` (26) of now —
      26 h is the **nightly-dump cadence plus slack**, deliberately not the
      5-minute WAL RPO: this job verifies Tier 2; Tier 1 freshness is the
      daily job's 60-minute WAL check;
   5. referential spot-checks pass: every `invoices.customer_id` and
      `payments.invoice_id` resolves, and `amount_paid =
      SUM(payments.amount)` on a 50-invoice sample;
   6. `ANALYZE` completes and a `users.email` lookup uses an index
      (constraint/index validity).

   Each check reports independently, then a summary table, then one
   pass/fail (`deploy/db_restore_verify.sh`).

3. **On-droplet**: daily `pgbackrest check` (archiving round-trip), weekly
   `pgbackrest verify` (repo checksums). Watch `pg_wal` disk usage if the
   archive ever stalls.

Production credentials never enter CI: the jobs hold only the read-only
bucket key, and the scratch database exists only for that pipeline.

## Anti-tamper / ransomware posture

Threat model: **a compromised droplet must not be able to destroy its own
backups.** The droplet necessarily holds credentials that write to the
bucket (archive-push, backups, sync) — so the protection cannot be "the
droplet has no bucket access"; it is that nothing the droplet's key can do
is irreversible for the recovery window.

- [ ] **Bucket versioning ON — required, not optional.** Every overwrite or
      delete leaves the prior generation recoverable as a non-current
      version. This is what makes the hourly `rclone sync` (whose deletions
      propagate) and a ransomware-style purge survivable.
- [ ] **Lifecycle: permanently delete non-current versions after 14 days.**
      Deleting an object only writes a short-lived delete marker; the data
      outlives the attack by 14 days — the recovery window. (Current-object
      expiry of 35 days applies to `db/` and `uploads/` only — never to
      `pgbackrest/`.)
- [ ] **Key separation, least privilege per holder:**

      | Key | Scope | Held where |
      |---|---|---|
      | Droplet key | per-bucket read-write | droplet only (`pgbackrest.conf` 0600, deploy user's rclone config 0600) |
      | CI key | per-bucket **read-only** | GitLab masked CI/CD variables (`BACKUP_S3_*`) |
      | Admin key | full access incl. purging versions, bucket settings | **password manager only — never on the droplet, never in CI** |

      Ideally the droplet key would be write-only-with-no-delete, but Spaces
      per-bucket keys cannot express that, and pgBackRest legitimately needs
      delete for its own retention expiry. The **compensating control** is
      the pair above: versioning + 14-day non-current retention means the
      droplet key can only create delete markers it cannot purge, and the
      only key that can purge versions (or disable versioning) lives
      off-droplet. Recovery from a tampered bucket = list and restore object
      versions using the admin key from a clean machine.
- [ ] **The repo-cipher passphrase exists in exactly two places** — password
      manager + droplet 0600 config (see Tier 1 above). Losing it makes every
      physical backup unreadable; leaking it only matters together with
      bucket read access. Two locations minimum, zero copies anywhere else.
- [ ] **`audit_events` is append-only** (docs/database.md §4) and every
      backup tier preserves it — PITR can therefore also reconstruct *what*
      an attacker or mistake changed, not just undo it.

## Which backup do I restore from? — decision table

| Failure mode | Restore from | Why |
|---|---|---|
| Bad migration right after a deploy | Tier 3: `pre-<sha>.dump` (scenario 2) | taken immediately before exactly that migration; smallest loss window |
| Accidental deletion / bad bulk update at a known time | Tier 1: PITR to T−1 min on a **scratch** cluster + surgical reinsert (scenario 0) | keeps everyone else's writes; a full restore would discard them |
| Physical/page corruption detected | Tier 1: full backup from **before** the corruption + PITR stopping just before it | physical restore is exact; if corruption predates all retained fulls, fall back to the newest clean nightly dump — logical dumps read rows and cannot carry page corruption |
| Droplet loss | Tier 1: latest full + diff + replay of **all** archived WAL (scenario 1) | minutes of loss instead of 24 h; Tier 2 is the fallback path in the same scenario |
| pgBackRest repo unusable (lost passphrase, poisoned repo) | Tier 2: newest `nightly-*.dump` | the independent tier — this is why it exists |
| Uploaded document deleted/overwritten | prior object version in `uploads-sync/` (bucket versioning); else the nightly `uploads-*.tar.gz` | versioning keeps every generation for 14 days after deletion |
| Bucket tampered with using the droplet's key | object versions, restored with the **admin key** from a clean machine | droplet key cannot purge versions |

All restores are deliberate, data-loss-bearing human decisions — never
automated. Announce before starting; record what was restored and why in the
operations log afterwards. Restored scratch instances contain real financial
data — tear them down when done.

## Restore scenarios

### 0. PITR — accidental deletion (walkthrough: "rows deleted at 14:37")

Someone deleted a set of invoices at **14:37 UTC**. Do **not** restore
production wholesale — writes since 14:37 are legitimate. Recover the
14:36 state on a scratch cluster, extract, reinsert.

```bash
# 1. Scratch data directory on the droplet (needs free disk ≈ database size;
#    use a throwaway droplet in the same region if space is tight)
sudo -u postgres mkdir -m 700 /var/lib/postgresql/16/scratch

# 2. Restore to one minute before the damage. pgBackRest picks the right
#    full+diff automatically and replays WAL up to the target.
sudo -u postgres pgbackrest --stanza=priori restore \
  --pg1-path=/var/lib/postgresql/16/scratch \
  --type=time --target='2026-08-17 14:36:00+00' \
  --target-action=promote

# 3. Start the scratch cluster on a side port, loopback only, and with
#    archiving OFF — it must never push its WAL into the production repo.
sudo -u postgres /usr/lib/postgresql/16/bin/pg_ctl \
  -D /var/lib/postgresql/16/scratch \
  -o "-p 5433 -c listen_addresses=127.0.0.1 -c archive_mode=off" start

# 4. Recovery replays to the target, then promotes (--target-action=promote).
#    Confirm it is out of recovery and frozen at 14:36:
sudo -u postgres psql -p 5433 -d priori_crm -Atc 'SELECT pg_is_in_recovery()'   # → f
sudo -u postgres psql -p 5433 -d priori_crm -Atc 'SELECT max(created_at) FROM audit_events'  # ≤ 14:36
```

Extract and reinsert (FK order: parent document → line items → payments):

```bash
sudo -u postgres psql -p 5433 -d priori_crm \
  -c "\copy (SELECT * FROM invoices WHERE id = '<uuid>') TO 'invoice.csv' CSV HEADER"
# ...then \copy ... FROM into production with a PGPASSFILE, never argv.
```

Cross-check against `audit_events` (append-only; `before` snapshots) to
confirm the reinserted rows match exactly what the deletion removed. Tear
down:

```bash
sudo -u postgres /usr/lib/postgresql/16/bin/pg_ctl -D /var/lib/postgresql/16/scratch stop
sudo -u postgres rm -rf /var/lib/postgresql/16/scratch
```

### 1. Droplet loss (rebuild from offsite)

1. Provision a new droplet; follow `docs/operations/deployment-setup.md` to
   recreate `/srv/priori/{releases,current,shared,backups}`, the deploy
   user, sudoers, nginx, systemd unit, and `shared/.env` (secrets from the
   password manager — they are not in any backup).
2. Reinstall PostgreSQL 16 + pgBackRest and both config files (Bring-up
   checklist steps 3–5); the bucket key and **cipher passphrase** come from
   the password manager.
3. **Primary path — physical restore with WAL replay** (loses only the last
   un-archived seconds/minutes):
   ```bash
   sudo systemctl stop postgresql
   sudo -u postgres find /var/lib/postgresql/16/main -mindepth 1 -delete
   sudo -u postgres pgbackrest --stanza=priori restore     # latest full+diff, replays all archived WAL
   sudo systemctl start postgresql                          # watch the log until recovery completes
   sudo -u postgres pgbackrest --stanza=priori check
   sudo -u postgres pgbackrest --stanza=priori --type=full backup   # new chain on the new timeline — do this immediately
   ```
4. **Fallback path — logical restore** (if the repo is unusable; accepts up
   to 24 h loss). As the deploy user; `$PG_URL` without the password — use a
   `PGPASSFILE`, never argv:
   ```bash
   rclone lsf spaces:priori-crm-backups/db/ | sort | tail -1
   rclone copy spaces:priori-crm-backups/db/nightly-<utc>.dump /srv/priori/backups/
   createdb priori_crm
   pg_restore --no-owner --no-privileges --exit-on-error -d "$PG_URL" \
     /srv/priori/backups/nightly-<utc>.dump
   ```
5. Restore uploads — the hourly mirror is fresher than the nightly tar:
   ```bash
   rclone sync spaces:priori-crm-backups/uploads-sync /srv/priori/shared/uploads
   chmod 700 /srv/priori/shared/uploads
   # fallback: tar -xzf the newest uploads-<utc>.tar.gz from uploads/
   ```
6. Deploy the current `main` via *Deploy production* (`workflow_dispatch`).
   The release script's `alembic upgrade head` brings the restored schema to
   the deployed code's head if the backup predates it.
7. Point DNS at the new droplet; verify `/api/v1/health`, log in, open a
   recent invoice, download one uploaded document.
8. Re-run the Bring-up checklist from step 7 (cron entries) — the rebuilt
   droplet has none — and confirm the next `scheduled:backup-freshness` run
   is green end-to-end.

### 2. Bad migration right after a deploy

Code rollback first: run `rollback-production.yml` with the previous SHA
(symlink swap, seconds). Only if the OLD code cannot run against the NEW
schema (expand/contract was violated) do a data restore:

1. `sudo systemctl stop priori-api` — no writes during restore.
2. The pre-deploy dump is the right artifact: `pre-<failed-sha>.dump` was
   taken immediately before that deploy's migration.
3. Restore into a **fresh** database and swing over, rather than dropping in
   place — it preserves the broken state for diagnosis:
   ```bash
   createdb priori_crm_restore
   pg_restore --no-owner --no-privileges --exit-on-error \
     -d "<PG_URL for priori_crm_restore>" /srv/priori/backups/pre-<sha>.dump
   # point DATABASE_URL in shared/.env at priori_crm_restore
   ```
4. `sudo systemctl start priori-api`; verify health; rename/clean databases
   once stable.
5. **Understand what was lost**: every write between the pre-deploy dump and
   the stop is gone. If that window matters, scenario 0's PITR-to-scratch
   can recover the missing minutes surgically — the WAL archive covers them.

### 3. Accidental data deletion (surgical restore)

Scenario 0 **is** the procedure — PITR to one minute before the mistake is
strictly better than the nearest nightly dump (which can be up to 24 h
stale). Use the nightly dump for extraction only when pgBackRest is
unavailable: restore it into a scratch database (same `pg_restore` as
scenario 1 step 4, into `priori_crm_scratch`), extract with `\copy`,
reinsert in FK order, verify against `audit_events`, drop the scratch
database.

## Quarterly DR drill — timed, pass/fail

Do not let the first full restore be during an incident. Once a quarter
(first week), one engineer runs the drill **timed end-to-end**; alternate
between the two variants so both paths stay rehearsed:

- **Variant A (full rebuild):** scenario 1 on a throwaway droplet, through
  step 7 (app serving, document downloadable).
- **Variant B (PITR surgical):** scenario 0 against a scratch cluster —
  pick an arbitrary target time from yesterday, recover, extract one
  invoice + its payments, verify against `audit_events`.

Checklist (record every timestamp in the ops log):

- [ ] T₀ noted; drill announced (so a red freshness run mid-drill is not
      mistaken for an incident).
- [ ] Restore completed; for Variant A: `/api/v1/health` healthy, login
      works, a recent invoice opens, one uploaded document downloads.
- [ ] Freshness of restored data measured: `SELECT max(created_at) FROM
      audit_events` vs. the drill start — Variant A must be within the
      5-minute RPO of T₀; Variant B must be ≤ the requested target time.
- [ ] `deploy/db_restore_verify.sh` run against the restored database
      (Variant A) — all checks pass.
- [ ] T_final noted; throwaway droplet/scratch cluster **destroyed** (it
      holds real financial data).
- [ ] Ops-log entry written: date, operator, variant, artifacts used, elapsed
      time, anything that surprised.

**Pass:** every box ticked **and** elapsed (T_final − T₀) **≤ 4 h** for
Variant A (Variant B budget: 1 h). **Fail:** anything missed — file an issue
the same day, fix the gap before the next quarter; two consecutive failed
drills mean the RTO in SLO 4 is fiction and must be re-negotiated or the
gaps fixed with priority.

## Staging (MochaHost)

Staging data is disposable by policy; no RPO applies. If a safety net is
wanted anyway: a weekly cPanel cron running `pg_dump -Fc` into `~/backups`
(Postgres 13.23 client tools are available on the host) with `find -mtime +28
-delete` retention. Do not point any staging job at the production bucket,
and do not install pgBackRest there — shared cPanel offers neither the
access nor a reason.

## Bring-up checklist — single ordered sequence (outside this repo)

Everything below is infrastructure work on the bucket, the droplet, and
GitLab settings; none of it can live in the tree. Do it **in this order** —
later steps verify earlier ones.

1. [ ] **Bucket**: create the Spaces bucket (e.g. `priori-crm-backups`, same
       region as the droplet), **private**; **enable versioning** (required —
       anti-tamper section); lifecycle rules:
       - expire current objects under `db/` and `uploads/` after 35 days;
       - permanently delete non-current versions after 14 days (bucket-wide);
       - **no rule may touch `pgbackrest/`** (it manages its own retention;
         external deletion corrupts the repo), nor `uploads-sync/` or
         `heartbeats/`.
2. [ ] **Keys & secrets**: three Spaces keys — droplet **read-write**
       (per-bucket), CI **read-only** (per-bucket), admin **full-access**
       stored only in the password manager. Generate the repo-cipher
       passphrase (`openssl rand -base64 48`) and store it in the password
       manager **before** it ever touches the droplet.
3. [ ] **Droplet packages**: `apt-get install pgbackrest rclone
       postgresql-client`; `install -d -o postgres -g postgres -m 750
       /var/log/pgbackrest`. Configure the deploy user's rclone remote
       (`rclone config`, name `spaces`) with the droplet key.
4. [ ] **pgBackRest config**: fill `deploy/pgbackrest.conf.template` →
       `/etc/pgbackrest/pgbackrest.conf`, `postgres:postgres` 0600 (bucket
       key + cipher passphrase from step 2).
5. [ ] **PostgreSQL archiving**: install
       `deploy/postgresql-archiving.conf.template` →
       `/etc/postgresql/16/main/conf.d/archiving.conf`. **Restart required**
       for `archive_mode=on` — announce a brief window (connections drop for
       seconds; the API reconnects): `sudo systemctl restart postgresql`.
6. [ ] **Stanza + round-trip check**:
       ```bash
       sudo -u postgres pgbackrest --stanza=priori stanza-create
       sudo -u postgres pgbackrest --stanza=priori check   # proves archive-push works end-to-end
       ```
7. [ ] **First full backup**: `sudo -u postgres pgbackrest --stanza=priori
       --type=full backup`; confirm with `pgbackrest info` and by listing
       `pgbackrest/backup/priori/` in the bucket.
8. [ ] **Scripts + cron**: from a checkout (the deploy workflow rsyncs only
       `api/` and `frontend/dist/` — re-run after any script change):
       ```bash
       install -D -m 0700 deploy/db_backup.sh    /srv/priori/bin/db_backup.sh
       install -D -m 0700 deploy/uploads_sync.sh /srv/priori/bin/uploads_sync.sh
       ```
       Deploy user's crontab:
       ```cron
       # nightly DB dump + uploads tar (after the 00:15 UTC nightly transitions)
       15 1 * * * RCLONE_REMOTE=spaces:priori-crm-backups /srv/priori/bin/db_backup.sh >> /srv/priori/backups/backup.log 2>&1
       # hourly uploads mirror
       5 * * * *  RCLONE_REMOTE=spaces:priori-crm-backups /srv/priori/bin/uploads_sync.sh >> /srv/priori/backups/uploads-sync.log 2>&1
       ```
       Postgres user's crontab: the four pgBackRest lines from §Tier 1.
9. [ ] **CI variables**: the four `BACKUP_S3_*` variables (masked, Settings >
       CI/CD > Variables) using the **read-only** key from step 2.
10. [ ] **Pipeline schedules** (CI/CD > Schedules, on `develop`,
        pipeline-failure notifications on):
        - `SCHEDULED_TASK=restore-verify`, cron `0 3 1 * *` (monthly);
        - `SCHEDULED_TASK=backup-freshness`, cron `0 7 * * *` (daily). If
          created before steps 6–8 are done, set `CHECK_PGBACKREST=0` /
          `CHECK_UPLOADS_SYNC=0` on the schedule and **remove the overrides
          once live**.
11. [ ] **First-backup verification**: after one nightly cron cycle, trigger
        the `backup-freshness` schedule manually — every check green, no
        SKIPPED rows remaining.
12. [ ] **First restore test**: trigger the `restore-verify` schedule
        manually and watch it pass end-to-end; then run drill Variant B
        (PITR to a scratch cluster) once, timed. Record both in the ops log.
13. [ ] Quarterly thereafter: the DR drill (§above), alternating variants.
