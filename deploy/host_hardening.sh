#!/usr/bin/env bash
# Host-hardening baseline — captures the droplet's host security posture as
# code. Issue #86 (follow-up from #81 / !79): the droplet rebuild automation
# rebuilds the application platform, but firewall rules, sshd policy and
# unattended-upgrades were undocumented hand state — a rebuilt droplet could
# come up LESS hardened than the machine it replaces, at exactly the moment
# it holds a freshly restored full ledger.
#
# Run as root on the droplet, from a checkout of this repository:
#
#   sudo deploy/host_hardening.sh                 # default: DO-cloud-firewall path
#   sudo ENABLE_UFW=1 deploy/host_hardening.sh    # non-DO host: ufw fallback
#
# Standalone today (safe to run on the LIVE droplet — nothing here restarts
# the app or PostgreSQL); designed to be invoked by deploy/droplet_rebuild.sh
# once !79 merges.
#
# What it does:
#   1. unattended-upgrades — installs the package plus the two committed apt
#      configs (deploy/apt-auto-upgrades.conf, deploy/unattended-upgrades-
#      priori.conf): security pocket only, never an unattended reboot.
#   2. sshd drop-in — installs deploy/sshd-hardening.conf as
#      /etc/ssh/sshd_config.d/00-priori-hardening.conf: password auth off,
#      keyboard-interactive off, root key-only (see that file for why the
#      00- prefix is load-bearing).
#   3. ufw FALLBACK, opt-in — the decided network baseline is the
#      DigitalOcean CLOUD firewall (22/80/443), which is an account-level DO
#      resource this script cannot manage: it needs a DO API token, and this
#      droplet must not hold one (docs/operations/host-hardening.md has the
#      doctl commands). ENABLE_UFW=1 exists for non-DO hosts or deliberate
#      defence in depth.
#
# fail2ban is deliberately ABSENT — decision record with reasoning in
# docs/operations/host-hardening.md §fail2ban (short: with key-only ssh its
# marginal value does not pay for a root log-parsing daemon that exists to
# lock people out).
#
# LOCKOUT-SAFETY ORDERING — the design constraint everything obeys:
#   - the sshd drop-in cannot strand a keyless machine: the script refuses
#     to disable password auth unless an authorized_keys with at least one
#     key exists (HARDEN_FORCE_NO_KEY=1 only for containers/tests);
#   - the running sshd never re-reads config until `sshd -t` validates the
#     complete effective config; on failure the drop-in is REMOVED again, so
#     a failed half-run leaves the previous working config in force;
#   - the reload never severs established sessions (and a socket-activated
#     sshd simply picks the new config up on the next connection);
#   - ufw rules are written while the firewall is INACTIVE, ssh is the FIRST
#     rule added, and `ufw enable` is the LAST statement of the block — any
#     failure before it leaves ufw off (machine reachable, fail-open), never
#     enabled without an ssh rule.
#
# Conventions follow deploy/db_backup.sh and the !69 lineage: set -euo
# pipefail, timestamped log(), loud failures, umask discipline, configuration
# via environment. This script handles NO secrets at all.
#
# Idempotent: safe to re-run after a partial failure; every step converges
# to the committed state (the configs carry no hand-filled values, so
# overwriting them is correct, unlike pgbackrest.conf).

set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

ENABLE_UFW="${ENABLE_UFW:-0}"
SSHD_SERVICE="${SSHD_SERVICE:-ssh}"   # Ubuntu/Debian unit name; RHEL would be sshd
SSHD_DROPIN="/etc/ssh/sshd_config.d/00-priori-hardening.conf"

log()   { printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }
fatal() { log "FATAL: $*"; exit 1; }

# --- preflight -----------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || fatal "must run as root: sudo $0"

if [ "${SKIP_OS_CHECK:-0}" != "1" ]; then
  # shellcheck disable=SC1091 # /etc/os-release exists on every systemd distro
  . /etc/os-release
  if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
    fatal "expected Ubuntu 24.04 (got ${ID:-?} ${VERSION_ID:-?}) — the production
       droplet baseline. Re-run with SKIP_OS_CHECK=1 only on a host whose
       packages and sshd drop-in semantics you have verified."
  fi
fi

# Everything this script installs must come from the repo checkout — fail
# before touching the system if any committed artifact is missing.
REQUIRED_FILES=(
  deploy/sshd-hardening.conf
  deploy/apt-auto-upgrades.conf
  deploy/unattended-upgrades-priori.conf
)
for f in "${REQUIRED_FILES[@]}"; do
  [ -f "$REPO_DIR/$f" ] || fatal "$REPO_DIR/$f missing — run from a full checkout"
done

# Root-owned system config must not inherit a stray restrictive/odd umask.
umask 022

# Containers (the CI/sandbox environment this script is exercised in) have
# no running systemd: install and VALIDATE everything, skip only the
# service/timer interactions — and say so loudly.
HAVE_SYSTEMD=0
[ -d /run/systemd/system ] && HAVE_SYSTEMD=1

# --- 1. unattended-upgrades ------------------------------------------------------
log "Installing unattended-upgrades (security pocket only, no unattended reboot)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends unattended-upgrades

install -m 644 -o root -g root "$REPO_DIR/deploy/apt-auto-upgrades.conf" \
  /etc/apt/apt.conf.d/20auto-upgrades
install -m 644 -o root -g root "$REPO_DIR/deploy/unattended-upgrades-priori.conf" \
  /etc/apt/apt.conf.d/52priori-unattended-upgrades

# apt-config parses every file in apt.conf.d — a syntax error in what we just
# installed fails HERE, loudly, not at 06:00 tomorrow inside a timer.
apt-config dump >/dev/null || fatal "apt configuration no longer parses after install"
apt-config dump APT::Periodic::Unattended-Upgrade | grep -q '"1"' \
  || fatal "APT::Periodic::Unattended-Upgrade is not 1"
apt-config dump APT::Periodic::Update-Package-Lists | grep -q '"1"' \
  || fatal "APT::Periodic::Update-Package-Lists is not 1"
apt-config dump Unattended-Upgrade::Automatic-Reboot | grep -q '"false"' \
  || fatal "Unattended-Upgrade::Automatic-Reboot is not false"

if [ "$HAVE_SYSTEMD" = "1" ]; then
  # Idempotent: the package enables these by default, but a hand-disabled
  # timer must not survive a hardening run silently.
  systemctl enable --now apt-daily.timer apt-daily-upgrade.timer
else
  log "systemd not running — skipped enabling apt-daily timers (container run;"
  log "on the droplet the timers are enabled and this branch does not fire)"
fi

# --- 2. sshd: password auth off, root key-only ------------------------------------
command -v sshd >/dev/null \
  || fatal "sshd not on PATH — is openssh-server installed? (it is on every droplet image)"

# LOCKOUT GUARD: never disable password authentication on a machine where no
# account has a public key installed — that is a machine nobody can enter.
if [ "${HARDEN_FORCE_NO_KEY:-0}" != "1" ]; then
  has_key=0
  for ak in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do
    if [ -s "$ak" ] && grep -Eq '^(ssh-|ecdsa-|sk-)' "$ak"; then
      log "found authorized key(s) in $ak — key-only access is viable"
      has_key=1
      break
    fi
  done
  [ "$has_key" = "1" ] || fatal "no authorized_keys with a key found for root or any
       user — disabling PasswordAuthentication now would lock every operator
       out. Install a public key first. (HARDEN_FORCE_NO_KEY=1 skips this
       guard for container/test runs ONLY.)"
fi

log "Installing sshd hardening drop-in $SSHD_DROPIN"
install -d -m 755 -o root -g root /etc/ssh/sshd_config.d
install -m 644 -o root -g root "$REPO_DIR/deploy/sshd-hardening.conf" "$SSHD_DROPIN"

# Validate the COMPLETE effective config before the running daemon ever
# re-reads it. On failure, remove the drop-in again: a failed run leaves
# sshd exactly as it was, running and valid.
if ! sshd -t; then
  rm -f "$SSHD_DROPIN"
  fatal "sshd -t rejected the config including the drop-in — removed
       $SSHD_DROPIN again; the running sshd keeps its previous valid config"
fi

# sshd -T prints the EFFECTIVE merged config — this is the drop-in-ordering
# proof (a 50-cloud-init.conf saying 'PasswordAuthentication yes' would make
# these fail; see deploy/sshd-hardening.conf for why 00- wins).
sshd -T | grep -qx 'passwordauthentication no' \
  || fatal "effective sshd config still has passwordauthentication yes — a
       lexically-earlier drop-in is overriding $SSHD_DROPIN"
sshd -T | grep -qx 'kbdinteractiveauthentication no' \
  || fatal "effective sshd config still allows keyboard-interactive auth"
sshd -T | grep -qxE 'permitrootlogin (prohibit-password|without-password)' \
  || fatal "effective permitrootlogin is not prohibit-password"

if [ "$HAVE_SYSTEMD" = "1" ]; then
  # reload (never restart): established sessions — including the one running
  # this script — are not severed. try-*: on Ubuntu 24.04 sshd is socket-
  # activated and may be inactive with no connections; new connections get
  # the new config anyway, so "not running" is not an error.
  systemctl try-reload-or-restart "$SSHD_SERVICE"
  log "sshd configuration applied and service reloaded"
else
  log "systemd not running — config installed and validated; a real droplet"
  log "reloads sshd here (established sessions survive a reload)"
fi

# --- 3. firewall -------------------------------------------------------------------
if [ "$ENABLE_UFW" = "1" ]; then
  log "ENABLE_UFW=1 — configuring ufw fallback (22 limited, 80, 443)"
  apt-get install -y -q --no-install-recommends ufw

  # ORDER IS THE LOCKOUT DEFENCE. On first run ufw is inactive: rules and
  # policies below are written to config only and enforce NOTHING until
  # `enable`, which is deliberately the LAST statement — a failure anywhere
  # above it leaves the firewall off (machine reachable, fail-open), never
  # half-enabled without ssh. ssh is still the FIRST rule added so that on
  # RE-runs against an already-active firewall the ssh rule can only ever be
  # confirmed-present before anything else is touched (adds are idempotent:
  # "Skipping adding existing rule").
  ufw limit 22/tcp comment 'ssh - rate-limited (6 conn/30s per source)'
  ufw allow 80/tcp comment 'http - redirect + ACME http-01'
  ufw allow 443/tcp comment 'https'
  ufw default deny incoming
  ufw default allow outgoing
  ufw --force enable

  ufw status verbose | grep -q 'Status: active' \
    || fatal "ufw enable did not leave the firewall active"
  ufw status | grep -q '22/tcp' \
    || fatal "ufw is active without an ssh rule — fix immediately FROM THE
       STILL-OPEN SESSION (ufw allow 22/tcp); do not log out"
else
  log "ufw untouched (ENABLE_UFW=0): the decided network baseline is the"
  log "DigitalOcean cloud firewall (22/80/443, tag priori-prod) — account-"
  log "level, cannot live in this script; doctl commands and reasoning in"
  log "docs/operations/host-hardening.md. Set ENABLE_UFW=1 only on non-DO hosts."
fi

# --- verify ------------------------------------------------------------------------
log "Verifying"
[ -f "$SSHD_DROPIN" ] || fatal "verify: $SSHD_DROPIN missing"
[ -f /etc/apt/apt.conf.d/20auto-upgrades ] || fatal "verify: 20auto-upgrades missing"
[ -f /etc/apt/apt.conf.d/52priori-unattended-upgrades ] \
  || fatal "verify: 52priori-unattended-upgrades missing"
command -v unattended-upgrade >/dev/null || fatal "verify: unattended-upgrade not on PATH"
sshd -t || fatal "verify: sshd -t failed"

log "Host-hardening baseline applied."
cat <<SUMMARY

================================================================================
 Host-hardening baseline is in force (issue #86):
   - sshd: password auth OFF, keyboard-interactive OFF, root key-only
     ($SSHD_DROPIN)
   - unattended-upgrades: security pocket only, no unattended reboot
     (/etc/apt/apt.conf.d/20auto-upgrades, 52priori-unattended-upgrades)
   - firewall: $( [ "$ENABLE_UFW" = "1" ] \
       && echo "ufw active (22 rate-limited, 80, 443; default deny incoming)" \
       || echo "DigitalOcean cloud firewall (verify it is attached: tag priori-prod)" )
   - fail2ban: deliberately not installed — decision record in
     docs/operations/host-hardening.md

 Record the audit checklist (docs/operations/host-hardening.md §audit) so
 parity with the machine this one replaces is VERIFIED, not assumed.

 This changes nothing about the rebuild hand-finish list, which remains
 exactly: secrets, DNS, TLS.
================================================================================
SUMMARY
