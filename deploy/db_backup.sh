#!/usr/bin/env bash
# Nightly database + uploads backup, executed ON the DigitalOcean droplet.
#
# Installed once as /srv/priori/bin/db_backup.sh and driven by cron — see
# docs/runbooks/database-backup-restore.md for the install checklist. This is
# the disaster-recovery backup; the pre-deploy dump taken by
# deploy/production_release.sh is deploy insurance only and is NOT a
# substitute: it runs only when a deploy runs, and it lives on the same
# droplet as the database it protects.
#
# What this script does, in order, failing loudly at each step:
#   1. pg_dump -Fc of the production database -> backups/nightly/nightly-<UTC>.dump
#   2. copy the dump offsite via rclone (RCLONE_REMOTE) IMMEDIATELY — droplet
#      loss must never take the only backups with it, and a later uploads-tar
#      failure must never strand a good dump on the droplet.
#   3. tar.gz of shared/uploads -> backups/uploads-<UTC>.tar.gz + offsite copy.
#      This is an independent failure domain: a tar/copy failure still exits
#      nonzero overall (cron/logs show red) but only AFTER the dump is safe.
#   4. prune local copies (newest DB_RETAIN / UPLOADS_RETAIN kept)
#
# Nightly dumps live in backups/nightly/, NOT directly in backups/:
# deploy/production_release.sh prunes backups/*.dump keeping the newest 14 of
# ALL dumps there, so co-locating the nightly dumps would let them crowd out
# (and evict) the pre-<sha>.dump deploy-insurance dumps within ~two weeks.
# Each retention policy owns its own directory. The offsite layout (db/ and
# uploads/ prefixes in the bucket) is unaffected.
#
# Secret handling follows deploy/production_release.sh exactly: DATABASE_URL
# is parsed out of shared/.env with python-dotenv (the file is dotenv syntax,
# never source it), the password goes into a throwaway 0600 PGPASSFILE (never
# argv — argv is visible in `ps` to every local account), and everything is
# written under umask 077.

set -euo pipefail

ROOT="${ROOT:-/srv/priori}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
# Nightly dumps get their own directory so production_release.sh's
# newest-14-of-*.dump prune (scoped to $BACKUP_DIR) can never evict them, and
# vice versa (see header comment).
NIGHTLY_DIR="${NIGHTLY_DIR:-$BACKUP_DIR/nightly}"
ENV_FILE="${ENV_FILE:-$ROOT/shared/.env}"
UPLOADS_DIR="${UPLOADS_DIR:-$ROOT/shared/uploads}"
# python-dotenv is present in every release venv (pydantic-settings dependency).
VENV_PY="${VENV_PY:-$ROOT/current/venv/bin/python}"
DB_RETAIN="${DB_RETAIN:-14}"          # newest N nightly dumps kept locally
UPLOADS_RETAIN="${UPLOADS_RETAIN:-7}" # newest N uploads archives kept locally
# rclone destination, e.g. "spaces:priori-crm-backups". Offsite retention is
# the bucket's lifecycle policy, not this script (see the runbook).
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
# Set to 1 ONLY while offsite storage is being provisioned. A backup that
# exists only on the droplet does not survive the droplet.
ALLOW_LOCAL_ONLY="${ALLOW_LOCAL_ONLY:-0}"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

[ -f "$ENV_FILE" ] || { log "FATAL: $ENV_FILE missing"; exit 1; }
[ -x "$VENV_PY" ] || { log "FATAL: $VENV_PY not found — is a release deployed?"; exit 1; }
command -v pg_dump >/dev/null || { log "FATAL: pg_dump not found — install postgresql-client"; exit 1; }
if [ -z "$RCLONE_REMOTE" ] && [ "$ALLOW_LOCAL_ONLY" != "1" ]; then
  log "FATAL: RCLONE_REMOTE is not set. A droplet-local backup is not disaster"
  log "       recovery. Set RCLONE_REMOTE (or ALLOW_LOCAL_ONLY=1 during setup)."
  exit 1
fi
if [ -n "$RCLONE_REMOTE" ] && ! command -v rclone >/dev/null; then
  log "FATAL: RCLONE_REMOTE is set but rclone is not installed"
  exit 1
fi

# Dumps and archives hold customer and financial data — never world-readable.
umask 077
mkdir -p "$BACKUP_DIR" "$NIGHTLY_DIR"
chmod 700 "$BACKUP_DIR" "$NIGHTLY_DIR"

# --- extract DATABASE_URL without sourcing .env -------------------------------
# Identical helper to deploy/production_release.sh: parse with the same parser
# the app uses, split the password into PGPASSFILE, normalise the SQLAlchemy
# driver form (postgresql+psycopg2://) to plain postgresql:// for libpq.
PGPASSFILE="$(mktemp)" # mktemp creates 0600 — never widen this
export PGPASSFILE
trap 'rm -f "$PGPASSFILE"' EXIT

PG_URL="$(ENV_FILE="$ENV_FILE" "$VENV_PY" - <<'PY'
import os
from urllib.parse import unquote, urlsplit, urlunsplit

from dotenv import dotenv_values

env_file = os.environ["ENV_FILE"]
url = (dotenv_values(env_file).get("DATABASE_URL") or "").strip()
if not url:
    raise SystemExit(f"DATABASE_URL missing from {env_file}")

scheme, sep, rest = url.partition("://")
if not sep:
    raise SystemExit(f"DATABASE_URL in {env_file} is not a URL")
parts = urlsplit(scheme.partition("+")[0] + "://" + rest)

# pgpass escapes backslash and colon; libpq percent-decodes the URL password,
# so decode before writing. Wildcard fields sidestep host/db matching rules.
password = unquote(parts.password or "")
with open(os.environ["PGPASSFILE"], "w") as fh:
    fh.write("*:*:*:*:" + password.replace("\\", "\\\\").replace(":", "\\:") + "\n")

netloc = parts.hostname or ""
if parts.port:
    netloc = f"{netloc}:{parts.port}"
if parts.username:
    netloc = f"{parts.username}@{netloc}"
print(urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)))
PY
)"
[ -n "$PG_URL" ] || { log "FATAL: could not extract DATABASE_URL from $ENV_FILE"; exit 1; }

STAMP="$(date -u '+%Y%m%d%H%M%S')"

# --- 1. database dump ----------------------------------------------------------
DB_DUMP="$NIGHTLY_DIR/nightly-$STAMP.dump"
log "Dumping database to $DB_DUMP"
# Remove the partial file if pg_dump fails: a truncated dump that restores
# cleanly up to the truncation point is worse than no dump at all.
pg_dump -Fc "$PG_URL" > "$DB_DUMP" || {
  rc=$?
  rm -f "$DB_DUMP"
  log "FATAL: pg_dump failed (exit $rc) — partial dump removed"
  exit "$rc"
}

# --- 2. database dump offsite — IMMEDIATELY, before anything can fail ----------
# The dump is the artifact this script exists to protect; it must be safely
# offsite before the uploads archive gets a chance to fail. (A live upload
# racing the tar makes GNU tar exit nonzero — that must never strand a
# perfectly good dump on the droplet until the freshness alert fires.)
if [ -n "$RCLONE_REMOTE" ]; then
  log "Copying database dump to $RCLONE_REMOTE"
  rclone copyto "$DB_DUMP" "$RCLONE_REMOTE/db/$(basename "$DB_DUMP")" || {
    log "FATAL: offsite copy of the database dump failed"
    exit 1
  }
else
  log "WARNING: local-only backup (ALLOW_LOCAL_ONLY=1) — this does NOT survive droplet loss"
fi

# --- 3. uploads archive — an independent failure domain -------------------------
# STORAGE_BACKEND=local keeps proof-of-payment documents on disk; they are as
# unrecoverable as the database if the droplet is lost.
#
# A failure here must NOT abort the script (the dump is already offsite), but
# it must still make the overall run exit nonzero so cron/logs show red.
#
# GNU tar exit codes: 1 means "some files differ" — the warning class,
# including 'file changed as we read it' when a live upload races the 01:15
# run. The archive is still written and every file in it is complete; the
# racing file is simply as-of-read. Treat 1 as loud-but-archived. Exit codes
# >= 2 are fatal errors — remove the partial archive and mark the run failed.
UPLOADS_TAR=""
UPLOADS_FAILED=0
if [ -d "$UPLOADS_DIR" ]; then
  UPLOADS_TAR="$BACKUP_DIR/uploads-$STAMP.tar.gz"
  log "Archiving uploads to $UPLOADS_TAR"
  tar_rc=0
  tar -czf "$UPLOADS_TAR" -C "$(dirname "$UPLOADS_DIR")" "$(basename "$UPLOADS_DIR")" || tar_rc=$?
  if [ "$tar_rc" -eq 1 ]; then
    log "WARNING: tar exited 1 ('file changed as we read it' class) — a live"
    log "         upload raced the archive. The archive is complete and kept;"
    log "         the racing file is captured as-of-read."
  elif [ "$tar_rc" -ge 2 ]; then
    rm -f "$UPLOADS_TAR"
    UPLOADS_TAR=""
    UPLOADS_FAILED=1
    log "ERROR: uploads tar failed (exit $tar_rc) — partial archive removed."
    log "       Continuing: the database dump is already offsite; this run"
    log "       will still exit nonzero."
  fi
else
  log "WARNING: $UPLOADS_DIR does not exist — skipping uploads archive"
fi

if [ -n "$UPLOADS_TAR" ] && [ -n "$RCLONE_REMOTE" ]; then
  log "Copying uploads archive to $RCLONE_REMOTE"
  rclone copyto "$UPLOADS_TAR" "$RCLONE_REMOTE/uploads/$(basename "$UPLOADS_TAR")" || {
    UPLOADS_FAILED=1
    log "ERROR: offsite copy of the uploads archive failed — the complete"
    log "       local archive is kept; this run will still exit nonzero."
  }
fi

# --- 4. local retention ----------------------------------------------------------
# Filenames are nightly-<UTC>.dump / uploads-<UTC>.tar.gz by construction — no
# whitespace or control characters — so the ls|tail|xargs pipelines are safe.
# Pruning is best-effort and must not fail the backup under pipefail.
# shellcheck disable=SC2012 # info: ls-vs-find; fixed-format names, and find has no portable -t sort
ls -1t "$NIGHTLY_DIR"/nightly-*.dump 2>/dev/null | tail -n +"$((DB_RETAIN + 1))" | xargs -r rm -f -- || true
# shellcheck disable=SC2012 # info: same reasoning as above
ls -1t "$BACKUP_DIR"/uploads-*.tar.gz 2>/dev/null | tail -n +"$((UPLOADS_RETAIN + 1))" | xargs -r rm -f -- || true

if [ "$UPLOADS_FAILED" -ne 0 ]; then
  log "BACKUP PARTIAL: database dump completed${RCLONE_REMOTE:+ and copied offsite} ($DB_DUMP),"
  log "                but the uploads archive tier FAILED — investigate before the next run."
  exit 1
fi
log "Backup complete: $DB_DUMP${UPLOADS_TAR:+ + $UPLOADS_TAR}"
