# Host hardening baseline (production droplet)

Audience: the deployment operator / on-call engineer. Related: issue #86
(follow-up from #81 / !79), `deploy/host_hardening.sh`,
`docs/runbooks/database-backup-restore.md` (restore scenario 1),
`docs/operations/deployment-setup.md` (§2.0 is the app-state twin of the
audit checklist below).

The droplet holds the full financial ledger. Everything the application
platform needs is rebuilt by automation, but until issue #86 the host's
*security* posture — firewall, sshd policy, patching — was hand-applied and
undocumented, so a rebuilt droplet could come up **less hardened** than the
machine it replaced. This document records the decided baseline; the
baseline itself is code.

## The baseline — decision summary

| Control | Decision | Captured where |
|---|---|---|
| Network firewall | **DigitalOcean cloud firewall** — inbound 22/80/443 only, attached by tag `priori-prod` (preferred; §1) | this document (doctl commands) — deliberately *not* in the script, §1 explains why |
| Firewall fallback | ufw, **opt-in** (`ENABLE_UFW=1`), lockout-safe ordering | `deploy/host_hardening.sh` §3 |
| sshd | password auth off, keyboard-interactive off, root **key-only** (`prohibit-password`), no empty passwords, no X11 | `deploy/sshd-hardening.conf` → `/etc/ssh/sshd_config.d/00-priori-hardening.conf` |
| OS patching | unattended-upgrades, **security pocket only**, never an unattended reboot | `deploy/apt-auto-upgrades.conf` + `deploy/unattended-upgrades-priori.conf` → `/etc/apt/apt.conf.d/{20auto-upgrades,52priori-unattended-upgrades}` |
| fail2ban | **rejected** — decision record in §4 | not installed, by design |

Applying the on-droplet part is one idempotent command from a repo checkout
(safe on the live droplet — nothing restarts the app or PostgreSQL, and the
sshd change is validated before the daemon re-reads it):

```bash
sudo deploy/host_hardening.sh
```

**The rebuild hand-finish list is unchanged: exactly secrets, DNS, TLS.**
The DO firewall adds no hand-finish item because it *survives* droplet loss —
it is an account-level resource created once (§1); a rebuilt droplet gets it
by being created with the `priori-prod` tag, i.e. during provisioning, before
first boot.

## 1. Network firewall — DigitalOcean cloud firewall (preferred)

Why a cloud firewall instead of ufw inside the rebuild script:

- **Enforced outside the droplet.** It filters at DO's edge, so it survives
  droplet loss, a half-run bootstrap, kernel/iptables misconfiguration, or a
  compromised host disabling its own firewall. A tag-attached firewall
  protects the rebuilt droplet **from first boot**, before any script runs.
- **No lockout footgun by construction.** Locking yourself out of ssh with a
  cloud firewall is recoverable out-of-band via the DO console (Recovery
  Console does not traverse the firewall). ufw mis-ordering on a remote
  machine is not.
- **It cannot live in `droplet_rebuild.sh`.** Managing it requires a DO API
  token; the rebuild script runs *on* the droplet, which must never hold
  account credentials (a droplet that can edit its own firewall defeats the
  point above).

### One-time creation (doctl)

From an operator machine with `doctl` authenticated (the API token stays in
the password manager; never on the droplet, never in CI):

```bash
# The firewall, attached by TAG — droplets created with the tag are covered
# automatically, which is what makes rebuilds inherit it with zero hand steps.
doctl compute firewall create \
  --name priori-prod-fw \
  --tag-names priori-prod \
  --inbound-rules "protocol:tcp,ports:22,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:80,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:443,address:0.0.0.0/0,address:::/0" \
  --outbound-rules "protocol:icmp,address:0.0.0.0/0,address:::/0 protocol:tcp,ports:0,address:0.0.0.0/0,address:::/0 protocol:udp,ports:0,address:0.0.0.0/0,address:::/0"

# Tag the CURRENT live droplet so the firewall applies to it too:
doctl compute droplet-action  # (or: Console → Droplets → <droplet> → Tags → add priori-prod)
doctl compute droplet tag <droplet-id> --tag-name priori-prod
```

Console path: *Networking → Firewalls → Create Firewall*; inbound rules TCP
22, 80, 443 from all IPv4 + all IPv6; outbound all; *Apply to* → tag
`priori-prod`.

On any rebuild, create the replacement droplet **with the tag**:

```bash
doctl compute droplet create ... --tag-names priori-prod
```

Everything not listed (Postgres 5432 included — the API connects over
localhost) is unreachable from outside. **Optional tightening**: restrict the
port-22 rule to the operators' stable egress IPs/CIDRs; revisit after any ISP
change. Do not restrict 80/443 — they serve the public app and ACME.

### ufw fallback — when and how

`ENABLE_UFW=1 deploy/host_hardening.sh` exists for a host that is not behind
the DO firewall (non-DO migration, local rehearsal) or for deliberate defence
in depth. It is **off by default** so the standard path stays single-firewall
(two firewalls double the "why can't I reach it" surface during an incident).

The script's ordering is the lockout defence, and it is deliberate:

1. rules are written while ufw is **inactive** (they enforce nothing yet);
2. `ufw limit 22/tcp` is the **first** rule added (`limit` = allow +
   kernel-level rate limiting, 6 connections/30 s per source);
3. 80/tcp and 443/tcp allowed; default deny incoming / allow outgoing;
4. `ufw --force enable` is the **last** statement — a failure anywhere
   before it leaves the firewall off: fail-open and reachable, never
   half-enabled without ssh;
5. post-enable verification asserts active + an ssh rule, and on failure
   says to fix it **from the still-open session** (enabling ufw does not cut
   established connections).

## 2. sshd policy

Committed as [`deploy/sshd-hardening.conf`](../../deploy/sshd-hardening.conf),
installed to `/etc/ssh/sshd_config.d/00-priori-hardening.conf`:

```
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin prohibit-password
X11Forwarding no
```

- **`00-` prefix is load-bearing.** Ubuntu's `sshd_config` includes
  `sshd_config.d/*.conf` at the *top* of the file, the glob expands in
  lexical order, and sshd keeps the **first** value it sees per keyword —
  the earliest drop-in wins. Cloud provisioning writes `50-cloud-init.conf`
  (`PasswordAuthentication yes` on password-provisioned droplets) and cloud
  images ship `60-cloudimg-settings.conf`; `00-` beats both. A `99-` file
  would be silently overridden — the exact failure mode this issue exists to
  prevent.
- **`prohibit-password`, not `no`, for root**: root-with-key is the
  documented DO access path (provisioning injects the root key; the rebuild
  bootstrap runs as root). What the threat model forbids — root login with a
  password — is exactly what `prohibit-password` forbids.
- **Lockout guards in the script**: it refuses to run on a machine with no
  `authorized_keys` anywhere (key-only auth must be *viable* before password
  auth is disabled), validates the complete config with `sshd -t` *before*
  the daemon re-reads it, rolls the drop-in back if validation fails, and
  applies with a **reload** (established sessions — including yours —
  survive).

Verify the effective merged config any time:

```bash
sudo sshd -T | grep -Ei 'passwordauth|permitroot|kbdinteractive|emptypass'
```

## 3. OS patching — unattended-upgrades

Two committed apt configs (see the files for the full commentary):

- [`deploy/apt-auto-upgrades.conf`](../../deploy/apt-auto-upgrades.conf) →
  `/etc/apt/apt.conf.d/20auto-upgrades`: daily list refresh + daily
  unattended-upgrade run (the apt-daily systemd timers).
- [`deploy/unattended-upgrades-priori.conf`](../../deploy/unattended-upgrades-priori.conf)
  → `/etc/apt/apt.conf.d/52priori-unattended-upgrades`: **security pocket
  only** (origin lists are `#clear`ed first so distro defaults or hand edits
  cannot silently widen the scope), `Automatic-Reboot "false"`, superseded
  kernels cleaned up.

Policy: security patches apply themselves within a day; **nothing reboots
the ledger's database host unattended**. Kernel/libc updates that need a
reboot set `/var/run/reboot-required` — the audit checklist below checks it,
and the reboot happens in an announced window like any other restart.

## 4. fail2ban — decision: REJECTED (deliberately not installed)

With the rest of this baseline in place, fail2ban's marginal value does not
pay for its operational cost:

- **What it would defend against is already futile.** With
  `PasswordAuthentication no` and `KbdInteractiveAuthentication no`,
  ssh brute force cannot succeed cryptographically; fail2ban would be
  defending log tidiness, not the ledger.
- **It is itself attack surface and a lockout footgun.** A root daemon
  parsing attacker-controlled log lines with regexes, whose entire purpose
  is to lock people out — including the on-call operator on a flaky NAT or
  hotel Wi-Fi, plausibly *during* the incident that made them ssh in.
  "No lockout footguns" is a hard constraint of this baseline.
- **The rate-limiting value is already covered.** The DO firewall reduces
  exposure to 22/80/443; on the ufw fallback path, `ufw limit 22/tcp`
  provides kernel-level ssh rate limiting with zero new daemons.
- **Operational noise.** One more service whose health must be watched on a
  droplet whose monitoring budget is deliberately spent on backup freshness
  (the thing that actually pages).

**Revisit if** password or keyboard-interactive auth is ever re-enabled
(it must not be), or if port 22 must stay open to `0.0.0.0/0` *and* auth-log
noise becomes operationally costly — and even then, restricting the DO
firewall's port-22 sources (§1) is the better first move.

## 5. Live-droplet audit checklist — parity is verified, not assumed

The live droplet's hardening was applied by hand; **what it actually runs is
unknown until a human records it** (there is deliberately no automation with
ssh access to production). Mirroring how `deployment-setup.md` §2.0 records
app state: run these on the **live** droplet and keep the output; run the
same on any **rebuilt** droplet after `host_hardening.sh`; a rebuild passes
only when the rebuilt column is at least as hardened as the live one.

```bash
ssh root@accounting.priori.co.ke   # and later: the rebuilt droplet
```

| # | Check | Command | Live droplet (date: ____) | Rebuilt droplet | Parity OK? |
|---|---|---|---|---|---|
| 1 | DO cloud firewall attached, inbound = 22/80/443 only | (operator machine) `doctl compute firewall list --format Name,Tags,InboundRules` — or Console → Networking → Firewalls | | | |
| 2 | ufw state (expected: inactive when the DO firewall is attached; if *active* on the live droplet, record the full rule list) | `ufw status verbose` | | | |
| 3 | sshd effective auth policy | `sshd -T \| grep -Ei 'passwordauth\|permitroot\|kbdinteractive\|emptypass'` | | | |
| 4 | sshd drop-ins present (watch for a `50-cloud-init.conf` overriding hand edits) | `ls -la /etc/ssh/sshd_config.d/` | | | |
| 5 | fail2ban presence (baseline: absent — if *present and enabled* on the live droplet, see note below) | `systemctl is-active fail2ban; dpkg -l fail2ban 2>/dev/null \| tail -1` | | | |
| 6 | unattended-upgrades installed + periodic switches on | `dpkg -l unattended-upgrades \| tail -1; apt-config dump \| grep -E 'APT::Periodic::(Update-Package-Lists\|Unattended-Upgrade) '` | | | |
| 7 | unattended-upgrades actually ran recently | `ls -lt /var/log/unattended-upgrades/ \| head -5` | | | |
| 8 | apt-daily timers enabled | `systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer` | | | |
| 9 | pending reboot (security kernel waiting) | `[ -f /var/run/reboot-required ] && cat /var/run/reboot-required \|\| echo none` | | | |

Rules for filling it in:

- **Record verbatim output**, not "looks fine". Keep the filled table in the
  ops log next to the §2.0 record.
- **The live column is the floor, this document is the target.** Any control
  present on the live droplet but absent from this baseline (e.g. fail2ban
  running, extra ufw rules, sshd `AllowUsers`) means one of two things:
  either adopt it into the baseline (commit + MR, updating §4 if it
  overturns the fail2ban decision) or record *why* it is dropped — same-day
  issue, never silent loss. That is the entire point of parity.
- **A rebuilt droplet failing row 1** (no firewall tag) is a stop-the-line
  finding: the machine is serving with every port open.

## 6. How this integrates with the rebuild

- Restore scenario 1 (`docs/runbooks/database-backup-restore.md`) starts
  with: create the droplet **with the `priori-prod` tag**, then run
  `deploy/host_hardening.sh` as part of the host build.
- `deploy/droplet_rebuild.sh` (!79, issue #81) should invoke
  `host_hardening.sh` once both are on the same branch — tracked as a
  follow-up so neither MR blocks the other.
- The quarterly DR drill (Variant A) should fill the audit checklist for the
  drill droplet — that is what proves the baseline is actually converging
  machines, not just existing in the repo.
