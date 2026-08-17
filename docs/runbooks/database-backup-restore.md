# Runbook: database backup & restore (disaster recovery)

Audience: the deployment operator / on-call engineer. Related: issue #70,
`docs/operations/deployment.md` §5.3, `docs/operations/slos.md` (SLO 4).

## Targets

| Objective | Target | Meaning |
|---|---|---|
| **RPO** (max data loss) | **≤ 24 hours** | The nightly backup bounds how much committed data a total loss can destroy. The pre-deploy dump narrows the window further, but only on deploy days. |
| **RTO** (time to restore) | **≤ 4 hours** | From "droplet is gone" to the app serving again on a rebuilt host, following §Restore scenario 1. |

## Backup architecture — two kinds of dump, do not confuse them

| | Pre-deploy dump | Nightly DR backup |
|---|---|---|
| Made by | `deploy/production_release.sh` (during a production deploy) | `deploy/db_backup.sh` (cron, nightly) |
| When | only when a deploy runs | every night, `15 1 * * *` UTC |
| Contains | database only | database **and** `shared/uploads` archive |
| Lives | `/srv/priori/backups/pre-<sha>.dump`, droplet only | `/srv/priori/backups/nightly-<utc>.dump` + offsite copy in the Spaces bucket (`db/`, `uploads/`) |
| Purpose | undo for a bad migration right after a deploy | survival of droplet loss, corruption, accidental deletion |
| Tested | never automatically | **monthly**, by `scheduled:db-restore-verify` |

Retention: newest 14 nightly dumps and 7 uploads archives locally (script
defaults); offsite retention is the **bucket lifecycle policy** (recommended:
expire objects after 35 days — set it on the bucket, not in scripts).

## The monthly restore test

`scheduled:db-restore-verify` (`.gitlab/ci/scheduled-jobs.yml`) runs on a
GitLab pipeline schedule (`SCHEDULED_TASK=restore-verify`, cron `0 3 1 * *`).
It downloads the newest offsite dump with **read-only** bucket credentials,
restores it into a throwaway Postgres service container, and fails the
pipeline unless all of the following hold:

1. `pg_restore --exit-on-error` completes;
2. `alembic_version` carries a migration stamp;
3. key tables (`users`, `customers`, `invoices`, `payments`, `audit_events`)
   are non-empty;
4. the newest `audit_events.created_at` is within `RPO_HOURS` (26) of now —
   which catches both a stalled nightly cron and a stale offsite sync.

A red scheduled pipeline is the alert, exactly like the synthetic monitoring.
Configure pipeline-failure notifications on the schedule. Production
credentials never enter CI: the job holds only the read-only bucket key, and
the scratch database exists only for that pipeline.

## Restore scenarios

All restores are deliberate, data-loss-bearing human decisions — never
automated. Announce before starting; record what was restored and why in the
operations log afterwards.

### 1. Droplet loss (rebuild from offsite)

1. Provision a new droplet; follow `docs/operations/deployment-setup.md` to
   recreate `/srv/priori/{releases,current,shared,backups}`, the deploy user,
   sudoers, nginx, systemd unit, and `shared/.env` (secrets from the password
   manager — they are not in any backup).
2. Fetch the newest artifacts (any machine with the rclone remote configured):
   ```bash
   rclone lsf spaces:priori-crm-backups/db/ | sort | tail -1        # newest dump
   rclone copy spaces:priori-crm-backups/db/nightly-<utc>.dump /srv/priori/backups/
   rclone lsf spaces:priori-crm-backups/uploads/ | sort | tail -1
   rclone copy spaces:priori-crm-backups/uploads/uploads-<utc>.tar.gz /srv/priori/backups/
   ```
3. Restore the database (as the deploy user; `$PG_URL` = the new droplet's
   `DATABASE_URL` without the password — use a `PGPASSFILE`, never argv):
   ```bash
   createdb priori_crm   # or via the postgres superuser, matching .env
   pg_restore --no-owner --no-privileges --exit-on-error -d "$PG_URL" \
     /srv/priori/backups/nightly-<utc>.dump
   ```
4. Restore uploads:
   ```bash
   tar -xzf /srv/priori/backups/uploads-<utc>.tar.gz -C /srv/priori/shared/
   chmod 700 /srv/priori/shared/uploads
   ```
5. Deploy the current `main` via *Deploy production* (`workflow_dispatch`).
   The release script's `alembic upgrade head` brings the restored schema to
   the deployed code's head if the dump predates it.
6. Point DNS at the new droplet; verify `/api/v1/health`, log in, open a
   recent invoice, download one uploaded document.
7. Reinstall the backup cron (checklist below) — the rebuilt droplet has none.

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
   the stop is gone. The window is minutes if caught immediately — this is
   why deploy failures should be acted on at once.

### 3. Accidental data deletion (surgical restore)

Do **not** full-restore for one deleted record set — that would discard
everyone else's writes since the dump. Instead:

1. Restore the newest nightly dump into a scratch database (side-by-side on
   the droplet or anywhere): same `pg_restore` as above into
   `priori_crm_scratch`.
2. Extract only the affected rows, e.g.:
   ```bash
   psql "$SCRATCH_URL" -c "\\copy (SELECT * FROM invoices WHERE id = '<uuid>') TO 'row.csv' CSV HEADER"
   ```
   and reinsert into production with `\copy ... FROM`, respecting FK order
   (parent document → line items → payments).
3. Check `audit_events` (append-only, `entity_type` + `entity_id`) to confirm
   exactly what the deletion removed — it stores `before` snapshots.
4. Drop the scratch database.

## Staging (MochaHost)

Staging data is disposable by policy; no RPO applies. If a safety net is
wanted anyway: a weekly cPanel cron running `pg_dump -Fc` into `~/backups`
(Postgres 13.23 client tools are available on the host) with `find -mtime +28
-delete` retention. Do not point any staging job at the production bucket.

## Infrastructure checklist (outside this repo)

Mirrors `deployment.md` §7 — none of this can live in the tree:

- [ ] Create the Spaces bucket (e.g. `priori-crm-backups`, same region as the
      droplet), **private**, with a lifecycle rule expiring objects after 35
      days.
- [ ] Create two Spaces keys: **read-write** for the droplet, **read-only**
      for CI. Never reuse one for the other.
- [ ] On the droplet: `apt-get install rclone` and configure the remote
      (`rclone config`, name it e.g. `spaces`) for the deploy user.
- [ ] Install the script from a checkout (it is not shipped by the deploy
      workflow, which rsyncs only `api/` and `frontend/dist/`):
      ```bash
      install -D -m 0700 deploy/db_backup.sh /srv/priori/bin/db_backup.sh
      ```
      Re-run after any change to the script.
- [ ] Install the cron entry for the deploy user (01:15 UTC, after the
      00:15 UTC nightly transitions so the dump includes them):
      ```
      15 1 * * * RCLONE_REMOTE=spaces:priori-crm-backups /srv/priori/bin/db_backup.sh >> /srv/priori/backups/backup.log 2>&1
      ```
- [ ] Set the four `BACKUP_S3_*` CI/CD variables (masked) in GitLab —
      Settings > CI/CD > Variables — using the **read-only** key.
- [ ] Create the pipeline schedule: `SCHEDULED_TASK=restore-verify`, cron
      `0 3 1 * *`, on `develop`; enable pipeline-failure notifications.
- [ ] After the first nightly run: confirm the dump appears in the bucket,
      then trigger the schedule manually once and watch it pass end-to-end.
- [ ] Quarterly: walk restore scenario 1 against a throwaway droplet and time
      it against the 4 h RTO.
