#!/usr/bin/env bash
# Hourly uploads sync to the offsite bucket, executed ON the droplet.
#
# Installed once as /srv/priori/bin/uploads_sync.sh and driven by cron (see
# docs/runbooks/database-backup-restore.md for the install checklist).
#
# Why this exists: the nightly uploads tar in deploy/db_backup.sh has the
# same 24-hour exposure the nightly pg_dump had — a proof-of-payment document
# uploaded at 09:00 and lost at 18:00 was in no backup at all. This script
# mirrors /srv/priori/shared/uploads to the bucket every hour, so droplet
# loss costs at most ~1 hour of documents. The nightly tar stays as the
# point-in-time archive; this mirror is the freshness tier.
#
# Deletions propagate (rclone sync mirrors the source), which is exactly why
# the bucket MUST have versioning enabled (runbook, anti-tamper section): a
# deleted or overwritten file remains recoverable as a prior object version.
# --max-delete additionally refuses a mass deletion in one run — a wiped or
# unmounted uploads directory must fail this script loudly, not silently
# erase the mirror's current versions.
#
# Conventions follow deploy/db_backup.sh: set -euo pipefail, timestamped
# log(), loud failures, configuration via environment. No database
# credentials are involved; rclone reads the remote's key from the deploy
# user's rclone config, never argv.

set -euo pipefail

ROOT="${ROOT:-/srv/priori}"
UPLOADS_DIR="${UPLOADS_DIR:-$ROOT/shared/uploads}"
# rclone destination bucket root, e.g. "spaces:priori-crm-backups" — the same
# value db_backup.sh uses. The mirror lives under <remote>/uploads-sync/ so
# it never collides with the nightly tars under <remote>/uploads/.
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
# Abort (exit non-zero, delete nothing further) if a single run would delete
# more than this many remote files. Raise deliberately for a known clean-up.
MAX_DELETE="${MAX_DELETE:-200}"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

[ -n "$RCLONE_REMOTE" ] || { log "FATAL: RCLONE_REMOTE is not set (e.g. spaces:priori-crm-backups)"; exit 1; }
command -v rclone >/dev/null || { log "FATAL: rclone is not installed"; exit 1; }
# A missing source directory must never be treated as "everything was
# deleted" — refuse instead of propagating an unmounted/renamed directory.
[ -d "$UPLOADS_DIR" ] || { log "FATAL: $UPLOADS_DIR does not exist — refusing to sync"; exit 1; }

DEST="$RCLONE_REMOTE/uploads-sync"
log "Syncing $UPLOADS_DIR -> $DEST (max-delete $MAX_DELETE)"
rclone sync "$UPLOADS_DIR" "$DEST" \
  --max-delete "$MAX_DELETE" \
  --transfers 4 --checkers 8 \
  --stats-one-line --stats 0 || {
  rc=$?
  log "FATAL: rclone sync failed (exit $rc) — the offsite uploads mirror is stale"
  exit "$rc"
}

# Freshness heartbeat for the dead-man's switch (scheduled:backup-freshness).
# Uploads may legitimately not change for days and rclone preserves source
# mtimes, so the mirror's own timestamps cannot signal "the sync is running".
# This marker's mtime can: it is touched only after a successful sync. It
# lives OUTSIDE uploads-sync/ because sync would delete any file not present
# in the source.
rclone touch "$RCLONE_REMOTE/heartbeats/uploads-sync" || {
  rc=$?
  log "FATAL: could not update the uploads-sync heartbeat (exit $rc)"
  exit "$rc"
}

log "Uploads sync complete"
