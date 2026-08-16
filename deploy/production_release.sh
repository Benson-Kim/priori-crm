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

mkdir -p "$ROOT/backups"
BACKUP="$ROOT/backups/pre-$CI_COMMIT_SHA.dump"
log "Backing up to $BACKUP"
pg_dump -Fc "$PG_URL" > "$BACKUP"

# --- migrate -----------------------------------------------------------------
# Its own step. If this fails, `set -e` aborts here: the symlink still points at
# the old release and the service was never touched.
log "Running migrations"
cd "$REL/api"
"$REL/venv/bin/alembic" upgrade head

# --- cut over ----------------------------------------------------------------
# systemd resolves WorkingDirectory at start, and nginx's root is
# $ROOT/current/frontend/dist, so this one symlink swaps API and SPA together.
log "Switching current -> $REL"
ln -sfn "$REL" "$ROOT/current"
sudo /usr/bin/systemctl restart "$SERVICE"
sudo /usr/bin/systemctl reload nginx

# --- verify, or put it back --------------------------------------------------
for i in $(seq 1 30); do
  if curl -fsS --max-time 10 "$HEALTH_URL" | grep -q '"status":"healthy"'; then
    log "deploy ok: $CI_COMMIT_SHA (healthy after ${i} attempt(s))"
    exit 0
  fi
  sleep 2
done

log "health check FAILED at $HEALTH_URL"
if [ -n "$PREV" ] && [ -d "$PREV" ]; then
  log "rolling back to $PREV"
  ln -sfn "$PREV" "$ROOT/current"
  sudo /usr/bin/systemctl restart "$SERVICE"
  log "rolled back — NOTE: the migration above was NOT reverted (§5.3)."
  log "If the old code cannot run against the new schema, restore $BACKUP by hand."
else
  log "no previous release to roll back to — service left on $REL"
fi
exit 1
