#!/usr/bin/env bash
# Smoke-test failure triage, executed ON the MochaHost staging account.
#
# Piped over ssh by .github/workflows/deploy-staging.yml when the externally
# run smoke test fails, and runnable by hand during an incident:
#
#   ssh -i ~/.ssh/priori-staging-deploy priori@staging.crm.priori.co.ke \
#     'bash -s' < deploy/staging_diagnose.sh
#
# staging_release.sh already health-checks from the host, but that verdict is
# minutes old by the time the smoke test fails and the SPA cutover has
# happened in between. This re-runs the same check AT FAILURE TIME: if the
# health URL answers from the host now, the app and its Passenger mount are
# fine and the network path between the GitHub runner and the host is
# dropping traffic; if it fails here too, the app is down and the sections
# below say why. Reporting only — always exits 0 so it never masks the smoke
# test's own failure with a different one.

set -u

APP_DIR="${APP_DIR:-$HOME/apps/priori-api}"
HEALTH_URL="${HEALTH_URL:-https://staging.crm.priori.co.ke/api/v1/health}"

echo "== host-local curl of the public health URL =="
body="$(mktemp)"
curl_exit=0
http_code="$(curl -sS -o "$body" -w '%{http_code}' --max-time 60 "$HEALTH_URL")" \
  || curl_exit=$?
echo "curl exit ${curl_exit}, HTTP ${http_code} for $HEALTH_URL"
echo "response body (first 300 bytes):"
head -c 300 "$body" || true
echo
rm -f "$body"

echo "== Passenger app dir, shim, .env and restart marker =="
ls -ld "$APP_DIR" "$APP_DIR/passenger_wsgi.py" "$APP_DIR/.env" \
  "$APP_DIR/tmp/restart.txt" 2>&1 || true

echo "== recent stderr / Passenger logs =="
found_log=0
for f in "$APP_DIR/stderr.log" "$HOME"/logs/*err*; do
  [ -f "$f" ] || continue
  found_log=1
  echo "-- $f (last 30 lines)"
  tail -n 30 "$f" || true
done
[ "$found_log" -eq 1 ] || echo "(no stderr/error logs found)"

echo "== running Passenger / Python processes for this account =="
# shellcheck disable=SC2009 # pgrep is not guaranteed on the shared host
ps -o pid,etime,rss,args -u "$(id -un)" 2>/dev/null \
  | grep -Ei 'passenger|python' | grep -v grep \
  || echo "(none visible — Passenger has not spawned the app)"

exit 0
