#!/usr/bin/env bash
# Staging release steps, executed ON the MochaHost cPanel account.
#
# Piped over ssh by .github/workflows/deploy-staging.yml *after* rsync has
# already placed the new SPA bundle and API source. Kept in the repo rather
# than inlined in YAML so it is reviewable and can be run by hand during an
# incident. See docs/operations/deployment.md §4.6.
#
# Assumes the cPanel Python App exists (app root ~/apps/priori-api, Application
# URL /api, Python 3.12) and that ~/apps/priori-api/.env carries the staging
# settings — notably API_V1_PREFIX=/v1, which the Passenger mount requires.

set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/apps/priori-api}"
VENV_ACTIVATE="${VENV_ACTIVATE:-$HOME/virtualenv/apps/priori-api/3.12/bin/activate}"

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

[ -f "$VENV_ACTIVATE" ] || {
  log "FATAL: venv activate script not found at $VENV_ACTIVATE"
  log "Create the app under cPanel > Setup Python App first (see §7)."
  exit 1
}

# shellcheck disable=SC1090
source "$VENV_ACTIVATE"
cd "$APP_DIR"

# The .env is host-managed and excluded from rsync; a missing one means the app
# boots on defaults and fails confusingly at the first request instead of here.
[ -f "$APP_DIR/.env" ] || { log "FATAL: $APP_DIR/.env is missing"; exit 1; }

log "Installing dependencies"
pip install --quiet --upgrade -r requirements.txt

# Expand/contract discipline applies here too: the migration runs while the
# OLD code keeps serving until the Passenger restart marker below is touched
# (and Passenger reloads lazily, on the next request). Migrations must
# therefore be backward-compatible with the previous release — add nullable
# columns first, drop old ones a release later. See deployment.md §5.3.
log "Running migrations"
alembic upgrade head

# Passenger watches this file's mtime and reloads the app on change. Without it
# the old code keeps serving and the deploy looks successful but changes nothing.
log "Restarting Passenger"
mkdir -p tmp
touch tmp/restart.txt

log "Staging release complete"
