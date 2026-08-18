# Purchase Orders — DevOps runbook

Operational wiring for the Purchase Orders module's background and
infrastructure dependencies. The PO module introduces the first **email
delivery** in the Purchases section and **object-storage attachments**, both of
which need infrastructure that is independent of the feature code.

> Source of truth for endpoints: the internal jobs are FastAPI routes gated by
> the `X-Internal-Secret` header (`app.common.dependencies.verify_internal_secret`).
> If `INTERNAL_API_SECRET` is unset the endpoints **fail closed** (403).

## 1. Internal-job scheduler

The module relies on the transactional email outbox and the existing nightly
maintenance jobs. A scheduler must periodically call these machine-to-machine
endpoints (all `POST`, all gated by `X-Internal-Secret`):

| Job | Endpoint | Cadence |
| --- | --- | --- |
| Email outbox drain | `/api/v1/internal/email-outbox/drain` | ~every 5 min |
| Invoice overdue transition | `/api/v1/invoices/internal/transition-overdue` | nightly |
| Quote expired transition | `/api/v1/quotes/internal/transition-expired` | nightly |
| OTP purge | `/api/v1/auth/internal/purge-otps` | nightly |

### GitLab CI/CD Schedules (default mechanism)

The caller lives in [`.gitlab/ci/scheduled-jobs.yml`](../../.gitlab/ci/scheduled-jobs.yml)
and runs only on scheduled pipelines. Create one pipeline schedule per task
under **CI/CD > Schedules**:

| Schedule | Cron | Variable |
| --- | --- | --- |
| Outbox drain | `*/5 * * * *` | `SCHEDULED_TASK=drain` |
| Nightly maintenance | `0 2 * * *` | `SCHEDULED_TASK=nightly-transitions` |

The `nightly-transitions` job runs the invoice/quote/OTP jobs and a final
outbox drain in sequence.

Required **masked** CI/CD variables (Settings > CI/CD > Variables):

- `API_BASE_URL` — e.g. `https://priori-crm-api.onrender.com` (no trailing path).
- `INTERNAL_API_SECRET` — must equal the API's `INTERNAL_API_SECRET` env var.

### GitHub Actions Schedules (mirror)

`.github/workflows/scheduled-jobs.yml` mirrors the GitLab caller so the jobs
still run if GitHub is the default VCS. It uses two `schedule` crons plus
`workflow_dispatch`:

| Trigger | Cron | Task |
| --- | --- | --- |
| Outbox drain | `*/5 * * * *` | `drain` |
| Nightly maintenance | `0 2 * * *` | `nightly-transitions` |

Required **repository secrets** (Settings > Secrets and variables > Actions):
`API_BASE_URL` and `INTERNAL_API_SECRET` (same meaning as the GitLab variables).
Use `workflow_dispatch` to run either task on demand.

### Alternatives

- **k8s CronJob** / **cron + curl**: same four `POST`s with the
  `X-Internal-Secret` header. Use the same cadence as above.

## 2. Alerting

- **Missed / failed runs:** enable pipeline-failure notifications (or a Slack /
  PagerDuty integration) on the scheduled pipelines. Any non-2xx response makes
  the scheduled job exit non-zero, so the pipeline goes red. Alert if a
  schedule has not produced a successful run within its window.
- **Dead-lettered email:** the drain job fails when the drain response reports
  `dead > 0` (deliveries exhausted after `MAX_DELIVERY_ATTEMPTS = 5`). Treat a
  failing drain pipeline as an actionable alert and inspect the `email_outbox`
  rows in `dead` status.

## 3. AWS SES (coordinate with Send)

- Verify the sender identity / domain used by `SES_SENDER_EMAIL`.
- Request **production access** (move the account out of the SES sandbox) so
  arbitrary vendor recipients can be emailed.
- Set `AWS_REGION` to the SES region (default `af-south-1` in `render.yaml`).
- Provide `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` via the platform secret
  store (Render dashboard / k8s secret) — **never commit them**. Production
  config validation (`Settings.validate_production_hardening`) fails fast if
  the SES credentials or sender are missing in production.

## 4. Object storage (coordinate with Documents)

- Provision a bucket for PO document upload/download when
  `STORAGE_BACKEND=s3`; set `S3_BUCKET` and `S3_REGION` (and `S3_ENDPOINT_URL`
  for S3-compatible providers).
- Attach **least-privilege** IAM credentials limited to that bucket
  (`GetObject` / `PutObject` / `DeleteObject`).
- Add a lifecycle / retention policy appropriate to document-retention
  requirements.
- The default `local` backend writes under `UPLOAD_DIR` (a persistent disk in
  `render.yaml`).

## 5. Environment variables

| Variable | Purpose | Where set |
| --- | --- | --- |
| `AWS_REGION` | SES region | platform env (`render.yaml`) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | SES credentials | **secret** (dashboard / k8s secret) |
| `SES_SENDER_EMAIL` | Verified From: address | **secret/env** |
| `STORAGE_BACKEND` | `local` or `s3` | platform env |
| `UPLOAD_DIR` | Local upload path (local backend) | platform env / disk mount |
| `S3_BUCKET` / `S3_REGION` / `S3_ENDPOINT_URL` | Object storage (s3 backend) | platform env |
| `INTERNAL_API_SECRET` | Gates the internal scheduler endpoints | **secret**, and the matching scheduler CI variable |
| `BATCH_SIZE` | Export truncation cap (Excel/PDF) | platform env (optional) |

Local defaults for these are documented on the `api` service in
[`docker-compose.yml`](../../docker-compose.yml); secrets fall through to
`api/.env`.

## 6. Blob cleanup

The hard-delete paths (PO delete and document delete) purge the underlying
objects from storage after the DB row is removed
(`PurchaseOrderService._purge_storage_objects` / `delete_document`). Ensure the
deployed storage credentials grant `DeleteObject` so this path is reachable;
failures are logged (not raised) and leave a reconcilable orphan rather than a
failed request.

## 7. Pipeline coverage

`.gitlab-ci.yml` (the pipeline that fires on GitLab MR events) runs, for the
backend: `api:lint` (ruff), `api:openapi-schema` (offline OpenAPI export +
frontend type generation), `api:test` (Postgres-guarded suite), and the
GitLab-managed SAST / Dependency-Scanning / Secret-Detection analyzers. The
`.github/workflows/` definitions remain authoritative for the GitHub-side
checks (dependency scanning via pip-audit/npm audit, and secret detection via
gitleaks — see deployment.md §3.8), and `.github/workflows/scheduled-jobs.yml`
mirrors the internal-job scheduler.

Both schedulers (GitLab CI/CD Schedules and GitHub Actions Schedules) target
the same internal endpoints; enable **only one** in any given deployment to
avoid double-draining (harmless but redundant).
