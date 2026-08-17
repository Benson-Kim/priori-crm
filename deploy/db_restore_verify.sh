#!/usr/bin/env bash
# Restore a production dump into a DISPOSABLE Postgres and verify it.
#
# Used by the monthly scheduled restore test (scheduled:db-restore-verify in
# .gitlab/ci/scheduled-jobs.yml) and runnable by hand during a DR drill or an
# incident. See docs/runbooks/database-backup-restore.md.
#
# A backup that has never been restored is a hope, not a backup. This script
# proves that a dump actually restores and contains recent, plausible data.
#
# Usage:
#   SCRATCH_DB_URL='postgresql://user@host:5432/db' PGPASSWORD='...' \
#     db_restore_verify.sh <dump-file>
#
# Environment:
#   SCRATCH_DB_URL        (required) a DISPOSABLE, EMPTY Postgres database.
#                         Never point this at production — the restore writes
#                         the full schema and data into it. Keep the password
#                         out of the URL (argv is visible in `ps`); pass it
#                         via PGPASSWORD or PGPASSFILE instead.
#   RPO_HOURS             freshness budget for the newest audit_events row
#                         (default 26: nightly cadence plus scheduling slack).
#   REQUIRED_NONEMPTY     space-separated tables that must have > 0 rows.
#   EXPECTED_ALEMBIC_REV  optional; when set, the restored alembic_version
#                         must equal it exactly.

set -euo pipefail

DUMP_FILE="${1:?usage: db_restore_verify.sh <dump-file>}"
: "${SCRATCH_DB_URL:?SCRATCH_DB_URL must point at a disposable Postgres}"
RPO_HOURS="${RPO_HOURS:-26}"
REQUIRED_NONEMPTY="${REQUIRED_NONEMPTY:-users customers invoices payments audit_events}"
EXPECTED_ALEMBIC_REV="${EXPECTED_ALEMBIC_REV:-}"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
q() { psql "$SCRATCH_DB_URL" -X -A -t -v ON_ERROR_STOP=1 -c "$1"; }

command -v pg_restore >/dev/null || { log "FATAL: pg_restore not found"; exit 1; }
command -v psql >/dev/null || { log "FATAL: psql not found"; exit 1; }
[ -s "$DUMP_FILE" ] || { log "FATAL: $DUMP_FILE is missing or empty"; exit 1; }

# Refuse to restore into a database that already has tables — a mixed-up URL
# must fail HERE, before pg_restore writes anything.
existing="$(q "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"
if [ "$existing" != "0" ]; then
  log "FATAL: target database already has $existing tables in schema public — refusing."
  log "       SCRATCH_DB_URL must be a fresh, disposable database."
  exit 1
fi

log "Restoring $DUMP_FILE ($(wc -c < "$DUMP_FILE") bytes)"
# --no-owner/--no-privileges: the scratch role differs from production's.
# --exit-on-error: a partial restore must be a red run, not a warning.
pg_restore --no-owner --no-privileges --exit-on-error -d "$SCRATCH_DB_URL" "$DUMP_FILE"
log "Restore completed"

fail=0

# --- 1. migration stamp -------------------------------------------------------
rev="$(q "SELECT version_num FROM alembic_version")" || rev=""
if [ -z "$rev" ]; then
  log "FAIL: alembic_version is missing or empty — the dump has no migration stamp"
  fail=1
else
  log "OK: alembic revision $rev"
  if [ -n "$EXPECTED_ALEMBIC_REV" ] && [ "$rev" != "$EXPECTED_ALEMBIC_REV" ]; then
    log "FAIL: expected alembic revision $EXPECTED_ALEMBIC_REV, dump has $rev"
    fail=1
  fi
fi

# --- 2. plausible row counts -------------------------------------------------------
for t in $REQUIRED_NONEMPTY; do
  if ! n="$(q "SELECT count(*) FROM \"$t\"")"; then
    log "FAIL: table $t is missing from the restored dump"
    fail=1
    continue
  fi
  if [ "$n" = "0" ]; then
    log "FAIL: table $t restored empty"
    fail=1
  else
    log "OK: $t has $n rows"
  fi
done

# --- 3. freshness: the newest audit row must be inside the RPO window ---------
# Every financially significant mutation writes an audit_events row in the
# same transaction (docs/database.md §4), so its max(created_at) is a good
# proxy for "how recent is this backup".
age_hours="$(q "SELECT COALESCE(floor(extract(epoch FROM (now() - max(created_at))) / 3600)::text, '') FROM audit_events")" || age_hours=""
if [ -z "$age_hours" ]; then
  log "FAIL: could not compute audit_events freshness (table missing or empty)"
  fail=1
elif [ "$age_hours" -gt "$RPO_HOURS" ]; then
  log "FAIL: newest audit_events row is ${age_hours}h old — outside the ${RPO_HOURS}h RPO window."
  log "      Either the nightly backup is not running, or the offsite sync is stale."
  fail=1
else
  log "OK: newest audit_events row is ${age_hours}h old (budget ${RPO_HOURS}h)"
fi

if [ "$fail" -ne 0 ]; then
  log "RESTORE VERIFICATION FAILED"
  exit 1
fi
log "RESTORE VERIFICATION PASSED"
