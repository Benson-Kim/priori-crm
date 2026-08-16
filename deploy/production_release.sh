#!/usr/bin/env bash
# Production release steps, executed ON the DigitalOcean droplet.
#
# Piped over ssh by .github/workflows/deploy-production.yml after rsync has
# placed the new release under /srv/priori/releases/$CI_COMMIT_SHA.
# See docs/operations/deployment.md §5.
#
# The ordering is the whole point: back up, migrate, and fail loudly BEFORE
# anything user-facing changes. A failed migration must present as a failed
# deploy with production still serving the old release — not as a crash-looping
# service (which is what `alembic upgrade head && uvicorn` in the Dockerfile
# would give us).

set -euo pipefail

: "${CI_COMMIT_SHA:?CI_COMMIT_SHA must be set by the caller}"

ROOT="${ROOT:-/srv/priori}"
REL="$ROOT/releases/$CI_COMMIT_SHA"
SERVICE="${SERVICE:-priori-api}"
HEALTH_URL="${HEALTH_URL:-https://accounting.priori.co.ke/api/v1/health}"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

PREV="$(readlink -f "$ROOT/current" 2>/dev/null || true)"
log "previous release: ${PREV:-<none>}"
log "new release:      $REL"

[ -d "$REL/api" ] || { log "FATAL: $REL/api missing — rsync did not land"; exit 1; }

if [ -n "$PREV" ] && [ "$PREV" = "$REL" ]; then
  # Redeploying the live SHA is allowed (e.g. after a host-side fix), but be
  # loud about the consequence: there is no distinct release to fall back to.
  log "WARNING: $CI_COMMIT_SHA is already the live release — this redeploy has"
  log "         NO distinct rollback target; a failed health check cannot roll back."
fi

# --- shared state ------------------------------------------------------------
# Secrets and user uploads live outside the release so they survive rollback.
ln -sfn "$ROOT/shared/.env" "$REL/api/.env"
ln -sfn "$ROOT/shared/uploads" "$REL/api/uploads"

# --- per-release venv --------------------------------------------------------
# Rebuilt per release so a rollback restores the dependency set too, not just
# the source.
log "Building venv"
python3.12 -m venv "$REL/venv"
"$REL/venv/bin/pip" install --quiet --upgrade pip
"$REL/venv/bin/pip" install --quiet -r "$REL/api/requirements.txt"

# --- backup ------------------------------------------------------------------
# Read DATABASE_URL from the shared .env WITHOUT sourcing it. The file is
# python-dotenv format (pydantic-settings loads it in the app), which is NOT
# shell: the documented template value `APP_NAME=Business Central` is a valid
# dotenv line, but sourced under `set -e` it assigns APP_NAME=Business and then
# executes `Central` as a command, aborting every deploy. Other legal dotenv
# values can be misparsed or even executed the same way. The per-release venv
# already contains python-dotenv (a dependency of pydantic-settings), so parse
# the file with the same parser the app uses.
# This is also why .env must be readable by the deploy user (deploy:deploy
# 0600), not root-only — see §5.1.
#
# The database password must never appear in pg_dump's argv — argv is visible
# in `ps` to every local account. The helper below splits the password out
# into a throwaway pgpass file (0600, wildcard match) consumed via PGPASSFILE,
# and prints a password-free libpq URL. It also normalises the SQLAlchemy
# driver form (postgresql+psycopg2://), which libpq rejects but config.py
# accepts (api/app/lib/config.py:135).
PGPASSFILE="$(mktemp)" # mktemp creates 0600 — never widen this
export PGPASSFILE
trap 'rm -f "$PGPASSFILE"' EXIT

PG_URL="$(ENV_FILE="$ROOT/shared/.env" "$REL/venv/bin/python" - <<'PY'
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
[ -n "$PG_URL" ] || { log "FATAL: could not extract DATABASE_URL from $ROOT/shared/.env"; exit 1; }

command -v pg_dump >/dev/null || {
  log "FATAL: pg_dump not found — install postgresql-client"
  exit 1
}

# The dump holds customer and financial data — never world-readable. umask 077
# makes the file 0600 at creation (no chmod-after-write exposure window), and
# the directory itself is locked to the deploy user.
umask 077
mkdir -p "$ROOT/backups"
chmod 700 "$ROOT/backups"

BACKUP="$ROOT/backups/pre-$CI_COMMIT_SHA.dump"
if [ -e "$BACKUP" ]; then
  # Redeploying a SHA must not overwrite the dump taken before its FIRST
  # deploy — that one reflects the pre-migration schema and is the valuable
  # copy. Timestamp the new file instead.
  BACKUP="$ROOT/backups/pre-$CI_COMMIT_SHA-$(date -u '+%Y%m%d%H%M%S').dump"
  log "pre-$CI_COMMIT_SHA.dump already exists — writing $BACKUP instead"
fi

log "Backing up to $BACKUP"
# Remove the partial file if pg_dump fails: a truncated dump that restores
# cleanly up to the truncation point is worse than no dump at all.
pg_dump -Fc "$PG_URL" > "$BACKUP" || {
  rc=$?
  rm -f "$BACKUP"
  log "FATAL: pg_dump failed (exit $rc) — partial dump removed"
  exit "$rc"
}

# Retention: keep the newest 14 dumps so backups/ cannot grow without bound
# (each dump is a full database copy). 14 comfortably covers the release
# cadence here while keeping the restore window wide; adjust deliberately if
# that changes. Dump filenames contain no whitespace by construction, so the
# ls|tail|xargs pipeline is safe. `|| true`: pruning is best-effort and must
# not fail the deploy under pipefail.
# shellcheck disable=SC2012 # info: ls-vs-find; names are pre-$SHA[-timestamp].dump, no whitespace/control chars, and find has no portable -t sort
ls -1t "$ROOT/backups"/*.dump 2>/dev/null | tail -n +15 | xargs -r rm -f -- || true

# --- migrate -----------------------------------------------------------------
# Its own step. If this fails, `set -e` aborts here: the symlink still points at
# the old release and the service was never touched.
log "Running migrations"
cd "$REL/api"
"$REL/venv/bin/alembic" upgrade head

# --- cut over, verify, or put it back -----------------------------------------
# systemd resolves WorkingDirectory at start, and nginx's root is
# $ROOT/current/frontend/dist, so this one symlink swaps API and SPA together.
#
# The ENTIRE cutover — symlink swap, restart, reload, health check — feeds ONE
# rollback path. Calling the function in an `if` suspends errexit inside it,
# so a nonzero `systemctl restart` or `nginx reload` cannot abort the script
# after the symlink swap but before the rollback — which would leave
# production pointed at a release that never came up.
cutover() {
  # shellcheck disable=SC2015 # info: A&&B||C is intended — the || handler must fire when ANY cutover command fails; not if-then-else here
  ln -sfn "$REL" "$ROOT/current" &&
    sudo /usr/bin/systemctl restart "$SERVICE" &&
    sudo /usr/bin/systemctl reload nginx || {
    log "cutover command failed (symlink/restart/reload)"
    return 1
  }
  for i in $(seq 1 30); do
    if curl -fsS --max-time 10 "$HEALTH_URL" | grep -q '"status":"healthy"'; then
      log "deploy ok: $CI_COMMIT_SHA (healthy after ${i} attempt(s))"
      return 0
    fi
    sleep 2
  done
  log "health check FAILED at $HEALTH_URL"
  return 1
}

log "Switching current -> $REL"
if cutover; then
  exit 0
fi

# --- roll back ----------------------------------------------------------------
if [ -n "$PREV" ] && [ -d "$PREV" ] && [ "$PREV" != "$REL" ]; then
  log "rolling back to $PREV"
  ln -sfn "$PREV" "$ROOT/current" || { log "FATAL: could not repoint current at $PREV"; exit 1; }
  sudo /usr/bin/systemctl restart "$SERVICE" || log "WARNING: restart failed during rollback — check $SERVICE by hand"
  sudo /usr/bin/systemctl reload nginx || log "WARNING: nginx reload failed during rollback"
  log "rolled back — NOTE: the migration above was NOT reverted (§5.3)."
  log "If the old code cannot run against the new schema, restore $BACKUP by hand."
else
  log "no previous (distinct) release to roll back to — service left on $REL"
fi
exit 1
