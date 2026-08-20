#!/usr/bin/env bash
# Droplet rebuild bootstrap — builds the production droplet from nothing.
#
# This is restore scenario 1, STEP 1 (docs/runbooks/database-backup-restore.md):
# it replaces "follow docs/operations/deployment-setup.md by hand" as the way a
# lost droplet is rebuilt, so the 4-hour RTO (SLO 4) no longer rests on a
# hand-built machine. Issue #81; quarterly DR drill Variant A times this
# script as part of the 4 h budget.
#
# Run as root on a FRESH Ubuntu 24.04 (noble) droplet, from a checkout of this
# repository (any ref that contains the deploy/ artifacts — normally main):
#
#   sudo deploy/droplet_rebuild.sh
#
# or let cloud-init run it at first boot: deploy/cloud-init.yaml.template.
#
# What it does — everything that is CODE, nothing that is a SECRET:
#   1. packages: nginx, PostgreSQL 16, pgbackrest, rclone, age, python3.12,
#      certbot (+ nginx plugin), postgresql-client, rsync, curl.
#      Node.js is deliberately NOT installed: the SPA is built in CI and
#      shipped as static frontend/dist files that nginx serves; nothing on
#      the droplet executes JavaScript.
#   2. deploy user + /etc/sudoers.d/priori-deploy (exactly two commands:
#      restart priori-api, reload nginx — validated with visudo before install)
#   3. directory skeleton /srv/priori/{releases,shared,shared/uploads,backups,bin}
#      (current/ is a symlink the first release creates — see the summary)
#   4. ops scripts deploy/db_backup.sh + deploy/uploads_sync.sh -> /srv/priori/bin
#   5. nginx site from deploy/nginx-site.conf (committed, no secrets) + the
#      systemd unit from deploy/priori-api.service (committed) — enabled, and
#      nginx -t gates the install
#   6. pgBackRest + PostgreSQL archiving config from the committed templates.
#      deploy/pgbackrest.conf.template is installed WITH ITS PLACEHOLDERS
#      INTACT (0600 postgres:postgres) and never overwritten if present —
#      the bucket key and cipher passphrase are filled BY HAND from the
#      password manager; secrets never live in this repo.
#   7. crontabs from deploy/cron.d/*.cron (deploy + postgres users)
#
# What it deliberately does NOT do — the documented hand-finish list, exactly:
#   - SECRETS  (shared/.env; pgbackrest.conf placeholders; the deploy user's
#     rclone remote; the age public recipient; deploy's authorized_keys)
#   - DNS      (point accounting.priori.co.ke at the new droplet)
#   - TLS      (certbot --nginx — needs DNS first)
# The exact commands are printed in the summary at the end.
#
# Conventions follow deploy/db_backup.sh: set -euo pipefail, timestamped
# log(), loud failures, configuration via environment, nothing secret on
# argv. Idempotent: safe to re-run after a partial failure; it never
# overwrites a config that may already hold hand-filled secrets.

set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${ROOT:-/srv/priori}"
SITE_DOMAIN="${SITE_DOMAIN:-accounting.priori.co.ke}"
PG_VERSION="${PG_VERSION:-16}"
PG_CONF_DIR="${PG_CONF_DIR:-/etc/postgresql/$PG_VERSION/main/conf.d}"

log()   { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
fatal() { log "FATAL: $*"; exit 1; }

# --- preflight -----------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || fatal "must run as root: sudo $0"

if [ "${SKIP_OS_CHECK:-0}" != "1" ]; then
  # shellcheck disable=SC1091 # /etc/os-release exists on every systemd distro
  . /etc/os-release
  if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
    fatal "expected Ubuntu 24.04 (got ${ID:-?} ${VERSION_ID:-?}). The package set
       (postgresql-16, python3.12, pgbackrest, age) is pinned to noble.
       Re-run with SKIP_OS_CHECK=1 only if you have verified the packages."
  fi
fi

# Everything this script installs must come from the repo checkout — fail
# before touching the system if any committed artifact is missing.
REQUIRED_FILES=(
  deploy/db_backup.sh
  deploy/uploads_sync.sh
  deploy/nginx-site.conf
  deploy/priori-api.service
  deploy/pgbackrest.conf.template
  deploy/postgresql-archiving.conf.template
  deploy/cron.d/priori-backups.cron
  deploy/cron.d/priori-pgbackrest.cron
)
for f in "${REQUIRED_FILES[@]}"; do
  [ -f "$REPO_DIR/$f" ] || fatal "$REPO_DIR/$f missing — run from a full checkout"
done

# Root-owned system config must not inherit a stray restrictive/odd umask.
umask 022

# --- 1. packages -----------------------------------------------------------------
# --no-install-recommends keeps the surface small; every package here is a
# direct, documented dependency of the platform (see header).
log "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
  nginx \
  "postgresql-$PG_VERSION" \
  "postgresql-client-$PG_VERSION" \
  pgbackrest \
  rclone \
  age \
  python3.12 \
  python3.12-venv \
  rsync \
  curl \
  certbot \
  python3-certbot-nginx

# --- 2. deploy user + sudoers ------------------------------------------------------
if id deploy >/dev/null 2>&1; then
  log "deploy user already exists"
else
  log "Creating deploy user"
  adduser --disabled-password --gecos "" deploy
fi
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh

# sudoers matches the literal path and /bin is a symlink it will not follow
# (deployment-setup.md §2.2) — verify before writing the rule.
[ "$(command -v systemctl)" = "/usr/bin/systemctl" ] \
  || fatal "systemctl is not /usr/bin/systemctl — the sudoers rule would silently never match"

log "Installing /etc/sudoers.d/priori-deploy"
SUDOERS_TMP="$(mktemp)"
trap 'rm -f "$SUDOERS_TMP"' EXIT
cat > "$SUDOERS_TMP" <<'SUDO'
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart priori-api, /usr/bin/systemctl reload nginx
SUDO
# A syntactically broken sudoers file locks everyone out of sudo — validate
# the candidate BEFORE it lands in /etc/sudoers.d.
visudo -c -f "$SUDOERS_TMP" >/dev/null || fatal "sudoers candidate failed visudo -c"
install -m 440 -o root -g root "$SUDOERS_TMP" /etc/sudoers.d/priori-deploy

# --- 3. directory skeleton ----------------------------------------------------------
# current/ is deliberately absent: it is the release symlink; the first
# deploy (or the pre-pipeline seed) creates it. backups/ and uploads/ hold
# financial data — never group/world readable.
log "Creating $ROOT skeleton"
install -d -m 755 -o deploy -g deploy "$ROOT" "$ROOT/releases" "$ROOT/shared" "$ROOT/bin"
install -d -m 700 -o deploy -g deploy "$ROOT/backups" "$ROOT/shared/uploads"

# --- 4. ops scripts -----------------------------------------------------------------
# Same destination and modes as the Deploy production workflow's "Ship ops
# scripts" step — after bring-up that workflow re-ships them on every deploy,
# so /srv/priori/bin tracks the repo instead of drifting from it.
log "Installing ops scripts to $ROOT/bin"
install -m 700 -o deploy -g deploy "$REPO_DIR/deploy/db_backup.sh" "$ROOT/bin/db_backup.sh"
install -m 700 -o deploy -g deploy "$REPO_DIR/deploy/uploads_sync.sh" "$ROOT/bin/uploads_sync.sh"

# --- 5. nginx site + systemd unit ---------------------------------------------------
log "Installing nginx site (deploy/nginx-site.conf)"
install -m 644 -o root -g root "$REPO_DIR/deploy/nginx-site.conf" \
  /etc/nginx/sites-available/priori-crm.conf
ln -sfn ../sites-available/priori-crm.conf /etc/nginx/sites-enabled/priori-crm.conf
# The distro default site answers on the same port 80 catch-all — remove it
# so the priori site is what serves (and what certbot later edits).
rm -f /etc/nginx/sites-enabled/default
nginx -t || fatal "nginx -t rejected the installed configuration"
systemctl enable --now nginx
systemctl reload nginx

log "Installing systemd unit (deploy/priori-api.service)"
install -m 644 -o root -g root "$REPO_DIR/deploy/priori-api.service" \
  /etc/systemd/system/priori-api.service
systemctl daemon-reload
# enable, do NOT start: /srv/priori/current does not exist until the first
# release lands; the restore/deploy flow starts the service.
systemctl enable priori-api

# --- 6. PostgreSQL + pgBackRest config ----------------------------------------------
# The Ubuntu package created and started cluster $PG_VERSION/main.
PG_DATA_DIR="/var/lib/postgresql/$PG_VERSION/main"
[ -d "$PG_DATA_DIR" ] || fatal "$PG_DATA_DIR missing — did the postgresql-$PG_VERSION install create the cluster?"

log "Preparing pgBackRest (config with placeholders intact — secrets are hand-filled)"
install -d -m 750 -o postgres -g postgres /var/log/pgbackrest
install -d -m 755 -o root -g root /etc/pgbackrest
if [ -e /etc/pgbackrest/pgbackrest.conf ]; then
  # Never clobber: an existing config may already hold the hand-filled bucket
  # key and cipher passphrase. Idempotence must not destroy secrets.
  log "/etc/pgbackrest/pgbackrest.conf already exists — leaving it untouched"
else
  install -m 600 -o postgres -g postgres \
    "$REPO_DIR/deploy/pgbackrest.conf.template" /etc/pgbackrest/pgbackrest.conf
  log "installed /etc/pgbackrest/pgbackrest.conf — PLACEHOLDERS MUST BE FILLED (see summary)"
fi

# /etc/priori holds the age PUBLIC recipient (not secret, but root-owned so
# cron cannot be silently re-pointed — db_backup.sh refuses to run without it).
install -d -m 755 -o root -g root /etc/priori

log "Installing PostgreSQL archiving config (archive_mode=on needs a restart — doing it now, while nothing serves)"
install -d -m 755 -o postgres -g postgres "$PG_CONF_DIR"
install -m 644 -o postgres -g postgres \
  "$REPO_DIR/deploy/postgresql-archiving.conf.template" "$PG_CONF_DIR/archiving.conf"
# Restarting now (fresh droplet, no traffic) means filling the pgBackRest
# secrets later needs NO further restart: archive_command simply starts
# succeeding once the stanza exists. Until then archive-push fails and
# PostgreSQL retains WAL in pg_wal — loud (daily check + CI freshness), not
# silent, and bounded by how fast the secrets step happens.
systemctl restart postgresql

# --- 7. crontabs -----------------------------------------------------------------------
# `crontab <file>` replaces each user's ENTIRE crontab — correct on a rebuilt
# droplet, and the committed files are the canonical schedule (config-as-code).
# The jobs fail loudly (by design) until the secrets step is done: RCLONE_REMOTE
# needs the rclone remote, db_backup.sh needs the age recipient, pgbackrest
# needs its filled config.
log "Installing crontabs from deploy/cron.d/"
crontab -u deploy "$REPO_DIR/deploy/cron.d/priori-backups.cron"
crontab -u postgres "$REPO_DIR/deploy/cron.d/priori-pgbackrest.cron"

# --- verify --------------------------------------------------------------------------
log "Verifying"
id deploy >/dev/null || fatal "verify: deploy user missing"
[ -x "$ROOT/bin/db_backup.sh" ] || fatal "verify: $ROOT/bin/db_backup.sh not executable"
[ -x "$ROOT/bin/uploads_sync.sh" ] || fatal "verify: $ROOT/bin/uploads_sync.sh not executable"
nginx -t || fatal "verify: nginx -t failed"
systemctl is-enabled --quiet priori-api || fatal "verify: priori-api is not enabled"
systemctl is-active --quiet postgresql || fatal "verify: postgresql is not active"
sudo -u postgres psql -Atc 'show archive_mode' | grep -qx 'on' \
  || fatal "verify: archive_mode is not on after restart"
crontab -l -u deploy | grep -q db_backup.sh || fatal "verify: deploy crontab missing"
crontab -l -u postgres | grep -q pgbackrest || fatal "verify: postgres crontab missing"
python3.12 -m venv --help >/dev/null || fatal "verify: python3.12 venv unusable"
for cmd in pg_dump pgbackrest rclone age certbot; do
  command -v "$cmd" >/dev/null || fatal "verify: $cmd not on PATH"
done

log "Rebuild bootstrap complete."
cat <<FINISH

================================================================================
 Machine is built. HAND-FINISH — exactly three things (nothing else remains):

 1. SECRETS — from the password manager, never from this repo:
      - /srv/priori/shared/.env            (deploy:deploy 0600; app config incl.
                                            DATABASE_URL, JWT_SECRET_KEY, ...)
      - /etc/pgbackrest/pgbackrest.conf    (replace every <placeholder>: bucket
                                            key + repo-cipher passphrase; keep 0600)
      - deploy user's rclone remote        (sudo -u deploy rclone config — name it
                                            'spaces', droplet RW key)
      - age PUBLIC recipient               (install -m 644 <file>
                                            /etc/priori/backup-age-recipients.txt)
      - deploy's SSH key                   (append the deploy .pub to
                                            /home/deploy/.ssh/authorized_keys, 0600)
    then create the stanza and prove archiving round-trips:
      sudo -u postgres pgbackrest --stanza=priori stanza-create
      sudo -u postgres pgbackrest --stanza=priori check

 2. DNS — point $SITE_DOMAIN at this droplet.

 3. TLS — after DNS resolves here:
      certbot --nginx -d $SITE_DOMAIN

 Then continue with restore scenario 1 step 3
 (docs/runbooks/database-backup-restore.md): restore the database, restore
 uploads, deploy the current release, re-apply erasures, verify.
================================================================================
FINISH
