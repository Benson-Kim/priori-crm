#!/usr/bin/env bash
# Repo-file layer of the droplet rebuild automation (issue #81).
#
# Run as root, FROM A REPO CHECKOUT, on a droplet that was created with
# deploy/cloud-init/droplet-user-data.yaml (which owns the OS layer:
# packages, deploy user, sudoers, directory skeleton). Idempotent — safe to
# re-run after a partial failure or to converge a drifted host:
#
#   rsync -az deploy/ root@<new-droplet>:/tmp/priori-deploy/
#   ssh root@<new-droplet> 'bash /tmp/priori-deploy/droplet_provision.sh'
#
# What it installs, all from committed files (config-as-code — nothing is
# typed from memory):
#   1. the directory skeleton + sudoers (idempotent repeat of cloud-init,
#      so the script also converges a droplet built without user-data)
#   2. systemd unit        deploy/templates/priori-api.service
#   3. nginx site          deploy/templates/nginx-priori.conf.template
#   4. ops scripts         deploy/db_backup.sh, deploy/uploads_sync.sh
#      -> /srv/priori/bin (the production deploy workflow re-ships these on
#      every deploy; this is the first install)
#   5. pgBackRest + PostgreSQL archiving config FROM THE TEMPLATES — with
#      their <PLACEHOLDERS> intact. Secrets are hand-filled from the
#      password manager afterwards; they never live in the repo and this
#      script NEVER overwrites an existing (already-filled) config.
#   6. both crontabs from deploy/cron.d/
#
# It ends by printing the hand-finish list, deliberately reduced to:
# secrets, DNS, TLS (issue #81 acceptance). Everything else is this script.

set -euo pipefail

log() { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

[ "$(id -u)" = "0" ] || { log "FATAL: run as root (the script installs system config)"; exit 1; }

# Resolve the deploy/ directory this script lives in — templates and cron
# files are addressed relative to it, so the checkout can live anywhere.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for f in templates/priori-api.service templates/nginx-priori.conf.template \
         db_backup.sh uploads_sync.sh pgbackrest.conf.template \
         postgresql-archiving.conf.template cron.d/priori-backups.cron \
         cron.d/priori-pgbackrest.cron; do
  [ -f "$SRC/$f" ] || { log "FATAL: $SRC/$f missing — run from a complete deploy/ checkout"; exit 1; }
done

# The OS layer must exist first (cloud-init, or the same packages by hand).
for cmd in nginx pgbackrest rclone age pg_dump psql systemctl; do
  command -v "$cmd" >/dev/null || {
    log "FATAL: $cmd not found — create the droplet with deploy/cloud-init/droplet-user-data.yaml (packages) first"
    exit 1
  }
done
id deploy >/dev/null 2>&1 || { log "FATAL: no 'deploy' user — cloud-init user-data did not run"; exit 1; }

PG_CONF_DIR="/etc/postgresql/16/main/conf.d"
[ -d "$PG_CONF_DIR" ] || {
  log "FATAL: $PG_CONF_DIR missing — is PostgreSQL 16 installed? (a different"
  log "       major version needs deploy/postgresql-archiving.conf.template re-checked)"
  exit 1
}

# --- 1. skeleton + sudoers (idempotent repeat of cloud-init) -------------------
log "Directory skeleton + sudoers"
mkdir -p /srv/priori/releases /srv/priori/shared/uploads /srv/priori/backups /srv/priori/bin /etc/priori
chown deploy:deploy /srv/priori /srv/priori/releases /srv/priori/shared \
  /srv/priori/shared/uploads /srv/priori/backups /srv/priori/bin
chmod 700 /srv/priori/backups /srv/priori/shared/uploads
install -d -o postgres -g postgres -m 750 /var/log/pgbackrest
if [ ! -f /etc/sudoers.d/priori-deploy ]; then
  printf 'deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart priori-api, /usr/bin/systemctl reload nginx\n' \
    > /etc/sudoers.d/priori-deploy.tmp
  chmod 440 /etc/sudoers.d/priori-deploy.tmp
  mv /etc/sudoers.d/priori-deploy.tmp /etc/sudoers.d/priori-deploy
fi
visudo -c >/dev/null || { log "FATAL: sudoers failed validation — fix before continuing"; exit 1; }

# --- 2. systemd unit ------------------------------------------------------------
log "systemd unit -> /etc/systemd/system/priori-api.service"
install -m 0644 "$SRC/templates/priori-api.service" /etc/systemd/system/priori-api.service
systemctl daemon-reload
# Enabled but NOT started: there is no release yet — the first production
# deploy (or the restore runbook) starts it.
systemctl enable priori-api >/dev/null 2>&1 || true

# --- 3. nginx site ---------------------------------------------------------------
log "nginx site -> /etc/nginx/sites-available/priori"
install -m 0644 "$SRC/templates/nginx-priori.conf.template" /etc/nginx/sites-available/priori
ln -sfn /etc/nginx/sites-available/priori /etc/nginx/sites-enabled/priori
# The stock catch-all would shadow the site on a fresh droplet.
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx || systemctl restart nginx

# --- 4. ops scripts ---------------------------------------------------------------
log "Ops scripts -> /srv/priori/bin"
install -m 0700 -o deploy -g deploy "$SRC/db_backup.sh" /srv/priori/bin/db_backup.sh
install -m 0700 -o deploy -g deploy "$SRC/uploads_sync.sh" /srv/priori/bin/uploads_sync.sh

# --- 5. config templates (placeholders intact — hand-fill; never clobber) ---------
if [ -f /etc/pgbackrest/pgbackrest.conf ]; then
  log "KEEPING existing /etc/pgbackrest/pgbackrest.conf (may hold filled secrets)"
else
  log "pgBackRest template -> /etc/pgbackrest/pgbackrest.conf (fill placeholders by hand)"
  install -D -m 0600 -o postgres -g postgres \
    "$SRC/pgbackrest.conf.template" /etc/pgbackrest/pgbackrest.conf
fi
if [ -f "$PG_CONF_DIR/archiving.conf" ]; then
  log "KEEPING existing $PG_CONF_DIR/archiving.conf"
else
  log "archiving template -> $PG_CONF_DIR/archiving.conf (postgres RESTART required — not done here)"
  install -m 0644 -o postgres -g postgres \
    "$SRC/postgresql-archiving.conf.template" "$PG_CONF_DIR/archiving.conf"
fi

# --- 6. crontabs from the committed files ------------------------------------------
# `crontab <file>` replaces the user's ENTIRE crontab — that is the point:
# the repo files are canonical (see the warnings inside them).
log "Crontabs from deploy/cron.d/"
crontab -u deploy "$SRC/cron.d/priori-backups.cron"
crontab -u postgres "$SRC/cron.d/priori-pgbackrest.cron"

log "Provisioning complete. HAND-FINISH (deliberately not automated — secrets, DNS, TLS):"
log "  1. SECRETS, all from the password manager, none in any backup:"
log "     - /srv/priori/shared/.env            (chown deploy, chmod 600)"
log "     - /etc/pgbackrest/pgbackrest.conf    (fill the <PLACEHOLDERS>: bucket key + cipher passphrase)"
log "     - deploy user's rclone remote 'spaces' (rclone config; droplet RW bucket key)"
log "     - age PUBLIC recipient -> /etc/priori/backup-age-recipients.txt (0644 root:root)"
log "  2. RESTORE the database + uploads: runbook scenario 1 steps 2-5"
log "     (docs/runbooks/database-backup-restore.md); then restart postgres for"
log "     archive_mode=on, stanza-create, check, first full backup (bring-up steps 5-7)."
log "  3. DNS: point accounting.priori.co.ke at this droplet."
log "  4. TLS: certbot --nginx -d accounting.priori.co.ke (rewrites the installed site block)."
log "  5. Deploy current main via the 'Deploy production' workflow, then verify"
log "     /api/v1/health and run the runbook's post-restore checks."
