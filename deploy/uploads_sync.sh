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
# Client-side encryption (rclone crypt, #82): proof-of-payment documents are
# ledger evidence + PII, and bucket ACLs alone must not be what stands
# between them and disclosure — the same reasoning that age-encrypts the
# nightly artifacts in deploy/db_backup.sh. The sync destination is a CRYPT
# remote (UPLOADS_CRYPT_REMOTE) layered over <bucket>/uploads-sync: names and
# contents are encrypted before anything leaves the droplet, while the
# literal uploads-sync/ prefix stays plaintext in the bucket so the
# lifecycle exclusions and the heartbeats/ separation keep working. Crypt's
# file-name encryption is deterministic and mtimes pass through, so delta
# syncs and per-object bucket versioning behave exactly as for plaintext
# (runbook, "Hourly mirror encryption"). Unlike age there is no public-key
# mode: the crypt password necessarily lives on the droplet (deploy user's
# rclone config, 0600) with the escrowed copy in the password manager — see
# the runbook's anti-tamper section for the residual risk this leaves.
#
# Deletions propagate (rclone sync mirrors the source), which is exactly why
# the bucket MUST have versioning enabled (runbook, anti-tamper section): a
# deleted or overwritten file remains recoverable as a prior object version
# (names are encrypted — the runbook documents how to map a file name to its
# bucket object key with `rclone backend encode` when recovering a version).
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
# rclone bucket root, e.g. "spaces:priori-crm-backups" — the same value
# db_backup.sh uses. Used here ONLY for the freshness heartbeat, which lives
# OUTSIDE uploads-sync/ (see below): the heartbeat is timing metadata, not
# document content, and the dead-man's switch reads it with the CI key that
# must never hold the crypt password.
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
# rclone CRYPT remote wrapping <bucket>/uploads-sync, e.g.
# "spaces-crypt-uploads:" (rclone config: type=crypt,
# remote=spaces:priori-crm-backups/uploads-sync, filename_encryption=
# standard, directory_name_encryption=true). The mirror is synced THROUGH
# this remote so it is encrypted client-side; it must wrap the path INSIDE
# uploads-sync/ so the prefix itself stays plaintext (lifecycle rules and
# the heartbeat separation depend on the literal prefix).
#
# Deliberately NOT named RCLONE_CRYPT_REMOTE: rclone parses RCLONE_*
# environment variables as its own configuration — RCLONE_CRYPT_REMOTE is
# the env form of --crypt-remote and would override the crypt remote's
# `remote` setting to point at itself (verified: rclone then aborts with
# "can't point crypt remote at itself").
UPLOADS_CRYPT_REMOTE="${UPLOADS_CRYPT_REMOTE:-}"
# Abort (exit non-zero, delete nothing further) if a single run would delete
# more than this many remote files. Raise deliberately for a known clean-up.
MAX_DELETE="${MAX_DELETE:-200}"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

[ -n "$RCLONE_REMOTE" ] || { log "FATAL: RCLONE_REMOTE is not set (e.g. spaces:priori-crm-backups)"; exit 1; }
[ -n "$UPLOADS_CRYPT_REMOTE" ] || { log "FATAL: UPLOADS_CRYPT_REMOTE is not set (e.g. spaces-crypt-uploads: — a crypt remote over <bucket>/uploads-sync)"; exit 1; }
command -v rclone >/dev/null || { log "FATAL: rclone is not installed"; exit 1; }
# A missing source directory must never be treated as "everything was
# deleted" — refuse instead of propagating an unmounted/renamed directory.
[ -d "$UPLOADS_DIR" ] || { log "FATAL: $UPLOADS_DIR does not exist — refusing to sync"; exit 1; }

# Refuse to sync unless the destination really is a crypt remote: only the
# crypt backend implements the `encode` command (a purely local computation,
# no API call), so this fails loudly if the remote was (re)configured as a
# plain one — a misconfiguration must not silently mirror plaintext
# documents into the bucket.
rclone backend encode "$UPLOADS_CRYPT_REMOTE" probe >/dev/null 2>&1 || {
  log "FATAL: $UPLOADS_CRYPT_REMOTE is not an rclone crypt remote — refusing to sync plaintext"
  exit 1
}

log "Syncing $UPLOADS_DIR -> $UPLOADS_CRYPT_REMOTE (encrypted; max-delete $MAX_DELETE)"
rclone sync "$UPLOADS_DIR" "$UPLOADS_CRYPT_REMOTE" \
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
