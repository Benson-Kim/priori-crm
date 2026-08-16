# Deployment setup — operator guide

Step-by-step for bringing the pipeline in [`deployment.md`](./deployment.md) online.
That document explains *why*; this one is what you type.

**Order matters.** Part 0 first — one item there can invalidate the staging design,
and another causes competing deploys if skipped. Then staging (nothing is live, so
mistakes are cheap). Production last, and **read §2.0 before typing anything on the
droplet — accounting.priori.co.ke is serving real users right now.**

Every step ends with a **Verify** block. If what you see does not match, stop there
rather than continuing — each step builds on the last.

| Part | What | Roughly |
|---|---|---|
| 0 | Clear the blockers | 30 min |
| 1 | Staging on MochaHost | 1–2 hrs |
| 2 | Production on the droplet | 2–3 hrs, mostly care |
| 3 | Day-to-day | — |

Throughout, `gh` is the GitHub CLI, already authenticated as `Benson-Kim`. Every
secret can be set from your terminal — no clicking through Settings.

---

## Part 0 — Clear the blockers first

### 0.1 Disconnect the Vercel projects  ⚠️ do this before merging the branch

Three Vercel projects are still attached to this repository:

```console
$ gh api repos/Benson-Kim/priori-crm/environments --jq '.environments[].name'
Preview
Preview – priori-crm
Preview – priori-crm-ou38
Preview – priori-crm-zluj
Production
...
```

Each will auto-deploy on the same pushes the new pipeline responds to. In the Vercel
dashboard, for **each** of `priori-crm`, `priori-crm-ou38`, `priori-crm-zluj`:
Settings → Git → **Disconnect**. Delete the projects outright if they are dead.

Do the same for Render if a service there is still linked to this repo.

**Verify** — after disconnecting, the Vercel-owned environments stop being updated.
The old entries may linger; what matters is that a push to `develop` no longer starts
a Vercel build. Confirm on the first staging deploy (§1.6) that the only run is
`Deploy staging`.

### 0.2 Decide on GitHub Pro

Your stated requirement is *"ci-passes, approve deployment, deploy"*. On this private
repo, the approval half cannot use GitHub's own mechanism:

```console
$ gh api repos/Benson-Kim/priori-crm/branches/main/protection
Upgrade to GitHub Pro or make this repository public ... (HTTP 403)
```

The pipeline ships with a `workflow_dispatch` gate instead: nothing reaches production
without a human deliberately running the workflow and typing `deploy`. That is a real
gate, but **the person who approves is the person who deploys** — it cannot require a
second pair of eyes.

- Happy with that? Change nothing.
- Want true four-eyes? Buy GitHub Pro, then: Settings → Environments → `production` →
  **Required reviewers**, and Settings → Branches → protect `main`. Tell me and I will
  switch `deploy-production.yml` from `workflow_dispatch` to `push` on `main` — it is
  about four lines.

### 0.3 Merge the branch

The work is on `duo/chore/deployment-pipeline`, branched from `develop`. Open the MR
on GitLab as usual. **Do not merge until 0.1 is done** — the moment it lands on
`develop`, `deploy-staging.yml` starts firing.

---

## Part 1 — Staging (MochaHost, cPanel)

Host `69.72.248.125`, cPanel user `priori`. SSH is open (OpenSSH 9.9 — the old
runbook's "ports 21/22 are filtered at the network edge" note is stale; that block
was lifted, which is why this design uses SSH where the genesis-prestige runbook
could not).

### 1.0 Get access without the account password  ⚠️ do this first

Every step below needs either cPanel or SSH. If you do not have the cPanel account
password, **you very likely do not need it** — key-based SSH bypasses it entirely,
and the key is installed from the web UI.

```bash
# On your machine. This is the same key CI will use later (§1.5) — not throwaway work.
ssh-keygen -t ed25519 -f ~/.ssh/priori-staging-deploy -N "" -C "github-actions-staging"
cat ~/.ssh/priori-staging-deploy.pub
```

1. Reach cPanel through the **MochaHost client portal** → *Login to cPanel*. That
   button is single sign-on: it authenticates from the billing account and does
   **not** prompt for the cPanel password.
2. cPanel → **SSH Access** → *Manage SSH Keys* → **Import Key**, paste the `.pub`
   contents.
3. **Authorize** the key — a separate click after importing, and the step people
   miss. An imported-but-unauthorized key still fails with `Permission denied`.

**Verify** — must print `priori`, with no password prompt:

```bash
ssh -i ~/.ssh/priori-staging-deploy priori@staging.crm.priori.co.ke whoami
```

| Result | Meaning |
|---|---|
| `priori` | Fully unblocked; continue to §1.1 |
| `Shell access is not enabled on your account!` | The key worked, but shell is off. Stop and say so — the deploy transport has to change to FTPS + cron |
| `Permission denied (publickey,...,password)` | Key not authorized (step 3), or pasted with a line break |

While logged into the portal, you can usually also **reset the cPanel password**
there without knowing the old one. Worth doing anyway, but not required for any step
in this guide.

> If you cannot reach cPanel at all, staging is blocked until someone can. **Part 2
> (production) is entirely independent — do that instead**; it is the live site and
> the higher-value half.

### 1.1 Create the database

> **Already investigated — this is no longer a blocker.** Neither extension is
> installable on this host and neither is needed; the migrations were changed to
> handle it. Create the database, run the check to confirm the state, and move on.
> The full reasoning is below the check.

In cPanel → **PostgreSQL Databases**, create:

- database `priori_crm_staging_db`
- user `priori_crm_staging` with a generated password
- add the user to the database with **ALL PRIVILEGES**

Then run the extension check **over SSH, not in phpPgAdmin**:

```bash
ssh -i ~/.ssh/priori-staging-deploy priori@staging.crm.priori.co.ke
command -v psql || echo "no psql — use the phpPgAdmin fallback below"

PGPASSWORD='<password>' psql -h localhost -U priori_crm_staging -d priori_crm_staging_db \
  -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm;' \
  -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto;' \
  -c "SELECT extname FROM pg_extension WHERE extname IN ('pg_trgm','pgcrypto');"
```

**Expected on this host** — two "could not open extension control file" errors and
zero rows. That is the known-good outcome, not a failure:

```
ERROR:  could not open extension control file ".../pg_trgm.control": No such file
ERROR:  could not open extension control file ".../pgcrypto.control": No such file
 extname
---------
(0 rows)
```

If instead it lists both extensions, you are on a host with contrib installed —
also fine, and you get the trigram indexes for free.

> **Why not phpPgAdmin?** Its SQL window wraps every statement in
> `SELECT COUNT(*) AS total FROM (<your sql>) AS sub` to paginate results, which is
> not valid around a `CREATE`. You get:
>
> ```
> ERROR: syntax error at or near "CREATE"
> LINE 1: SELECT COUNT(*) AS total FROM (CREATE EXTENSION IF NOT EXIST...
> ```
>
> That is the tool, not Postgres — the command never reached the server, so it tells
> you nothing about permissions. If you must use phpPgAdmin, untick **Paginate
> results** on the SQL page first, then run one statement at a time.

**Outcome on this host (checked 2026-08-16): neither extension is installable, and
that is fine — the deploy proceeds anyway.**

```
ERROR: could not open extension control file
       "/usr/share/pgsql/extension/pg_trgm.control": No such file or directory
```

Read that error carefully, because it is *not* a permission problem: the
`postgresql-contrib` package is absent from the server entirely, so the extensions
are not merely un-installed but unavailable. Only the host can change that.

Neither blocks staging:

- **`pgcrypto` is not needed at all.** No migration creates it; it was only ever
  wanted for `gen_random_uuid()`, which has been **core PostgreSQL since 13** — this
  server is 13.23. Nothing in the schema calls a pgcrypto-only function
  (`crypt`/`digest`/`hmac`/`pgp_*`).
- **`pg_trgm` is needed by two migrations, which now skip it cleanly.** Both are
  guarded on `pg_available_extensions`, so a host without contrib migrates through
  instead of aborting on the trigram indexes. Hosts that *do* have it — CI and the
  production droplet — behave exactly as before.

The cost is honest and small: staging searches with `ILIKE '%term%'` run as
sequential scans instead of index-assisted lookups. Results are identical; large
lists are slower. Production keeps its indexes.

**Optional — ask the host to install contrib anyway.** Worth doing so staging matches
production, but nothing waits on it:

> My PostgreSQL database `priori_crm_staging_db` is missing the `postgresql-contrib`
> package — `CREATE EXTENSION pg_trgm` fails with "could not open extension control
> file /usr/share/pgsql/extension/pg_trgm.control", and `pg_trgm` does not appear in
> `pg_available_extensions`. Please install the contrib package matching the server's
> PostgreSQL version (13.23) so `pg_trgm` can be enabled.

If they install it later, the guarded migrations will **not** re-run — Alembic has
already recorded them. Close the gap with the catch-up script, which is idempotent
and safe to run even if contrib is still missing:

```bash
psql -h localhost -U priori_crm_staging -d priori_crm_staging_db \
  -f ~/apps/priori-api/deploy/enable_trgm_indexes.sql
```

### 1.2 Create the Python app

cPanel → **Setup Python App** → Create Application:

| Field | Value |
|---|---|
| Python version | **3.12** |
| Application root | `apps/priori-api` |
| Application URL | `staging.crm.priori.co.ke` **/api** ← the `/api` path is required |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |

The `/api` mount is what makes staging same-origin like production. It is also why
staging runs `API_V1_PREFIX=/v1` (step 1.4) — Passenger strips the mount prefix before
the app sees the path. Getting this pair wrong 404s every route.

**Record the two paths cPanel prints when the app is created** — the later steps use
both, and if cPanel placed the app somewhere other than what you typed, following the
defaults below silently writes config into a directory nothing reads:

- the **application root**, e.g. `/home/priori/apps/priori-api` → used in §1.4
  and as `STAGING_APP_DIR` in §1.5
- the **virtualenv path**, e.g. `/home/priori/virtualenv/apps/priori-api/3.12`
  → `deploy/staging_release.sh` expects exactly this shape

Also create the docroot directory if it does not exist: cPanel → **Domains** →
confirm `staging.crm.priori.co.ke` points at `~/staging.crm.priori.co.ke`.

**Creating the app also creates `~/staging.crm.priori.co.ke/api/`** — a directory in
the *docroot* holding an `.htaccess` with the `PassengerAppRoot` directives. That
directory **is** the mount; delete it and the API 404s no matter how healthy the app
is. The deploy workflow excludes it (along with `.well-known/`, which AutoSSL needs)
from the SPA's `rsync --delete`, so deploys leave it alone — but do not clean it up
by hand either.

**Verify the mount exists before deploying:**

```bash
ssh -i ~/.ssh/priori-staging-deploy priori@staging.crm.priori.co.ke \
  'ls -la ~/staging.crm.priori.co.ke/api/ && cat ~/staging.crm.priori.co.ke/api/.htaccess'
```

**Verify** — cPanel → **SSL/TLS Status**: `staging.crm.priori.co.ke` shows an AutoSSL
certificate. The deploy's smoke test is an HTTPS call and will fail without one.

### 1.3 The deploy SSH key

Already done in §1.0 — `~/.ssh/priori-staging-deploy` is the key CI will use, and it
is deliberately a **dedicated** keypair rather than a personal one, because anyone who
can run a deploy can use it.

**Re-verify before moving on**, since the next steps all run over it:

```bash
ssh -i ~/.ssh/priori-staging-deploy priori@staging.crm.priori.co.ke whoami
```

### 1.4 Create the staging `.env` on the host

This file is host-managed: it is excluded from rsync and CI never sees it.

Write it into the **application root you recorded in §1.2** — the path below assumes
cPanel used `~/apps/priori-api`. Confirm first, because the command would otherwise
happily create a new directory that nothing reads:

```bash
ssh -i ~/.ssh/priori-staging-deploy priori@staging.crm.priori.co.ke \
  'ls -d ~/apps/priori-api && ls ~/apps/priori-api'
```

```bash
ssh -i ~/.ssh/priori-staging-deploy priori@staging.crm.priori.co.ke \
  'cat > ~/apps/priori-api/.env' <<'ENV'
APP_NAME=Business Central
ENVIRONMENT=staging
DEBUG=false

# REQUIRED for the Passenger /api mount. Production uses /api/v1; staging must
# use /v1 because Passenger strips the mount prefix. Wrong value = 404 on
# every route. See deployment.md §4.1.
API_V1_PREFIX=/v1

DATABASE_URL=postgresql://priori_crm_staging:<password>@localhost:5432/priori_crm_staging_db

# Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
JWT_SECRET_KEY=<paste a fresh 32+ char secret>

# Same-origin, so this is belt-and-braces rather than load-bearing.
CORS_ORIGINS=https://staging.crm.priori.co.ke
FRONTEND_BASE_URL=https://staging.crm.priori.co.ke

# Single Passenger process — in-process limiting is correct here. Leave REDIS_URL unset.
RATE_LIMIT_ENABLED=true
RATE_LIMIT_BACKEND=memory
TOKEN_DENYLIST_BACKEND=memory

AWS_REGION=af-south-1
SES_SENDER_EMAIL=noreply@priori.co.ke

STORAGE_BACKEND=local
UPLOAD_DIR=/home/priori/apps/priori-api/uploads

# Gates the internal job endpoints; they fail closed if unset.
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
INTERNAL_API_SECRET=<paste a fresh secret>

LOG_LEVEL=INFO
ENV
```

Replace every `<...>`. Then lock it down and create the uploads directory:

```bash
ssh -i ~/.ssh/priori-staging-deploy priori@staging.crm.priori.co.ke \
  'chmod 600 ~/apps/priori-api/.env && mkdir -p ~/apps/priori-api/uploads'
```

> `FRONTEND_BASE_URL` builds password-reset links. Pointing it at production would
> send staging users emails that log them into production.

### 1.5 Set the GitHub secrets

From the repo directory:

```bash
gh secret set STAGING_SSH_KEY      < ~/.ssh/priori-staging-deploy
gh secret set STAGING_KNOWN_HOSTS --body "$(ssh-keyscan -H staging.crm.priori.co.ke 2>/dev/null)"
gh secret set STAGING_SSH_USER    --body "priori"
gh secret set STAGING_SSH_HOST    --body "staging.crm.priori.co.ke"
gh secret set STAGING_DOCROOT     --body "/home/priori/staging.crm.priori.co.ke"
gh secret set STAGING_APP_DIR     --body "/home/priori/apps/priori-api"
```

Use absolute paths, not `~` — rsync targets do not expand it reliably.

Also, for the scheduled internal jobs (staging skips quietly until both are set):

```bash
gh secret set STAGING_API_BASE_URL        --body "https://staging.crm.priori.co.ke"
gh secret set STAGING_INTERNAL_API_SECRET --body "<the same INTERNAL_API_SECRET from 1.4>"
```

**Verify**

```bash
gh secret list | grep STAGING
```

### 1.6 First deploy

Merge to `develop` (or push any commit to it). Then watch:

```bash
gh run watch
```

The run is: `api-ci`, `ui-ci`, `security` in parallel → `deploy`.

### 1.7 Verify staging

```bash
# API through the Passenger mount — the single most important check
curl -s https://staging.crm.priori.co.ke/api/v1/health
# expect: {"status":"healthy","version":"1.0.0","environment":"staging",...}

curl -s https://staging.crm.priori.co.ke/api/v1/ping
# expect: {"ping":"pong"}

# SPA loads
curl -sI https://staging.crm.priori.co.ke | head -1        # 200
# Deep link falls back to index.html rather than 404
curl -sI https://staging.crm.priori.co.ke/customers | head -1   # 200
```

Then open <https://staging.crm.priori.co.ke> and log in.

### Staging troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/api/v1/health` → 404, SPA works | `API_V1_PREFIX` wrong | must be `/v1` in the staging `.env` (§1.4), then restart: `touch ~/apps/priori-api/tmp/restart.txt` |
| `/api/v1/health` returns **HTML** | `.htaccess` missing or the `!^/api` guard lost | re-run the deploy; check `~/staging.crm.priori.co.ke/.htaccess` exists |
| Deploy fails at `Configure SSH` | secret missing/malformed | `gh secret list`; re-set `STAGING_SSH_KEY` from the **private** key file |
| `alembic upgrade head` fails on `gen_random_uuid()` | extensions missing | back to §1.1 |
| `pip install` OOM / killed | shared-host memory cap | the SPA is built in CI already; if Python deps are the problem, ask support to raise the limit |
| 503 from LiteSpeed | app failed to boot | `~/apps/priori-api/stderr.log`, and cPanel → Setup Python App → restart |

---

## Part 2 — Production (DigitalOcean droplet)

**accounting.priori.co.ke is live.** The goal is to restructure it into
release directories *without downtime*, verify the site still works, and only then
let the pipeline deploy. Nothing below runs a deploy — you can stop after §2.4 and the
site is exactly as functional as it is now, just better organised.

### 2.0 Record the current state before changing anything

Run these and **keep the output** — the rest of Part 2 depends on it, and it is your
map back if anything goes wrong.

```bash
ssh root@accounting.priori.co.ke

# Which service runs the API, and how?
systemctl list-units --type=service | grep -iE 'priori|uvicorn|gunicorn|api|crm'
systemctl cat <the-unit-name>          # ← record ExecStart, WorkingDirectory,
                                       #   EnvironmentFile, User

# Where does nginx serve the SPA from, and where does it proxy /api?
nginx -T 2>/dev/null | grep -B5 -A25 'accounting.priori.co.ke'

# Where does the code live, and is it a git checkout?
ls -la /srv /opt /var/www 2>/dev/null
git -C <the-app-dir> log --oneline -3 2>/dev/null

# What Python, and is pg_dump present?
python3.12 --version || python3 --version
command -v pg_dump || echo "MISSING — apt install postgresql-client"

# Where is the database, and where is the current .env?
grep -rl DATABASE_URL <the-app-dir> 2>/dev/null | head
```

Two answers change what you do next:

- **`ExecStart` uses `uvicorn` or `gunicorn`?** With plain uvicorn, a restart drops
  in-flight requests for a second or two. Acceptable, but know it happens.
- **Is the database on this droplet or managed?** `pg_dump` needs to reach it from the
  droplet either way; if it is managed, confirm the droplet's IP is allow-listed.

### 2.1 Host prerequisites

```bash
apt update
apt install -y postgresql-client python3.12-venv rsync
command -v pg_dump && python3.12 -m venv --help >/dev/null && echo "OK"
```

`pg_dump` is **not** implied by `psycopg2` — without it the deploy aborts at the
backup step, by design.

### 2.2 Create the deploy user

```bash
adduser --disabled-password --gecos "" deploy
mkdir -p /home/deploy/.ssh && chmod 700 /home/deploy/.ssh
```

On **your machine**, make a dedicated keypair and install it:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/priori-prod-deploy -N "" -C "github-actions-production"
ssh-copy-id -i ~/.ssh/priori-prod-deploy.pub deploy@accounting.priori.co.ke
# or paste the .pub into /home/deploy/.ssh/authorized_keys as root
```

Grant exactly two sudo rights — note `/usr/bin/systemctl`, not `/bin/systemctl`.
sudoers matches the literal path and `/bin` is a symlink it will not follow, so the
wrong path silently fails to match:

```bash
# Confirm the path first
command -v systemctl        # expect /usr/bin/systemctl

cat > /etc/sudoers.d/priori-deploy <<'SUDO'
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart priori-api, /usr/bin/systemctl reload nginx
SUDO
chmod 440 /etc/sudoers.d/priori-deploy
visudo -c        # must say "parsed OK"
```

**Verify** — as `deploy`, this succeeds and anything else is refused:

```bash
sudo /usr/bin/systemctl reload nginx && echo "sudo OK"
sudo /usr/bin/systemctl stop nginx          # must be refused
```

### 2.3 Build the release layout around the running app

This is the delicate part. It moves nothing until the last moment.

```bash
# 1. Structure
mkdir -p /srv/priori/{releases,shared,backups}

# 2. Move the live .env into shared/ (adjust the source path from §2.0).
#    deploy must be able to read it: the release script sources it for
#    DATABASE_URL, and systemd reads it too.
cp <current-app-dir>/.env /srv/priori/shared/.env
chown deploy:deploy /srv/priori/shared/.env
chmod 600 /srv/priori/shared/.env

# 3. Move uploads so they survive releases (skip if STORAGE_BACKEND=s3)
mv <current-app-dir>/uploads /srv/priori/shared/uploads 2>/dev/null || mkdir -p /srv/priori/shared/uploads
chown -R deploy:deploy /srv/priori/shared/uploads

# 4. Seed a first release from what is running RIGHT NOW. This becomes your
#    rollback target — without it the first deploy has nothing to fall back to,
#    so check the verify block below before moving on.
#    rsync, not cp -a: if the live app is a git checkout, cp would copy .git
#    into every release and /srv/priori/releases would balloon.
BASE=/srv/priori/releases/pre-pipeline-$(date +%Y%m%d)
mkdir -p $BASE/api $BASE/frontend/dist
rsync -a --exclude '.git' --exclude '__pycache__' <current-app-dir>/ $BASE/api/
rsync -a <current-spa-docroot>/ $BASE/frontend/dist/
ln -sfn /srv/priori/shared/.env    $BASE/api/.env
ln -sfn /srv/priori/shared/uploads $BASE/api/uploads

# 5. Its venv (per-release, so a rollback restores dependencies too)
python3.12 -m venv $BASE/venv
$BASE/venv/bin/pip install -q --upgrade pip
$BASE/venv/bin/pip install -q -r $BASE/api/requirements.txt

# 6. Point current at it
ln -sfn $BASE /srv/priori/current
chown -R deploy:deploy /srv/priori
```

**Verify before touching any service** — the new tree must be complete:

```bash
ls -l /srv/priori/current                     # symlink → releases/pre-pipeline-…
ls /srv/priori/current/api/app/main.py        # exists
ls /srv/priori/current/frontend/dist/index.html
/srv/priori/current/venv/bin/python -c "import fastapi; print('venv OK')"
```

### 2.4 Point systemd and nginx at `current`

**Reconcile this with the real unit from §2.0 — the values below are illustrative,
not prescriptive.** In particular, copy the **bind address and port** from your
recorded `ExecStart`. nginx's `proxy_pass` points at whatever the API listens on
today; if you paste `--port 8000` and the live service uses something else, the API
502s the moment you restart. Same for `--workers`.

```bash
systemctl edit --full priori-api      # or create /etc/systemd/system/priori-api.service
```

```ini
[Unit]
Description=Priori CRM API
After=network.target

[Service]
User=deploy
Group=deploy
WorkingDirectory=/srv/priori/current/api
# Deliberately NO EnvironmentFile= — see the note below.
ExecStart=/srv/priori/current/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

systemd resolves `WorkingDirectory` at start, so a restart after the symlink swap
picks up the new release with no further edits — that is what makes deploys work.

**Why there is no `EnvironmentFile=`.** The app loads its own config:
`api/app/lib/config.py:108` sets `env_file=".env"`, resolved relative to the working
directory — and §2.3 symlinked `/srv/priori/shared/.env` to
`/srv/priori/current/api/.env`. So config loading stays exactly as it is today.

Adding `EnvironmentFile=` would *also* work, but systemd's parser is stricter than
python-dotenv's: no `export`, no shell quoting, and **no inline comments after a
value** — a line like `BATCH_SIZE=1000  # cap` becomes the literal string
`1000  # cap`. That fails quietly, with a subtly wrong value rather than an error.
Not worth the risk when the app already reads the file correctly.

If you do use `EnvironmentFile=` anyway, verify what systemd actually parsed:

```bash
systemctl show priori-api --property=Environment | tr ' ' '\n' | grep -E 'ENVIRONMENT|API_V1_PREFIX'
```

nginx: change the SPA root to the symlink, leave the `/api` proxy as it is.

```nginx
root /srv/priori/current/frontend/dist;
location / {
    try_files $uri /index.html;      # SPA deep links
}
```

Apply and check, in this order:

```bash
nginx -t                              # MUST say "syntax is ok" before reloading
systemctl daemon-reload
systemctl restart priori-api
systemctl reload nginx
```

**Verify — the site must be exactly as it was:**

```bash
curl -s https://accounting.priori.co.ke/api/v1/health
# {"status":"healthy",...,"environment":"production"}
curl -sI https://accounting.priori.co.ke | head -1        # 200
systemctl status priori-api --no-pager | head -5          # active (running)
```

Open the site and log in. **If anything is wrong, revert:** point nginx's `root` back
at the old docroot, restore the old unit, `systemctl restart priori-api`. Nothing has
been deleted — the original directories are untouched.

### 2.5 The free verification run — **while `PROD_SSH_KEY` is still unset**

> ⚠️ **Order matters here.** This run is safe *only because the SSH secrets do not
> exist yet* — that is what makes it stop short of touching the droplet. Once §2.6 is
> done, this exact command performs a **real production deploy**. Do this step first.

It costs one run and proves the three things that cannot be checked locally:
`secrets: inherit` gives gitleaks a usable token; CodeQL can upload under the
caller's permissions; and — the important one — `download-artifact` resolves an
artifact produced by a *called* workflow. That last step is the only one that would
fail **after** all CI has already passed, which is the most expensive place to find a
problem.

```bash
gh secret list | grep PROD_SSH_KEY   # must print NOTHING before you continue

gh workflow run deploy-production.yml --ref main -f confirm=deploy
gh run watch
```

Expected: `guard` → CI green → `Download OpenAPI schema` **succeeds** → `Configure
SSH` fails with `PROD_SSH_KEY is not set`. That failure is the pass condition.

A failure at the *download* step instead means something needs fixing before any real
deploy — tell me if you see it.

### 2.6 Set the production secrets

From here on, running `deploy-production.yml` deploys for real.

```bash
gh secret set PROD_SSH_KEY      < ~/.ssh/priori-prod-deploy
gh secret set PROD_KNOWN_HOSTS --body "$(ssh-keyscan -H accounting.priori.co.ke 2>/dev/null)"
gh secret set PROD_SSH_USER    --body "deploy"
gh secret set PROD_SSH_HOST    --body "accounting.priori.co.ke"

# Scheduled internal jobs (production fails loudly if these are unset — that is the alert)
gh secret set API_BASE_URL        --body "https://accounting.priori.co.ke"
gh secret set INTERNAL_API_SECRET --body "<the INTERNAL_API_SECRET from /srv/priori/shared/.env>"
```

### 2.7 First real production deploy

```bash
gh workflow run deploy-production.yml --ref main -f confirm=deploy
gh run watch
```

The run summary records the exact commit, its subject, and who triggered it. On the
host the script backs up to `/srv/priori/backups/pre-<sha>.dump`, migrates, swaps the
symlink, restarts, and health-checks — rolling the symlink back automatically if the
check fails.

**Verify**

```bash
curl -s https://accounting.priori.co.ke/api/v1/health
ssh deploy@accounting.priori.co.ke 'readlink -f /srv/priori/current; ls /srv/priori/backups | tail -3'
```

### 2.8 Rehearse a rollback once, deliberately

Do not let the first rollback be during an incident.

```bash
gh workflow run rollback-production.yml --ref main \
  -f sha=$(ssh deploy@accounting.priori.co.ke 'ls /srv/priori/releases | head -1')
```

Confirm the site still serves, then deploy forward again. **The one thing rollback
does not do is revert the migration** — if a release changed the schema, the older
code may not run against it. That is why migrations should be expand/contract: add
columns nullable, backfill, and drop the old column a release later.

---

## Part 3 — Day to day

**Normal flow.** Merge to `develop` → staging updates itself. When you want that in
production, merge `develop` → `main`, then run `deploy-production.yml` and type
`deploy`.

**Watch for:**

- The `Scheduled internal jobs` workflow going red — the outbox is not draining, or
  mail has dead-lettered. Both are real alerts.
- `/srv/priori/releases` growing without bound. Prune occasionally, but **always keep
  at least the last three** — they are your rollback targets:
  ```bash
  ssh deploy@accounting.priori.co.ke \
    'ls -1dt /srv/priori/releases/* | tail -n +4 | xargs -r rm -rf'
  ```
- `/srv/priori/backups` likewise; keep enough history to matter.

**Known cost.** CI runs twice on every push to `develop` (standalone, plus inside the
deploy run). See §3.7 of [`deployment.md`](./deployment.md) for the one-line fix if
you would rather not pay for it.
