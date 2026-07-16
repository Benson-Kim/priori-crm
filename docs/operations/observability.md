# Observability: correlated logs, metrics and traces

> When something fails, you must be able to trace the full request in
> **under 60 seconds**.

## The three pillars

| Pillar | Answers | Where it lives today |
|---|---|---|
| **Logs** | *what happened* | Structured JSON logs (`app.common.logging`), every request logged by `RequestLoggingMiddleware` with `request_id`, `method`, `path`, `status_code`, `duration_ms` |
| **Metrics** | *how often* | Derived from the `duration_ms` / `status_code` log fields and the synthetic probes (see `slos.md`). Native Prometheus/OTel metrics are a follow-up (issue #4) |
| **Traces** | *where* | `X-Request-ID` correlation across frontend → API → logs. Distributed OpenTelemetry traces are a follow-up (issue #4) |

The three pillars only work when they are **correlated**. Without all three
connected by one request ID you are investigating with one eye closed —
telemetry without correlation is just noise.

## The correlation contract

- Every request gets a `request_id` (UUID).
- `RequestIDMiddleware` **honours a well-formed inbound `X-Request-ID`**
  header, so the frontend, synthetic probes and downstream services can
  propagate one ID end-to-end. Malformed values are replaced with a fresh
  UUID (a client can never inject text into logs).
- The ID is echoed on every response as the `X-Request-ID` header —
  including error and throttled (429) responses.
- Every request/response log line carries `request_id`; API error payloads
  carry `request_id` in the JSON body.

## Runbook: trace any failure in under 60 seconds

1. **Get the ID** (≈10s). From the failing HTTP response take the
   `X-Request-ID` header (or the `request_id` field of the JSON error
   body). Users reporting an error in the UI can copy it from the browser
   dev-tools Network tab.
2. **Find the logs** (≈20s). Search the log pipeline for
   `request_id:<id>`. You get exactly two anchor lines — the request
   (method, path, redacted query params, client) and the response
   (status_code, duration_ms) — plus every application log emitted while
   handling it.
3. **Localise** (≈20s). `duration_ms` tells you whether it was slow or
   failing; the interleaved module logs (service errors log with
   `exc_info`) tell you where. Analytics events (`app.analytics` logger)
   emitted during the request add the business context.
4. **Correlate wider** (≈10s). If the failure is not isolated, pivot from
   the path + status_code to the metrics view (error rate for that route)
   and to the synthetic-probe history (`scheduled:synthetic-critical-path`
   pipelines) to bound the blast radius and start time.

## Roadmap (issue #4)

- OpenTelemetry auto-instrumentation (FastAPI + SQLAlchemy) exporting OTLP
  traces, with `request_id` attached as a span attribute so log ↔ trace
  pivoting works in both directions.
- Native request-rate / error-rate / latency-histogram metrics labelled by
  route and status, feeding SLO burn-rate alerts (see `slos.md`).
