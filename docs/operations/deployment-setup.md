## Part 2 — Production (DigitalOcean droplet)

> **Building a droplet from nothing? Do not follow this part by hand.**
> Part 2 documents the one-time, in-place restructuring of the *existing
> live* machine. Rebuilding a droplet from scratch (droplet loss, DR drill
> Variant A) is automated: `deploy/droplet_rebuild.sh` (run via
> `deploy/cloud-init.yaml.template` or from a checkout) installs the
> packages, deploy user + sudoers, `/srv/priori` skeleton, nginx site,
> systemd unit, backup config templates, ops scripts, and crontabs —
> leaving exactly **secrets, DNS, and TLS issuance** by hand. See
> [`../runbooks/database-backup-restore.md`](../runbooks/database-backup-restore.md),
> restore scenario 1 (issue #81).

**accounting.priori.co.ke is live.** The goal is to restructure it into
release directories *without downtime*, verify the site still works, and only then
let the pipeline deploy. Nothing below runs a deploy — you can stop after §2.4 and the
site is exactly as functional as it is now, just better organised.