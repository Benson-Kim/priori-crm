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
# Public health URL, probed FROM THIS HOST after the restart marker is touched.
# Overridable so an incident run can point elsewhere; empty skips the check.
HEALTH_URL="${HEALTH_URL:-https://staging.crm.priori.co.ke/api/v1/health}"

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

# Pre-flight: import the actual Passenger entrypoint in the venv, with the
# host .env in scope (cwd is $APP_DIR, so pydantic-settings reads it). This
# catches a missing dependency, an invalid .env, or a broken app import HERE,
# with a real traceback in the deploy log — instead of as a Passenger worker
# that hangs or 500s with nothing to go on. See deployment.md §4.1.
log "Pre-flight: importing passenger_wsgi (validates deps + .env + app import)"
python -c "import passenger_wsgi" || {
  log "FATAL: the Passenger entrypoint failed to import — see traceback above."
  log "Common causes: missing dependency, invalid .env value, bad DATABASE_URL."
  exit 1
}

# Passenger watches this file's mtime and reloads the app on change. Without it
# the old code keeps serving and the deploy looks successful but changes nothing.
log "Restarting Passenger"
mkdir -p tmp
touch tmp/restart.txt

# Warm-up + on-host health check. Passenger reloads lazily on the next
# request, so make that request from here: it (a) forces the app to boot
# while we can still watch it, (b) fails the release BEFORE the SPA cutover
# ships a new frontend against a dead API, and (c) tells a hung/broken app
# apart from a network problem between the GitHub runner and this host —
# if this passes and the workflow's smoke test still times out, the problem
# is on the network path, not in the app.
if [ -z "$HEALTH_URL" ]; then
  log "HEALTH_URL is empty — skipping the on-host health check"
elif ! command -v curl >/dev/null 2>&1; then
  log "WARNING: curl not available on this host — skipping the on-host health check"
else
  log "Warming up Passenger and checking $HEALTH_URL from this host"
  attempts=12
  healthy=0
  for attempt in $(seq 1 "$attempts"); do
    rm -f "/tmp/priori-health-body.$$" "/tmp/priori-health-err.$$"  # no stale body
    curl_rc=0
    http_code="$(curl -sS -o /tmp/priori-health-body.$$ --connect-timeout 5 \
      --max-time 60 -w '%{http_code}' "$HEALTH_URL" 2>/tmp/priori-health-err.$$)" \
      || curl_rc=$?
    body="$(cat /tmp/priori-health-body.$$ 2>/dev/null || true)"
    if [ "$curl_rc" -eq 0 ] && [ "$http_code" = "200" ] \
        && printf '%s' "$body" | grep -q '"status":"healthy"'; then
      log "Staging is healthy after $attempt attempt(s): $body"
      healthy=1
      break
    fi
    log "Attempt $attempt/$attempts: curl exit $curl_rc, HTTP ${http_code:-000}," \
      "error: $(cat /tmp/priori-health-err.$$ 2>/dev/null || true)"
    [ -n "$body" ] && log "Body: $(printf '%s' "$body" | head -c 300)"
    sleep 5
  done
  rm -f /tmp/priori-health-body.$$ /tmp/priori-health-err.$$
  if [ "$healthy" -ne 1 ]; then
    log "FATAL: the app never came healthy on its own host."
    log "Timeouts on every attempt = the app is hanging (see the fork-safety"
    log "note in passenger_wsgi.py); a 404 = API_V1_PREFIX must be /v1 in the"
    log "staging .env (deployment.md §4.1)."
    if [ -f "$APP_DIR/stderr.log" ]; then
      log "Last 50 lines of $APP_DIR/stderr.log:"
      tail -n 50 "$APP_DIR/stderr.log" || true
    fi
    exit 1
  fi
fi

log "Staging release complete"
