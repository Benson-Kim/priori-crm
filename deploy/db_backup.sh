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
#   1. pg_dump -Fc | age -> backups/nightly-<UTC>.dump.age
#   2. tar.gz | age of shared/uploads -> backups/uploads-<UTC>.tar.gz.age
#   3. prune local copies (newest DB_RETAIN / UPLOADS_RETAIN kept)
#   4. copy both artifacts offsite via rclone (BACKUP_BUCKET_REMOTE) —
#      droplet loss must never take the only backups with it.
#
# Client-side encryption (age, asymmetric): dumps and tars hold the full
# financial ledger + PII, and bucket ACLs alone must not be what stands
# between them and disclosure (a leaked bucket key or a bucket-policy
# mistake would otherwise expose plaintext). The droplet holds ONLY the age
# PUBLIC recipient(s) in AGE_RECIPIENTS_FILE; the PRIVATE identity that
# decrypts is escrowed off-droplet (password manager, canonical) and — for
# the monthly CI restore test — in the masked BACKUP_AGE_IDENTITY variable.
# This key is deliberately SEPARATE from the pgBackRest repo-cipher
# passphrase: the two tiers must not share a single point of loss.
#
# Secret handling follows deploy/production_release.sh exactly: DATABASE_URL
# is parsed out of shared/.env with python-dotenv (the file is dotenv syntax,
# never source it), the password goes into a throwaway 0600 PGPASSFILE (never
# argv — argv is visible in `ps` to every local account), and everything is
# written under umask 077. The age recipients file contains only public
# keys — nothing secret ever appears on argv or in this script's output.

set -euo pipefail

ROOT="${ROOT:-/srv/priori}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
ENV_FILE="${ENV_FILE:-$ROOT/shared/.env}"
UPLOADS_DIR="${UPLOADS_DIR:-$ROOT/shared/uploads}"
# python-dotenv is present in every release venv (pydantic-settings dependency).
VENV_PY="${VENV_PY:-$ROOT/current/venv/bin/python}"
DB_RETAIN="${DB_RETAIN:-14}"          # newest N nightly dumps kept locally
UPLOADS_RETAIN="${UPLOADS_RETAIN:-7}" # newest N uploads archives kept locally
# rclone destination, e.g. "spaces:priori-crm-backups". Offsite retention is
# the bucket's lifecycle policy, not this script (see the runbook).
#
# Deliberately NOT named RCLONE_REMOTE (#85): rclone parses RCLONE_*
# environment variables as its own configuration. There is no global
# --remote flag today, so rclone happens to ignore that name — but any
# future rclone release adding one would silently reinterpret our value as
# rclone's own setting (exactly what happened with RCLONE_CRYPT_REMOTE /
# --crypt-remote in #82).
BACKUP_BUCKET_REMOTE="${BACKUP_BUCKET_REMOTE:-}"
# age PUBLIC recipient(s), one per line (from `age-keygen`; the matching
# private identity lives in the password manager, never on the droplet).
AGE_RECIPIENTS_FILE="${AGE_RECIPIENTS_FILE:-/etc/priori/backup-age-recipients.txt}"
# Set to 1 ONLY while offsite storage is being provisioned. A backup that
# exists only on the droplet does not survive the droplet.
ALLOW_LOCAL_ONLY="${ALLOW_LOCAL_ONLY:-0}"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

[ -f "$ENV_FILE" ] || { log "FATAL: $ENV_FILE missing"; exit 1; }
[ -x "$VENV_PY" ] || { log "FATAL: $VENV_PY not found — is a release deployed?"; exit 1; }
command -v pg_dump >/dev/null || { log "FATAL: pg_dump not found — install postgresql-client"; exit 1; }
if [ -z "$BACKUP_BUCKET_REMOTE" ] && [ "$ALLOW_LOCAL_ONLY" != "1" ]; then
  log "FATAL: BACKUP_BUCKET_REMOTE is not set. A droplet-local backup is not disaster"
  log "       recovery. Set BACKUP_BUCKET_REMOTE (or ALLOW_LOCAL_ONLY=1 during setup)."
  exit 1
fi
if [ -n "$BACKUP_BUCKET_REMOTE" ] && ! command -v rclone >/dev/null; then
  log "FATAL: BACKUP_BUCKET_REMOTE is set but rclone is not installed"
  exit 1
fi
command -v age >/dev/null || { log "FATAL: age not found — install age (backups are client-side encrypted)"; exit 1; }
if [ ! -s "$AGE_RECIPIENTS_FILE" ]; then
  log "FATAL: $AGE_RECIPIENTS_FILE is missing or empty. Backups MUST be"
  log "       client-side encrypted — install the age PUBLIC recipient(s)"
  log "       there (bring-up checklist; the private identity stays in the"
  log "       password manager, never on the droplet)."
  exit 1
fi

# Dumps and archives hold customer and financial data — never world-readable.
umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

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
DB_DUMP="$BACKUP_DIR/nightly-$STAMP.dump.age"
log "Dumping database (encrypted) to $DB_DUMP"
# Remove the partial file if any pipe stage fails: a truncated dump that
# restores cleanly up to the truncation point is worse than no dump at all.
# pipefail makes a pg_dump failure fail the whole pipeline even though age
# is the last stage.
pg_dump -Fc "$PG_URL" | age -e -R "$AGE_RECIPIENTS_FILE" > "$DB_DUMP" || {
  rc=$?
  rm -f "$DB_DUMP"
  log "FATAL: pg_dump | age failed (exit $rc) — partial dump removed"
  exit "$rc"
}

# --- 2. uploads archive ----------------------------------------------------------
# STORAGE_BACKEND=local keeps proof-of-payment documents on disk; they are as
# unrecoverable as the database if the droplet is lost.
UPLOADS_TAR=""
if [ -d "$UPLOADS_DIR" ]; then
  UPLOADS_TAR="$BACKUP_DIR/uploads-$STAMP.tar.gz.age"
  log "Archiving uploads (encrypted) to $UPLOADS_TAR"
  tar -cz -C "$(dirname "$UPLOADS_DIR")" "$(basename "$UPLOADS_DIR")" \
    | age -e -R "$AGE_RECIPIENTS_FILE" > "$UPLOADS_TAR" || {
    rc=$?
    rm -f "$UPLOADS_TAR"
    log "FATAL: uploads tar | age failed (exit $rc) — partial archive removed"
    exit "$rc"
  }
else
  log "WARNING: $UPLOADS_DIR does not exist — skipping uploads archive"
fi

# --- 3. local retention ----------------------------------------------------------
# Filenames are nightly-<UTC>.dump.age / uploads-<UTC>.tar.gz.age by
# construction — no whitespace or control characters — so the ls|tail|xargs
# pipelines are safe.
# Pruning is best-effort and must not fail the backup under pipefail.
# shellcheck disable=SC2012 # info: ls-vs-find; fixed-format names, and find has no portable -t sort
ls -1t "$BACKUP_DIR"/nightly-*.dump.age 2>/dev/null | tail -n +"$((DB_RETAIN + 1))" | xargs -r rm -f -- || true
# shellcheck disable=SC2012 # info: same reasoning as above
ls -1t "$BACKUP_DIR"/uploads-*.tar.gz.age 2>/dev/null | tail -n +"$((UPLOADS_RETAIN + 1))" | xargs -r rm -f -- || true

# --- 4. offsite copy ----------------------------------------------------------
if [ -n "$BACKUP_BUCKET_REMOTE" ]; then
  log "Copying to $BACKUP_BUCKET_REMOTE"
  rclone copyto "$DB_DUMP" "$BACKUP_BUCKET_REMOTE/db/$(basename "$DB_DUMP")" || {
    log "FATAL: offsite copy of the database dump failed"
    exit 1
  }
  if [ -n "$UPLOADS_TAR" ]; then
    rclone copyto "$UPLOADS_TAR" "$BACKUP_BUCKET_REMOTE/uploads/$(basename "$UPLOADS_TAR")" || {
      log "FATAL: offsite copy of the uploads archive failed"
      exit 1
    }
  fi
else
  log "WARNING: local-only backup (ALLOW_LOCAL_ONLY=1) — this does NOT survive droplet loss"
fi

log "Backup complete: $DB_DUMP${UPLOADS_TAR:+ + $UPLOADS_TAR}"
