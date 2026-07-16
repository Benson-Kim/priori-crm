# Email outbox dead-letter queue (DLQ)

Document emails (invoice / quote / purchase-order sends) are written to the
transactional **email outbox** (`api/app/common/email_outbox.py`) in the
same database transaction as the business-state change they announce, so a
delivery failure can never silently lose an email. The DLQ is the safety
net for messages whose delivery keeps failing.

## Lifecycle

```
pending ── delivered ──▶ sent
   │
   └─ attempt fails ─▶ failed ── retried by drainer ──▶ sent
                        │
                        └─ attempts ≥ MAX_DELIVERY_ATTEMPTS (5) ─▶ dead
```

- `pending` — enqueued, not yet attempted.
- `failed` — transient failure; the drainer retries it.
- `dead` — retry budget exhausted (**dead-lettered**); needs an operator.
- Every attempt records `attempts` and `last_error` on the row, so the
  queue itself is the audit trail.

## Draining and alerting

- `POST /api/v1/internal/email-outbox/drain` retries pending/failed rows
  oldest-first (`SKIP LOCKED`, safe for concurrent drainers).
- It runs ~every 5 minutes via the `scheduled:email-outbox-drain` job
  (`.gitlab/ci/scheduled-jobs.yml`, `SCHEDULED_TASK=drain`).
- The scheduled job **fails when the drain reports `dead > 0`**, turning
  the pipeline red — that is the DLQ alert. Configure pipeline-failure
  notifications on the schedule so it pages on-call.

## Operator runbook

All endpoints require the `X-Internal-Secret` header
(`INTERNAL_API_SECRET`).

1. **Inspect** the dead letters:

   ```bash
   curl -H "X-Internal-Secret: $SECRET" \
     "$API/api/v1/internal/email-outbox/dead?limit=50"
   ```

   Each row returns `recipient`, `subject`, `document_type/document_id`,
   `attempts` and `last_error` — enough to diagnose without SQL.

2. **Fix the root cause.** Typical causes: SES/provider outage or
   throttling (wait / raise limits), a hard-bouncing recipient address
   (correct it on the customer/vendor record), or a PDF render failure for
   the referenced document (fix the document data).

3. **Requeue** once fixed — rows return to the retry queue with a fresh
   attempt budget and the next drain delivers them:

   ```bash
   # everything (oldest 50):
   curl -X POST -H "X-Internal-Secret: $SECRET" \
     "$API/api/v1/internal/email-outbox/requeue?limit=50"

   # a single row:
   curl -X POST -H "X-Internal-Secret: $SECRET" \
     "$API/api/v1/internal/email-outbox/requeue?outboxId=<uuid>"
   ```

4. **Verify**: the next `drain` summary should report the rows as
   `delivered` and the dead list should be empty.

> Never requeue without fixing the root cause first — the rows will just
> burn their retry budget and dead-letter again.
