# Priori CRM — Architectural Code Review

> Base branch: **`develop`**. Solutions-architecture review enforcing SOLID/DRY and prioritizing Reliability, Scalability, Maintainability, and Data Integrity & Transparency. Assumes the platform must scale to high traffic and large datasets.
>
> Stack: FastAPI backend (`api/app`) + React/TypeScript frontend (`frontend/src`). Every finding is verified against files on `develop`; corrections to a parallel review are called out.

---

## 0. Owner / Document-Header Details — Requirements

A new **document owner details** settings form must capture the organisation identity printed on every generated document (invoices, quotes, expenses, bills): **Full Name, Location/Watermark, Address, Email, Phone, Tax ID/PIN Number, Website**, plus a **logo Upload/Remove**.

**Today this is hardcoded, not data-driven:**
- `frontend/src/lib/constants.ts` `COMPANY_INFO` (name/address/phone/email) is rendered by `DocumentEditor.tsx`, `DocumentViewer.tsx`, `ExpenseViewer.tsx`.
- The logo is a static asset (`/Logo Priori.svg`); the two "Update" buttons in `DocumentEditor.tsx` are dead (no `onClick`).
- `api/app/common/pdf.py` renders only `settings.APP_NAME` — no address/phone/email/tax PIN/website/logo.
- `COMPANY_INFO` and `settings.APP_NAME` have already drifted (different values), proving the dual source of truth is unsafe.
- **Tax ID/PIN and Website do not exist anywhere** today — net-new fields.
- No `owner`/`organization` backend module exists.

**Required to implement:** new backend module (`models` + Alembic migration + `schemas` + `service` + `router`, registered in `main.py`), `GET`/`PUT` profile API, logo upload/remove endpoints reusing `lib/storage.py`, and frontend wiring (an `ownerApi` + hook, a single `DocumentOwnerHeader` consumed everywhere, an edit modal behind the "Update" buttons, removal of `COMPANY_INFO`). Decide a snapshot/version policy so historical PDFs stay immutable; clarify "Location/Watermark" semantics first.

---

## 1. Unimplemented Stubs

- **S-1 · Dead "Update" buttons** in `DocumentEditor.tsx` (logo + company blocks) — no `onClick`.
- **S-2 · Owner/organization module missing** entirely.
- **S-3 · PDF header incomplete** — `pdf.py` renders only `settings.APP_NAME`.
- **S-4 · Auth never wired** — pervasive `user_id = None  # TODO: Replace with current_user.id` across invoices/quotes/vendors/expenses routers.
- **S-5 · `seed_user` dev defaults** (`"Frank"`/`"Degods"`) in `auth/service.py` must not be reachable in prod.

---

## 2. Core-Principle Violations (by category)

All items verified against `develop`.

### SOLID

- **V-SOLID-1 · High · `InvoiceService` and `QuoteService` are near-duplicate (DRY+SRP).** Verified: both define the same `_transition()` + `ALLOWED_TRANSITIONS` state machine, identical `_ref_gen()`, the same create-with-`begin_nested()`-retry loop, the same customer existence/active validation, and parallel email-template helpers. Missing abstraction. Fix: extract a `BaseDocumentService` or focused mixins (`StateMachineMixin`, `ReferenceRetryMixin`, `CustomerValidationMixin`) + a shared document-email builder.
- **V-SOLID-2 · High · DIP · `common/pdf.py` + invoice email helpers** depend on the global `settings` singleton for branding. Inject an `OwnerProfile` instead.
- **V-SOLID-3 · Medium · SRP · `pdf.py` `_build_pdf`** takes ~15 keyword params and owns all layout. Pass structured DTOs (`OwnerInfo` + document DTO).
- **V-SOLID-4 · Medium · coupling · frontend** — owner header re-implemented in three components. Extract one `DocumentOwnerHeader`.

### DRY

- **V-DRY-1 · Critical · Company identity duplicated and drifted** — `COMPANY_INFO` (3 components) vs `settings.APP_NAME` (`pdf.py`, invoice email). Single owner profile + context; delete `COMPANY_INFO`.
- **V-DRY-2 · High · Service duplication** (see V-SOLID-1).
- **V-DRY-3 · Medium · Upload guard constants** inline in `expenses/router.py` (`MAX_UPLOAD_SIZE_BYTES`, `ALLOWED_MIME_TYPES`, `ALLOWED_EXTENSIONS`, traversal strip). Centralize in `common/uploads.py` before the logo uploader copies them.
- **V-DRY-4 · Medium · Overdue logic** — `common/financial.check_is_overdue`/`calculate_days_overdue` exist but invoice models/schemas recompute inline. Route all overdue logic through the helper.

### Reliability

- **V-REL-1 · Critical · No auth on any endpoint.** Plumbing exists (`security.py` `HTTPBearer`/`decode_access_token`, `dependencies.py` `CurrentUser`) but no router applies it, and `frontend/src/lib/api.ts` sends no `Authorization` header. Every endpoint is publicly callable.
- **V-REL-2 · Critical · `cancel_invoice` bypasses the state machine** — sets `invoice.status = CANCELED` directly, skipping `_transition()`. Use `self._transition(...)`.
- **V-REL-3 · High · `record_payment` has no row lock** — loads via `get_by_id` (no `with_for_update()`); concurrent payments can overpay. `send_invoice` shows the correct pattern.
- **V-REL-4 · Medium · Redundant `commit()` in `AuthService`** fragments the `get_db()` transaction boundary; use `flush()`.
- **NOT a finding — `@retry` on `send_otp`:** verified correctly applied (legal blank line between decorator and `def`). Rejected.

### Scalability

- **V-SCALE-1 · High · N+1 in `export_invoices_to_excel`** — one `get_by_id` per row. Batch-fetch with `joinedload(line_items)`.
- **V-SCALE-2 · Medium · Single-node local storage** (`lib/storage.py`) — logo + expense uploads won't survive horizontal scaling. Implement the S3 backend the class is shaped for.
- **V-SCALE-3 · Medium · No caching for the owner singleton** — read on every render/PDF. Cache + invalidate on `PUT`.
- **V-SCALE-4 · Medium · `Customer.invoices` uses `lazy="dynamic"`** — verified on `develop`. `dynamic` is discouraged in SQLAlchemy 2.x; prefer `select`/`selectin` with explicit pagination to avoid surprising query semantics.
- **V-SCALE-5 · Low · Synchronous PDF generation** in the request path. Acceptable now; revisit only if profiling shows latency.

### Maintainability

- **V-MAINT-1 · High · Inconsistent ORM style** — `auth/models.py` uses legacy `Column()`; all other models use `Mapped[]`+`mapped_column()`. Standardize.
- **V-MAINT-2 · High · Broken CI.** `ui-ci.yml` targets `ui/` but the app is in `frontend/` (path filter never matches; would fail if run). `api-ci.yml` env (malformed `DATABASE_URL`, 16-char `JWT_SECRET_KEY`, `ENVIRONMENT: test`) violates `config.py` validators, so settings can't import and `pytest` never meaningfully runs.
- **V-MAINT-3 · Medium · Thin tests** — no CRUD/API tests for customers/invoices/quotes/vendors/expenses; no frontend tests.
- **V-MAINT-4 · Medium · Auth models missing from `main.py` registry** — `User`/`OTPCode` not imported with the other models; Alembic autogenerate can miss them.

### Data Integrity

- **V-DI-1 · Critical · Audit fields always NULL** — `created_by`/`updated_by`/`recorded_by` never set because auth is unwired (depends on V-REL-1).
- **V-DI-2 · High · `Customer.quotes` uses `cascade="all, delete-orphan"`** — verified on `develop`. Deleting a customer destroys all their quote history irrecoverably. Note the asymmetry: `Customer.invoices` uses the safe `cascade="save-update, merge"` while quotes use the destructive cascade. Fix: change quotes to `save-update, merge` (match invoices) and use soft-delete for customers.
- **V-DI-3 · High · `duplicate_invoice` response-type mismatch** — service returns an ORM `Invoice`; router validates it against `InvoiceDuplicateResponse`. Likely runtime error; construct the response explicitly.
- **V-DI-4 · High · Owner-detail immutability** — a mutable singleton would retroactively alter issued documents. Snapshot or version.
- **V-DI-5 · Medium · Reference collisions after hard-delete** — `common/reference.py` uses COUNT(*)+1; `use_max_strategy` exists but invoices/quotes don't use it.
- **V-DI-6 · Medium · Denormalized `Customer.balance`** — stored `Numeric(15,2)` (`>= 0` CHECK) recomputed by `InvoiceService._update_customer_balance` via a separate query/flush. Risk of drift if a balance-affecting path forgets to recompute. Recompute within the same unit of work, or derive on read.

### Correction to a parallel review

A prior pass (run against a different branch) recorded `Customer.quotes` cascade and `Customer.invoices` `lazy="dynamic"` as **false / non-existent**. On **`develop`** both relationships **do exist** and those claims are **TRUE** (V-DI-2, V-SCALE-4). The discrepancy was a branch difference — the `Customer` model on the default branch had no relationships, but `develop` adds them.

---

## 3. Stakeholder-Tagged Notes

- **🔒 @ai-security-analyst** — V-REL-1 (unauth API), no RBAC column on `User`, wide-open CORS (`allow_methods/headers=["*"]` + credentials), new logo-upload attack surface. Route to GitLab's **Security Analyst Agent**.
- **⚙️ @ci-cd** — V-MAINT-2 (both pipelines broken), no SAST/dependency/secret scanning, add migration-drift gating for the new owner table; CI is GitHub Actions on a GitLab repo (no `.gitlab-ci.yml`).
- **🏗️ @systems-architect** — V-SOLID-1/2, V-DI-2 cascade asymmetry, single-node storage, owner-profile bounded context + immutability.
- **📋 @product-owner** — dead "Update" buttons block the feature; decisions needed on Location/Watermark semantics, historical-document re-branding policy, single vs multi-org; PDFs currently lack legally-relevant sender info.

---

## 4. Prioritized Roadmap

**Phase 1 — Critical:** V-REL-1 (auth + client token), V-REL-2 (`cancel_invoice`), V-DI-2 (quotes cascade), V-DI-3 (duplicate response), V-MAINT-2 (CI), CORS restriction, V-MAINT-4 (auth registry).

**Phase 2 — Owner feature:** owner module + migration → PDF/email injection → `DocumentOwnerHeader` + `ownerApi` → logo upload (after centralizing guards) → wire "Update" buttons → immutability snapshot.

**Phase 3 — Improvements:** V-SOLID-1 service mixins, V-DI-5/V-DI-6 (reference MAX strategy, balance recompute), V-REL-3 row lock, V-SCALE-1 N+1, V-SCALE-2 S3, caching, tests, ORM-style standardization.

**Key dependencies:** V-REL-1 unblocks V-DI-1 audit attribution; owner module precedes PDF/header/immutability work; centralize upload guards before the logo uploader.

---

## 5. Workflow-Trace Findings (fifth-pass)

These were found by tracing complete runtime workflows end-to-end (request lifecycle → middleware → handlers → service → model), rather than reading files in isolation. Each is verified against `develop`.

### Workflow A — HTTP request lifecycle & error handling

- **W-1 · Reliability · High · Rate-limit exception is not caught by the exception handlers.** `RateLimitMiddleware.dispatch()` raises `RateLimitException`, but `register_exception_handlers` installs `@app.exception_handler(AppException)` at the *app* layer. Starlette `BaseHTTPMiddleware` runs **outside** that layer, so a throttled request does not produce the intended clean `429` JSON — it surfaces as an unhandled error / 500. Fix: return a `JSONResponse(status_code=429, ...)` directly from the middleware instead of raising, or move rate limiting into a dependency/route layer where the handler applies.
- **W-2 · Reliability · Medium · `Retry-After` header never sent.** `RateLimitException(retry_after=60)` only places `retry_after` in the JSON `details`; the `AppException` handler sets no `Retry-After` HTTP header. Compliant clients/back-off libraries can't honor it. Fix: set the `Retry-After` header on 429 responses.
- **W-3 · Observability · Medium · Throttled/errored responses lose `X-Request-ID` / `X-Response-Time`.** Because the rate-limit middleware raises (W-1) and is registered after `RequestIDMiddleware`/`RequestLoggingMiddleware` in `main.py`, the logging middleware's post-call header/timing code does not run for throttled requests. Tracing headers are missing exactly when they're most useful. Fix follows from W-1.

### Workflow B — Rate limiting under scale

- **W-4 · Scalability · High · In-memory, per-process rate limiter.** `RateLimitMiddleware` stores counters in a process-local `OrderedDict`. With multiple Uvicorn workers or horizontally-scaled instances, the effective limit becomes `workers × RATE_LIMIT_PER_MINUTE` and is not shared. Fix: back the limiter with Redis (or an API-gateway limiter) for a shared window.
- **W-5 · Reliability · Medium · Client identity is the raw socket IP.** It keys on `request.client.host` with no `X-Forwarded-For`/`Forwarded` handling. Behind a load balancer or proxy, **all** users collapse into one bucket (everyone throttled together), while the code comment admits "use user ID in production." Fix: derive client identity from a trusted forwarded header (validated) and/or the authenticated user once auth lands (V-REL-1).

### Workflow C — Input normalization & contact data integrity

- **W-6 · Data Integrity · Medium · `normalize_phone` coerces invalid input instead of rejecting it.** In `common/validators.py`, the final `else` branch prepends the default dial code to *any* unmatched digit string, so a malformed number can be silently turned into a valid-looking E.164 value that passes the final regex. Garbage is stored as a "valid" phone. Fix: make the fall-through path raise rather than guess, and tighten national-number length checks per dial code.
- **W-7 · Reuse · Low · Owner-details plan should reuse existing validators.** `common/validators.py` already provides `normalize_phone`, `validate_country_code`, `capitalize_location`, and `empty_str_to_none`. The owner email/phone/website/location fields must reuse these (and the `Vendor` `@validates` email/website normalisers) rather than re-implement.

### Workflow D — Reference generation consistency

- **W-8 · Data Integrity · Medium · Inconsistent reference strategy across modules (confirms V-DI-5).** `ExpenseService._generate_expense_reference` correctly uses `use_max_strategy=True` (collision-safe after deletes), but `InvoiceService`/`QuoteService` use the COUNT(*)-based default. The safe pattern already exists and should be applied uniformly to invoice/quote references.

### Correction to a parallel review

- **"`empty_str_to_none` validator is duplicated across schemas" — OVERSTATED / largely FALSE.** A single shared `empty_str_to_none` already exists in `common/validators.py`. If individual schemas re-declare a local copy, the remedy is simply to import the shared one; this is not a missing-abstraction problem.

### Pagination note (verified clean)

- `common/pagination.py` is sound: `PaginationParams` enforces `ge=1`, `le=100`; `PaginatedResponse.create` computes `total_pages`/`has_next`/`has_prev` correctly with a `max(1, ...)` guard. The only related concern remains the `COUNT(*)`-before-pagination cost on very large tables (Low; optimize only if profiling shows it).

---

# Part II — Per-Module Deep Review

> Reviewed one module at a time. Each module is traced through its full workflows (request → router → service → model → DB) and assessed against SOLID/DRY and the four pillars, with stakeholder-tagged findings: 🔒 @ai-security-analyst, 🛠️ @ai-devops-engineer, 🖥️ @senior-backend-engineer, 🎨 @senior-frontend-engineer, 🗄️ @senior-database-admin.

## Module 1 — `auth`

Files reviewed end-to-end: `modules/auth/{models,schemas,service,router}.py`, `common/security.py`, `common/dependencies.py`, `tests/test_auth.py`. Flow: `POST /auth/login` (credentials → OTP email) → `POST /auth/verify-otp` (OTP → access+refresh JWT) → `POST /auth/refresh` (new access token). `get_current_user` resolves `CurrentUser` from the bearer token.

### 🔒 @ai-security-analyst

- **AUTH-SEC-1 · Critical · OTP brute-force / no attempt limiting.** `AuthService.verify_otp` accepts any `code` with no per-OTP attempt counter, no lockout, and no throttle dedicated to `/verify-otp`. The only limiter is the global `RateLimitMiddleware`, which is per-IP, in-memory, and itself broken on the throttle path (W-1/W-4). A 6-digit code (10^6 space) within a 5-minute window is brute-forceable. **Fix:** add an `attempts` counter on `OTPCode` (or a per-user/IP counter), invalidate the OTP after N failures, and apply a strict dedicated rate limit to `/verify-otp`. Route to GitLab's Security Analyst Agent.
- **AUTH-SEC-2 · High · OTP stored in plaintext.** `OTPCode.code` is a plain `String(6)`. A DB read, backup, or log leak exposes live codes. **Fix:** store a hash of the OTP (e.g. HMAC-SHA256 with a server secret) and compare hashes; never persist the raw code.
- **AUTH-SEC-3 · High · Refresh tokens are non-revocable; no logout.** `refresh_access_token` only decodes the JWT — no server-side store, no rotation, no denylist, and there is **no logout endpoint anywhere**. A leaked refresh token is valid until expiry (up to 30 days per `JWT_REFRESH_TOKEN_EXPIRE_DAYS`). **Fix:** persist refresh tokens (or a `jti` denylist), rotate on use, and add `POST /auth/logout` that revokes them.
- **AUTH-SEC-4 · Medium · User enumeration via response timing.** `login` raises `UnauthorizedException` for a missing user **before** calling `verify_password`, so a nonexistent email returns measurably faster than a wrong password (no bcrypt work). **Fix:** always run a dummy bcrypt verify on the no-user path to equalize timing; keep the generic error message (already good).
- **AUTH-SEC-5 · Medium · Thin JWT claims.** Tokens carry only `sub`, `exp`, `type` (see `security.py`). No `jti` (so individual revocation is impossible even with a denylist), no `iat`, no issuer/audience. **Fix:** add `jti`, `iat`, and `iss`/`aud`, and validate `aud`/`iss` on decode.

### 🖥️ @senior-backend-engineer

- **AUTH-BE-1 · High · Response schema type mismatch (`UserResponse.id`).** `User.id` is `UUID(as_uuid=True)` (a Python `uuid.UUID`), but `UserResponse.id` is declared `str` with `from_attributes=True`. Pydantic v2 does not coerce `UUID → str` for a `str`-typed field, so `UserResponse.model_validate(user)` on `/verify-otp` is likely to raise at response time. **Fix:** declare `id: uuid.UUID` (or add a serializer/`field_validator` that `str()`-casts), and add a test asserting the serialized shape.
- **AUTH-BE-2 · Medium · No email normalization.** `User.email` is unique and stored as-entered; `login`/`_get_user_by_email` do an exact match. A user created as `Frank@mail.com` cannot log in as `frank@mail.com`. **Fix:** normalize email to lowercase on create and on every lookup (reuse the `Vendor` `@validates` email pattern).
- **AUTH-BE-3 · Medium · `AuthService` commits directly (reaffirms V-REL-4).** `_create_otp`, `verify_otp`, `_invalidate_pending_otps`, and `seed_user` call `self._db.commit()` while `get_db()` already owns commit/rollback. This fragments the transaction boundary and can produce partial commits. **Fix:** use `flush()` in the service; let `get_db()` commit.
- **AUTH-BE-4 · Medium · No RBAC field on `User`.** There is no role/permission column, yet other modules' docstrings assume "Manager/Owner" gates. **Fix:** add a `role` column + an authorization dependency before those gates are relied upon.
- **AUTH-BE-5 · Low · `get_current_user` compares `User.id == token["sub"]` (str vs UUID).** Works via Postgres cast but is fragile. **Fix:** cast `uuid.UUID(token["sub"])` before querying.
- **AUTH-BE-6 · Low · `seed_user` ships hardcoded names** (`"Frank"`/`"Degods"`); ensure it is unreachable in production builds.

### 🗄️ @senior-database-admin

- **AUTH-DBA-1 · Medium · Missing composite index for the OTP hot path.** `verify_otp` filters `OTPCode` by `user_id` + `code` + `is_used` and orders by `created_at desc`; `_invalidate_pending_otps` filters `user_id` + `is_used`. `otp_codes` has only the implicit PK and the FK. **Fix:** add `Index("ix_otp_user_unused_created", user_id, is_used, created_at)`.
- **AUTH-DBA-2 · Medium · Unbounded OTP table growth.** Used/expired `otp_codes` rows are never purged. **Fix:** a scheduled cleanup (delete `expires_at < now() - retention`) or a partial-index + periodic job; consider `ON DELETE CASCADE` is already set from `users`.
- **AUTH-DBA-3 · Low · `User`/`OTPCode` use legacy `Column()` style and are not in the `main.py` model registry** (V-MAINT-1/V-MAINT-4) — risks Alembic autogenerate missing these tables. **Fix:** migrate to `Mapped[]` and add `import app.modules.auth.models` to the registry.

### 🛠️ @ai-devops-engineer

- **AUTH-OPS-1 · High · Auth depends on broken CI** (V-MAINT-2). `api-ci.yml` sets `JWT_SECRET_KEY: ci-test-secret-key` (16 chars), which fails the `≥32`-char validator in `config.py`; combined with the malformed `DATABASE_URL` and disallowed `ENVIRONMENT: test`, the auth tests cannot run in CI. **Fix:** valid CI env (≥32-char secret, valid DSN, allowed environment).
- **AUTH-OPS-2 · Medium · OTP exposure in logs.** In dev mode `_send_otp_email` logs the OTP at WARNING (`"DEV MODE — OTP for %s: %s"`). Ensure this path is impossible in staging/production and that log scrapers never capture it. **Fix:** gate strictly on `ENVIRONMENT == development` (already) and add a log-redaction guard.
- **AUTH-OPS-3 · Medium · No metrics/alerting on auth failures.** No counters for failed logins, OTP failures, or refresh failures — brute-force (AUTH-SEC-1) would be invisible operationally. **Fix:** emit structured metrics and alert on failure-rate spikes.
- **AUTH-OPS-4 · Low · SES client built at import time.** `email_service = EmailService()` constructs the boto3 SES client on import; missing AWS config can fail import in some environments. **Fix:** lazy/DI construction (also helps testability).

### 🎨 @senior-frontend-engineer

- **AUTH-FE-1 · Critical · Client never sends the token (reaffirms V-REL-1/SEC-2).** `frontend/src/lib/api.ts` attaches no `Authorization` header and there is no token storage/refresh logic. The login flow obtains tokens that are never used. **Fix:** store tokens securely, attach `Authorization: Bearer`, and add a 401→refresh→retry interceptor that calls `/auth/refresh`.
- **AUTH-FE-2 · Medium · No refresh/expiry handling.** With a 30-minute access token and no interceptor, sessions will break mid-use once auth is enforced. **Fix:** implement silent refresh using the refresh token; on refresh failure, redirect to login.
- **AUTH-FE-3 · Low · OTP UX.** `OTPInput` exists (`components/ui/OTPInput.tsx`) but there is no resend/expiry-countdown wired to `OTP_EXPIRY_SECONDS`. **Fix:** surface the 5-minute expiry and a resend action consistent with backend invalidation.

### What the `auth` module does well

- bcrypt hashing via `security.py` (`hash_password`/`verify_password`) is correct.
- OTP single-use is enforced and **tested** (`test_verify_otp_reuse_blocked`), and prior unused OTPs are invalidated on new login and on successful verify.
- Generic, non-leaky error messages on login failure; OTP format validated at the schema layer (`^\d{6}$`).
- Token `type` is checked on decode (access vs refresh) — prevents using a refresh token as an access token.

### Auth module — priority order

1. AUTH-SEC-1 (OTP brute-force) + AUTH-OPS-3 (failure metrics).
2. AUTH-BE-1 (UserResponse UUID/str — likely breaks `/verify-otp` today).
3. AUTH-SEC-3 (refresh revocation + logout), AUTH-SEC-2 (hash OTP).
4. AUTH-FE-1/FE-2 (client token + refresh) — needed before enforcing auth platform-wide (V-REL-1).
5. AUTH-DBA-1/DBA-2 (OTP index + cleanup), AUTH-BE-2/BE-3/BE-4, AUTH-SEC-4/SEC-5.
6. AUTH-OPS-1 (CI), AUTH-DBA-3, remaining Low items.

---

## Module 2 — Middleware & Cross-Cutting Layer

Files reviewed end-to-end: `common/middleware.py`, `common/exceptions.py`, `common/logging.py`, `common/database.py`, `app/main.py`, `modules/health/router.py`, `tests/test_rate_limiter.py`, `tests/conftest.py`. Flow: every request passes through RequestID → RequestLogging → RateLimit → CORS, then routing, then the registered exception handlers.

### 🛠️ @ai-devops-engineer

- **MW-OPS-1 · High · Rate-limit 429 path is broken at runtime, and the test hides it.** `RateLimitMiddleware.dispatch()` raises `RateLimitException`, but Starlette `BaseHTTPMiddleware` executes **outside** the `@app.exception_handler(AppException)` layer, so the intended clean `429` JSON is never produced by the app handler (reaffirms W-1). Critically, `tests/test_rate_limiter.py::test_exceeding_limit_raises` asserts the exception is *raised from `dispatch`* — it calls `dispatch()` directly and bypasses the ASGI/handler stack, so the unit test passes while production behavior is wrong. **Fix:** return a `JSONResponse(status_code=429, headers={"Retry-After": "60"}, ...)` directly from the middleware, and add an **integration** test via the `TestClient` that asserts a real `429` + `Retry-After`.
- **MW-OPS-2 · Medium · Health/ping endpoints are rate-limited.** `/health`, `/health/detailed`, `/ping` pass through `RateLimitMiddleware`; a burst of load-balancer probes from one source IP can trip the limit and flap the service to "unhealthy." **Fix:** exempt health/ping paths from the limiter (path allowlist) before any shared-store limiter (W-4) is added.
- **MW-OPS-3 · Medium · No inbound request-ID correlation.** `RequestIDMiddleware` always generates a fresh UUID and ignores any incoming `X-Request-ID`/`traceparent`. Cross-service/gateway traces are broken. **Fix:** honor a trusted inbound correlation header when present; otherwise generate.
- **MW-OPS-4 · Low · Structured `extra` lost in non-prod logs.** `logging.py` uses JSON formatting only in production; the dev `logging.Formatter` drops the rich `extra={...}` (request_id, duration_ms) emitted everywhere. **Fix:** use the JSON formatter in all environments (pretty-print in dev) so structured context is visible locally.

### 🔒 @ai-security-analyst

- **MW-SEC-1 · Medium · No security-headers middleware.** No HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options`/frame-ancestors CSP, or `Referrer-Policy` are set. **Fix:** add a small middleware that sets standard security headers (environment-aware HSTS).
- **MW-SEC-2 · Medium · Wide-open CORS (reaffirms ISSUE-026).** `main.py` uses `allow_methods=["*"]`, `allow_headers=["*"]` with `allow_credentials=True`. With credentials enabled this is overly permissive. **Fix:** restrict methods/headers and pin origins per environment (origins are already list-parsed from config).
- **MW-SEC-3 · Low/Medium · `/health/detailed` leaks internals unauthenticated.** It returns connection-pool internals (`size`, `checked_out`, `overflow`) and DB type with no auth. **Fix:** gate the detailed variant behind auth or an internal network, keep `/health` minimal for LBs.
- **MW-SEC-4 · Low · Unhandled-exception handler leaks type/message in development only.** `unhandled_exception_handler` adds `debug.type/message` when `is_development` — correct gating, just ensure staging is not treated as development.

### 🖥️ @senior-backend-engineer

- **MW-BE-1 · Medium · Middleware ordering is fragile and intent-dependent.** Starlette runs middleware in reverse registration order; the current add order (`RequestID`, `RequestLogging`, `RateLimit`, `CORS`) plus the raising rate-limiter (MW-OPS-1) makes request-ID/timing guarantees order-sensitive on error paths. **Fix:** make the limiter return a response (not raise), and document/lock the intended order with a comment + test.
- **MW-BE-2 · Medium · No request body-size limit or server-side timeout.** There is no max-body middleware and no per-request timeout. Combined with the frontend lacking an `AbortController`, slow/large requests can tie up workers. **Fix:** add a body-size guard and a timeout (or enforce at the gateway).
- **MW-BE-3 · Low · `datetime.utcnow()` in `health/router.py`** is deprecated in 3.12 and returns naive UTC, inconsistent with the `datetime.now(UTC)` convention used elsewhere. **Fix:** use `datetime.now(UTC)`.
- **MW-BE-4 · Low · Rate limiter uses naive local `datetime.now()`** for window math — inconsistent with the UTC-everywhere convention; switch to `datetime.now(UTC)`.

### 🗄️ @senior-database-admin

- **MW-DBA-1 · High · Test database is SQLite while production is PostgreSQL.** `tests/conftest.py` uses `sqlite://`. Production-only constructs — `server_default=text("gen_random_uuid()")`, `UUID(as_uuid=True)`, GIN/`gin_trgm_ops` and full-text indexes, partial indexes (`postgresql_where`), and `pg_advisory_xact_lock` in `reference.py` — are silently ignored or behave differently on SQLite. Tests therefore validate against a database that does not represent production (e.g., advisory-lock serialization and reference-collision behavior are untested). **Fix:** run tests against PostgreSQL (the CI already starts a `postgres:16` service — point tests at it once CI env is fixed, MW-OPS / V-MAINT-2), reserving SQLite only for pure-logic unit tests.
- **MW-DBA-2 · Medium · `detailed_health_check` is not a cheap probe.** It opens a real connection and runs `SELECT 1` plus pool introspection on every call; if wired to an aggressive LB it adds load. **Fix:** use `/health` (no DB) for LB liveness and `/health/detailed` for readiness/observability only.
- **MW-DBA-3 · Low · Pool sizing vs rate limit.** `DB_POOL_SIZE` default 20 + `DB_MAX_OVERFLOW` 10 against `RATE_LIMIT_PER_MINUTE` 60/IP per process is fine now, but document the relationship so scaling workers doesn't exhaust the DB.

### 🎨 @senior-frontend-engineer

- **MW-FE-1 · Medium · Client must consume `Retry-After` and `X-Request-ID`.** Once MW-OPS-1 is fixed, `frontend/src/lib/api.ts` should back off on `429` using `Retry-After` and surface `X-Request-ID` in error reports for support correlation. **Fix:** handle `429` with backoff and log/display the request ID on errors.
- **MW-FE-2 · Low · No request timeout / abort.** The client has no `AbortController`/timeout (pairs with MW-BE-2). **Fix:** add a default timeout and abort on unmount.

### What the middleware layer does well

- The exception-handler suite in `exceptions.py` is comprehensive and consistent: typed `AppException` hierarchy, friendly mapping of `IntegrityError` (unique → DUPLICATE_RECORD, FK → INVALID_REFERENCE), a catch-all that hides internals in production, and a uniform error envelope with `request_id`.
- The rate limiter's **memory leak was correctly fixed** with an LRU-bounded `OrderedDict` (capped at `MAX_CLIENTS`) and per-request window pruning — and this is well unit-tested.
- `database.py` is solid: `pool_pre_ping`, sane pool config from settings, `expire_on_commit=False`, naming-convention metadata for clean Alembic constraints, and pool-event logging.
- Connection-pool observability via `/health/detailed` + `get_pool_status()` is a good operability primitive (just scope its exposure, MW-SEC-3).

### Middleware layer — priority order

1. MW-OPS-1 (429 returns properly + integration test) — corrects a runtime correctness bug masked by a green unit test.
2. MW-DBA-1 (test on PostgreSQL) — removes false confidence across the whole suite.
3. MW-OPS-2 (exempt health from rate limit), MW-SEC-2 (CORS), MW-SEC-1 (security headers).
4. MW-BE-2 (body size/timeout), MW-OPS-3 (inbound request-ID), MW-SEC-3 (health exposure).
5. MW-BE-3/BE-4, MW-OPS-4, MW-FE-1/FE-2, MW-DBA-2/DBA-3.

---

## Module 3 — Shared / Common Layer

Files reviewed end-to-end: `common/{dependencies,exceptions,database,pagination,financial,reference,validators,storage,pdf,excel,statement,security,logging}.py`, `lib/{config,email}.py`, `tests/{test_financial,test_statement_generator,test_reference_generator}.py`. This layer is consumed by every feature module, so defects here have blast radius across the whole platform.

### 🖥️ @senior-backend-engineer

- **SH-BE-1 · High · Money math is not quantized in `financial.calculate_line_item`.** `line_total = quantity * unit_price` and `tax_amount = line_total * tax_rate` are returned **unrounded**; only the percentage-discount path quantizes to `0.01`. Summing unrounded products produces sub-cent drift that can violate the 2-dp money columns and the `balance_due >= 0` CHECK, and makes totals non-reproducible across recalculation. The existing tests only use cleanly-dividing values (`2×100.00`, `1.5×200.00`), so the gap is **untested**. **Fix:** quantize `line_total` and `tax_amount` to `Decimal("0.01")` (`ROUND_HALF_UP`) at the line level, and define a single rounding policy reused by discounts, statements, and PDFs.
- **SH-BE-2 · Medium · `financial.get_tax_rate` silently returns 0.00 for unknown tax types.** `TAX_RATES.get(tax_type, Decimal("0.00"))` means a typo or unmapped type charges **no tax** silently — a revenue/compliance risk. The "all enum members covered" test does not exercise an *unmapped* value. **Fix:** raise on unknown tax types (the enum already constrains valid inputs; treat a miss as a programming error).
- **SH-BE-3 · Medium · `financial.build_line_items` dual accessor is fragile.** The `item.get if isinstance(item, dict) else getattr` shim returns `None` for a missing field, which then hits `None * unit_price` → `TypeError`, or silently stores `None`. **Fix:** validate required fields are present (or accept only typed Pydantic models) before calculation.
- **SH-BE-4 · Medium · `dependencies.py` services receive only `db`, never `CurrentUser`.** Every `get_*_service` factory injects the session only, so write attribution (`created_by`/`updated_by`) must be threaded by hand even after auth lands (V-REL-1/V-DI-1). Every factory also uses lazy in-function imports to avoid circular imports — a symptom of an unclean import graph. **Fix:** thread `CurrentUser` through the service factories (or a request-scoped context) and resolve the circular-import root.
- **SH-BE-5 · Low · `pdf.py` truncates descriptions with `str(item.description)[:40]`** — silent cut, no ellipsis/wrap on generated documents. **Fix:** wrap text or append an ellipsis.
- **SH-BE-6 · Low · `security.py` `HTTPBearer()` uses `auto_error=True`** — a missing token returns FastAPI's default 401 shape, not the app's `UnauthorizedException` envelope, so error responses become inconsistent once auth is applied. **Fix:** `HTTPBearer(auto_error=False)` and raise `UnauthorizedException` explicitly.

### 🗄️ @senior-database-admin

- **SH-DBA-1 · Medium · `reference.py` COUNT strategy collides after hard-deletes (reaffirms V-DI-5/W-8).** Date-scoped and global references use `COUNT(*)+1`; `ExpenseService` already uses the safe `use_max_strategy=True`. **Fix:** apply MAX-based generation to invoice/quote references too.
- **SH-DBA-2 · Low · Advisory-lock + reference behavior is untested against PostgreSQL.** `pg_advisory_xact_lock` is a no-op on the SQLite test DB (MW-DBA-1), so the serialization guarantee in `reference.py` is effectively unverified. **Fix:** cover reference generation with a Postgres-backed concurrency test.
- **SH-DBA-3 · Low · `excel.py` casts every `Decimal` to `float`.** Fine for display, lossy if a sheet is ever re-imported as source data. **Fix:** keep money as string/decimal if round-tripping is ever required.

### 🔒 @ai-security-analyst

- **SH-SEC-1 · Medium · `storage.py` path safety lives only at the call site.** The shared `StorageService.upload_file` joins `directory`/`filename` directly; traversal protection is implemented in `expenses/router.py` (the `PurePosixPath(...).name` strip), not in the shared service. Any future caller (e.g. the owner-logo uploader) that forgets the strip is exposed. **Fix:** sanitize and `Path.resolve()`-confine inside `StorageService` itself, and centralize MIME/size guards (ISSUE-013/V-DRY-3).
- **SH-SEC-2 · Low · `config.py` keeps dead `MOCK_PAYMENT_GATEWAY_KEY`.** Unused secret-shaped config invites confusion/misuse. **Fix:** remove until a real gateway integration exists.

### 🛠️ @ai-devops-engineer

- **SH-OPS-1 · High · `config.py` `ENVIRONMENT` Literal omits `test`** — root cause of the CI failure (V-MAINT-2/AUTH-OPS-1): the API CI sets `ENVIRONMENT: test`, which the validator rejects, so settings can't load. **Fix:** add `test` to the Literal (or set CI to `development`), alongside the ≥32-char `JWT_SECRET_KEY` and valid `DATABASE_URL` fixes.
- **SH-OPS-2 · Medium · `excel.py` builds whole workbooks in memory, synchronously.** Combined with the N+1 fetch (V-SCALE-1) and `BATCH_SIZE=1000`, large exports are memory-heavy and block a worker. **Fix:** stream/paginate exports and/or move to a background job with a download link.
- **SH-OPS-3 · Low · `email.py` `EmailService()` constructed at import** (reaffirms AUTH-OPS-4) — boto3 SES client built on import; prefer lazy/DI for testability and resilience.

### 🎨 @senior-frontend-engineer

- **SH-FE-1 · Medium · Money rounding must match backend.** If SH-BE-1 is fixed server-side, the frontend `calculateTotals` (in `components/documents/utils.ts`) must use the **same** rounding policy or the previewed totals will disagree with the persisted/PDF totals. **Fix:** mirror the backend rounding rule (round per line, 2 dp) on the client.
- **SH-FE-2 · Low · Brand constant duplication.** The brand color `#1A1A2E` is repeated in `pdf.py`, `excel.py`, and Tailwind/frontend styles. **Fix:** single source for brand tokens shared across PDF/Excel/UI where feasible.

### DRY observations (shared layer)

- **SH-DRY-1 · Medium · Customer/vendor display-name fallback duplicated** across `pdf.py`, `excel.py`, and the invoice list query (`getattr(.., "display_name", str(id))`). Centralize a single `display_name` resolver.
- **SH-DRY-2 · Low · Brand color/styling constants duplicated** (SH-FE-2).

### What the shared layer does well

- **`statement.py` is a model deep module:** a pure, well-documented `StatementGenerator` that both Customer and Vendor statements delegate to (real DRY win), with deterministic debit-before-credit ordering on equal dates and clean dataclass inputs. (Only caveat: it inherits the unrounded-decimal policy, SH-BE-1.)
- **`excel.py` and `pdf.py` are genuinely "deep modules"** — small public surface (`export_invoices/quotes/expenses`, `generate_invoice/quote_pdf`) over concentrated formatting logic; good separation.
- **`exceptions.py`, `pagination.py`, `database.py`** are well-built (covered in Module 2) and `financial.calculate_discount`/`check_is_overdue` are correct and well-tested.
- **`validators.py`** provides reusable, centralized helpers (`empty_str_to_none`, `normalize_phone`, `validate_country_code`) — the right place for owner-detail validation to reuse (caveat: the `normalize_phone` coercion bug, W-6).

### Shared layer — priority order

1. SH-BE-1 (quantize money) + SH-FE-1 (match on client) — cross-cutting correctness; affects invoices, quotes, expenses, statements, PDFs, Excel.
2. SH-OPS-1 (CI `ENVIRONMENT`) — unblocks the entire test suite.
3. SH-BE-2 (tax-rate fallback), SH-BE-3 (line-item validation), SH-SEC-1 (storage path safety in the service).
4. SH-DBA-1 (MAX references), SH-OPS-2 (export streaming), SH-DRY-1 (display-name helper).
5. SH-BE-4 (CurrentUser in factories — with V-REL-1), SH-BE-5/BE-6, SH-DBA-2/DBA-3, SH-SEC-2, SH-OPS-3, SH-FE-2.

---

## Module 4 — `customers`

Files reviewed end-to-end: `modules/customers/{models,schemas,service,router}.py`. Flow: CRUD + activate/deactivate + soft/hard delete (with pre-delete check) + financial summary + invoice history + statement generation. No tests exist for this module.

### 🖥️ @senior-backend-engineer

- **CUST-BE-1 · Critical · `GET /customers/{id}` calls a non-existent service method.** The router's `get_customer` calls `service.get_customer_invoices(customer_id, ...)`, but `CustomerService` only defines `get_invoices` (the method is `get_customer_invoices` on the *router*, not the service). This raises `AttributeError` → 500 on **every** customer detail view. It is undetected because there is no test for the endpoint. **Fix:** call `service.get_invoices(...)`; add an endpoint test.
- **CUST-BE-2 · High · Silent error-swallowing on the detail endpoint.** `get_customer` wraps statement generation in `except Exception: statement = None`, which hides real failures (including CUST-BE-1-class bugs and DB errors) behind a blank statement. **Fix:** catch only expected, narrow exceptions; let unexpected errors surface to the handler.
- **CUST-BE-3 · Medium · Contradictory required/optional fields in `CustomerCreate`.** `address`, `country`, `province`, `city`, `postal_code` are typed `str | None` but declared required via `Field(..., min_length=...)`. The type says optional; the field says required. **Fix:** make the type and requiredness consistent (either truly optional with defaults, or `str` + required).
- **CUST-BE-4 · Medium · `update()` can set `status` directly, bypassing business rules.** `CustomerUpdate.status` lets a `PUT` flip a customer to `inactive`/`deleted` without the balance/open-quote guards enforced in `deactivate()`/`delete()`. **Fix:** remove `status` from `CustomerUpdate` and route status changes only through the dedicated endpoints.
- **CUST-BE-5 · Low · Misleading return type.** `list_customers` is annotated `-> PaginatedResponse[CustomerResponse]` but builds `CustomerSummary` items (router correctly says `CustomerSummary`). **Fix:** correct the service annotation.
- **CUST-BE-6 · Low · No write attribution.** `CustomerService.create/update/delete` take no `user_id`, and `Customer` has no `created_by`/`updated_by` (unlike `Vendor`/`Invoice`). Ties to V-DI-1/V-REL-1. **Fix:** add audit columns + thread `CurrentUser`.

### 🗄️ @senior-database-admin

- **CUST-DBA-1 · High · Hard-delete triggers the destructive quote cascade (reaffirms V-DI-2).** `delete(hard_delete=True, force=True)` runs `self._db.delete(customer)`. `Customer.quotes` is `cascade="all, delete-orphan"`, so this **silently deletes all the customer's quotes and their line items** — while invoices are protected by FK `RESTRICT`. The pre-delete check only *warns*; `force=true` removes the guard. **Fix:** change `Customer.quotes` to `cascade="save-update, merge"` (match invoices), and never hard-delete with related quotes; prefer soft-delete.
- **CUST-DBA-2 · Medium · Case-sensitive email uniqueness.** `Customer.email` is `unique=True` on the raw string and is not normalized on create/update, so `Alpha@x.com` and `alpha@x.com` can both exist as separate customers. **Fix:** normalize email to lowercase before insert/update (and add a case-insensitive unique index / citext).
- **CUST-DBA-3 · Medium · Detail view runs many aggregate queries per request.** `get_customer` fires `get_by_id` + `get_financial_summary` + invoice list/count + a **full-year statement** (additional invoice/payment scans). Ensure `invoices(customer_id, status)` and `payments(...)` composite indexes exist; consider not generating a full statement on every detail load. **Fix:** lazy-load the statement (separate endpoint/tab) and verify supporting indexes.

### 🔒 @ai-security-analyst

- **CUST-SEC-1 · High · Soft-deleted customers leak through reads.** `delete()` sets `status = DELETED`, but `list_customers` (default, no filter), `get_by_id`, financial summary, and statements never exclude `DELETED`. Deleted records remain visible/queryable. **Fix:** exclude `DELETED` by default everywhere and require an explicit filter to surface them.
- **CUST-SEC-2 · Medium · Unauthenticated + dangerous `force`/`hard_delete` flags.** With auth unwired (V-REL-1), any caller can hit `DELETE /customers/{id}?hard_delete=true&force=true` and irreversibly destroy a customer and (via CUST-DBA-1) their quotes. **Fix:** gate destructive flags behind authn + an elevated role (RBAC, AUTH-BE-4).

### 🛠️ @ai-devops-engineer

- **CUST-OPS-1 · Medium · Statement-default query params are mistyped.** In `generate_customer_statement`, `period_start`/`period_end` are `Annotated[date, Query(...)] = None` — a non-optional `date` defaulting to `None`. This is inconsistent and can surface as schema/validation oddities. **Fix:** type them `date | None`.
- **CUST-OPS-2 · Medium · No tests at all for customers.** CRUD, soft/hard delete gating, deactivate balance rules, and statement generation are entirely uncovered — CUST-BE-1 (a 500 on the detail page) would have been caught by one endpoint test. **Fix:** add CRUD + delete-eligibility + statement tests (Postgres-backed, per MW-DBA-1).

### 🎨 @senior-frontend-engineer

- **CUST-FE-1 · Medium · `recent_invoices` is an untyped `list[dict]`.** `CustomerDetailResponse.recent_invoices` is loosely typed dicts assembled in the router; the frontend has no contract and can silently break on shape changes. **Fix:** define a typed `RecentInvoice` schema and return it.
- **CUST-FE-2 · Low · alias/casing consistency.** `CustomerCreate` mixes camelCase aliases with `populate_by_name` and the examples mix `customerType` and `customer_type`; ensure the frontend consistently sends one casing. **Fix:** standardize on the camelCase aliases the UI uses and document it.

### What the `customers` module does well

- **Delete is genuinely well-designed at the API level:** soft-delete default, a dedicated `delete-check` endpoint that surfaces associated invoices/quotes/payments and outstanding balance, and clear `can_hard_delete` gating — a strong data-integrity posture (the cascade in CUST-DBA-1 is the one hole to close).
- **Idempotent `activate`/`deactivate`** with sensible business rules (block deactivation on outstanding balance / open quotes; block activating a deleted customer).
- **Statement generation delegates to the shared `StatementGenerator`** rather than reimplementing the ledger loop — a real DRY win, with a correct opening-balance computation.
- **Schema validation is strong and reuses the shared validators** (`normalize_phone`, `validate_country_code`, `capitalize_location`, `empty_str_to_none`) and enforces the business-customer-needs-company-name rule via a model validator.
- Consistent, typed exception handling on every service method.

### Customers module — priority order

1. CUST-BE-1 (detail endpoint 500) + CUST-OPS-2 (one endpoint test would catch it).
2. CUST-DBA-1 (quote cascade data-loss on hard delete) + CUST-SEC-2 (gate destructive flags).
3. CUST-SEC-1 (exclude soft-deleted from reads), CUST-BE-4 (status bypass), CUST-DBA-2 (email normalization).
4. CUST-BE-2 (stop swallowing errors), CUST-DBA-3 (detail-page query load), CUST-OPS-1 (param types), CUST-BE-3 (field requiredness).
5. CUST-BE-5/BE-6, CUST-FE-1/FE-2.

---

## Module 5 — `vendors`

Files reviewed end-to-end on `develop`: `modules/vendors/{models,schemas,service,router}.py`, `frontend/src/pages/purchases/vendors/{index,detail}.tsx`, `frontend/src/services/vendorApi.ts`; cross-checked against `modules/expenses/models.py` (the only live transaction source), `common/statement.py`, `common/dependencies.py`. Flow: CRUD + activate/deactivate + hard-delete (open-transaction guarded) + paginated transaction list + payables summary + period statement + contact search + real-time duplicate-email check. The `Vendor.expenses` relationship is `lazy="noload"` and the `expenses.vendor_id` FK is `ondelete="RESTRICT"` — so the destructive-cascade class of bug seen in `customers` (CUST-DBA-1) does **not** exist here. No tests exist for this module.

> Note on Bills: every aggregate (`_compute_payables_*`, `_has_open_transactions`, `get_vendor_transactions`) is written as an Expense-only query with the Bill half commented out and the `union_all` collapsed to `expense_rows.subquery()`. The "expenses + bills" wording in docstrings/`response_model` descriptions is therefore **aspirational, not implemented** — payables and the transaction list silently reflect expenses only.

### 🖥️ @senior-backend-engineer

- **VEND-BE-1 · Critical · `update()` lets a `PUT` set `status` directly, bypassing the `_transition` state machine.** `VendorUpdate(VendorBase)` adds `status: VendorStatus | None`, and `update()` applies every key via `setattr(vendor, field, value)`. A client can `PUT {"status": "inactive"}` (or any DB-CHECK-valid value) without going through `activate`/`deactivate`, defeating `ALLOWED_TRANSITIONS` and bypassing the version-bump/audit path those methods own. This is the same class as V-REL-2 (`cancel_invoice`) and CUST-BE-4. **Fix:** remove `status` from `VendorUpdate` and route status changes only through the dedicated endpoints (or have `update()` reject `status` and delegate to `_transition`).
- **VEND-BE-2 · High · `get_detail()` calls a non-existent `self._build_vendor_response(...)` → guaranteed `AttributeError`.** Only `_attach_payables` exists; there is no `_build_vendor_response`. The method is currently **unrouted** (no endpoint calls it; the router builds `VendorResponse.model_validate(vendor)` directly), so it is latent — but `VendorDetailResponse`, `get_detail`, and `next_actions` are dead, untested code that will 500 the moment anything wires them. **Fix:** delete `get_detail`/`VendorDetailResponse` or implement `_build_vendor_response`; add a test when it goes live.
- **VEND-BE-3 · High · Optimistic locking is a non-atomic check-then-set with no row lock.** `update()` compares `vendor.version != expected_version` in Python, then `setattr`s and does `version += 1` on a row loaded without `with_for_update()`. Two concurrent writers that both pass the check will both write (last-flush-wins); the lost-update the `version` column was added to prevent can still occur. The model also does **not** use SQLAlchemy's native `__mapper_args__ = {"version_id_col": version}`, so the DB never enforces the guard. **Fix:** use `version_id_col` (lets the UPDATE … WHERE version = :expected fail atomically) or `SELECT … FOR UPDATE` + re-check.
- **VEND-BE-4 · Medium · `VendorResponse` is populated by monkey-patching ORM instances.** `_attach_payables` sets `vendor.payables/total_unpaid/overdue_total` as ad-hoc Python attributes on the mapped `Vendor` before `model_validate`. It works only because `VendorResponse` defaults those fields; it is fragile (a typo silently yields defaults), couples the service to response shape, and muddies SRP. **Fix:** build and return the `VendorResponse` (or a DTO) explicitly rather than attaching transient attributes to the entity.
- **VEND-BE-5 · Medium · Three near-identical Expense aggregation queries (DRY).** `_compute_payables_for_vendor`, `_compute_payables_bulk`, and `get_vendor_transactions` each re-build the same Expense→(balance,status) projection + the same commented-out Bill union scaffold. When Bills land, all three must be edited in lockstep. **Fix:** extract one `_payable_transactions_query(vendor_ids)` (and a single place that knows how to union Bills) and derive the three callers from it.
- **VEND-BE-6 · Low · `currency.upper()` on create can raise on `None` despite the guard ordering.** `create()` does `data.currency.upper() if data.currency else Currency.KES`; safe today, but the schema `validate_currency_code` runs `v.upper()` in an `after` validator typed `-> str` while the field is `Currency | None` — if `currency` is ever `None` the validator raises `AttributeError` rather than a clean 422. **Fix:** guard `None` in `validate_currency_code` or make the field non-optional with a default.
- **VEND-BE-7 · Low · Dead imports / scaffolding.** `service.py` imports `and_`, `dataclass`, `VendorResponse`, `CLOSED/OPEN_PAYABLE_STATUSES` constants that are unused on the live path (`OPEN_PAYABLE_STATUSES` lists `partial`/`sent`, which aren't even valid Expense statuses). Noise that misleads the next reader. **Fix:** remove unused imports/constants.

### 🗄️ @senior-database-admin

- **VEND-DBA-1 · Medium · `currency` is free-text `String(3)` with no DB-level domain.** Unlike `status` (which has `ck_vendors_valid_status`), currency has only a schema-side length/upper check; a direct write or future code path can persist an unknown ISO code. **Fix:** add a `CHECK currency IN (...)` or FK to a currencies table, consistent with how `status` is constrained.
- **VEND-DBA-2 · Medium · Search can't use an index and isn't `LIKE`-safe.** `list_vendors` search does `ilike('%term%')` across `vendor_name/email/phone_primary/phone_secondary`; the leading wildcard precludes use of `ix_vendors_status_name`/`ix_vendors_email`, and `%`/`_` in the term are not escaped, so user input alters the pattern. On a large vendor table this is a full scan per keystroke (the UI searches on change). **Fix:** add a `pg_trgm` GIN index for name/email and escape `LIKE` metacharacters (or use a parameterized FTS), consistent with the trigram approach noted for customers.
- **VEND-DBA-3 · Medium · `get_status_counts` and `list_vendors` ignore the `version`/soft-delete dimension and assume only two statuses.** `get_status_counts` hardcodes `active + inactive`; any third status (or NULL) silently drops out of `all`. There is also no soft-delete concept, so `delete()` is a hard `DELETE` — fine given the `RESTRICT` FK, but it means a vendor with **only paid/closed** expenses is deletable, orphaning historical expense rows' `vendor_id` lookups at the app layer (the row remains via RESTRICT only while open). **Fix:** derive `all` from `SUM(count)` regardless of status; confirm the product intent of hard-deleting vendors that have closed history (prefer soft-delete/archival for auditability).
- **VEND-DBA-4 · Low · `_calculate_balance_at_date` sums `total_due` (gross) not the net charged, and re-scans expenses/payments per statement.** Opening balance = Σ`total_due` before date − Σ`payment.amount` before date. This is correct only if `total_due` is never revised post-issue; combined with the unrounded-decimal policy (SH-BE-1) it inherits any sub-cent drift. **Fix:** confirm immutability of issued `total_due`; reuse the shared rounding policy. Index support exists (`ix_expenses_vendor_status`, `ix_expense_payments_payment_date`).

### 🔒 @ai-security-analyst

- **VEND-SEC-1 · Critical · Every vendor endpoint is unauthenticated and write attribution is always NULL (reaffirms V-REL-1 / V-DI-1).** All eight routes carry `user_id = None  # TODO` and no `CurrentUser` dependency. Anyone can create/edit/activate/deactivate/**hard-delete** vendors and read every vendor's payables/statement. `created_by`/`updated_by` (columns that exist) are never populated, so there is no audit trail. **Fix:** apply the `CurrentUser` dependency and thread `current_user.id`; gate `DELETE` behind an elevated role (RBAC, AUTH-BE-4). Route to GitLab's Security Analyst Agent.
- **VEND-SEC-2 · Medium · Duplicate-email & contact-search endpoints are an unauthenticated enumeration surface.** `GET /vendors/check-email` confirms whether any email is already a vendor (and returns the vendor's name/ID), and `GET /vendors/contacts/search` returns CRM contact PII (name, email, phone, address). Unauthenticated, these leak supplier and contact data and enable email enumeration. **Fix:** require auth; consider returning a boolean (no name/ID) until the user is authorized to view the record.
- **VEND-SEC-3 · Low · Stored vendor `website` is rendered as a clickable link without scheme allow-listing.** The model/schema normalisers prepend `https://` only when no scheme is present, so an attacker-supplied `http://`-or-otherwise value is stored verbatim and `detail.tsx` renders `<a href={vendor.website} target="_blank">` (with `rel="noreferrer"`, good). Low risk given normalisation, but there's no allow-list of `http/https`. **Fix:** validate the scheme is `http(s)` on input.

### 🛠️ @ai-devops-engineer

- **VEND-OPS-1 · Medium · No tests for the entire module** — CRUD, the status-transition guard (VEND-BE-1), optimistic-locking races (VEND-BE-3), payables aggregation, and statement math are all uncovered. VEND-BE-2 (the `get_detail` AttributeError) would be caught by a single call. Tests must run on PostgreSQL (the payables `case`/`coalesce(Decimal)`, partial unique index `uq_vendors_email_not_null`, and `gen_random_uuid()` don't behave on the SQLite test DB — MW-DBA-1). **Fix:** add service + endpoint tests against Postgres.
- **VEND-OPS-2 · Medium · `_attach_payables` runs an aggregate per vendor on the single-vendor read paths; the list path is N+1-free but unbounded.** `get_by_id`/`activate`/`deactivate`/`update` each fire `_compute_payables_for_vendor` (one extra aggregate) on every call — acceptable, but every mutating call pays a read it may not need. `list_vendors` correctly batches via `_compute_payables_bulk` (good), yet computes payables for the whole page even when the caller only needed to flip a status. **Fix:** compute payables lazily (only for read/detail responses), not after writes.
- **VEND-OPS-3 · Low · Broad `noqa: F401` / silent `except ImportError` everywhere hides real wiring errors.** The `try: import Expense … except ImportError: return zeros` pattern is used in six places. Since `expenses` now exists on `develop`, an `ImportError` would today mask a genuine regression (e.g. a renamed model) as "vendor has no payables" rather than failing loudly. **Fix:** import `Expense` normally at module top now that the dependency is real; drop the defensive fallback.

### 🎨 @senior-frontend-engineer

- **VEND-FE-1 · High · Statement "To" block reads `statement.vendor.phone`, which the API never returns.** `vendorApi.ts` types `VendorStatement.vendor` with a `phone` field, but the backend `VendorResponse` exposes `phone_primary` (no `phone`). `detail.tsx` renders `{statement.vendor.phone}` → always `undefined`/blank on the printed statement. **Fix:** align the TS interface and the JSX to `phone_primary` (and reuse the full `Vendor` type instead of a hand-rolled subset).
- **VEND-FE-2 · High · Statement header uses hardcoded `COMPANY_INFO` and a static logo (reaffirms V-DRY-1 / owner-details feature).** `detail.tsx` imports `COMPANY_INFO` and `/Logo Priori.svg` for the printed statement — the same dual-source-of-truth that has already drifted from `settings.APP_NAME`. The vendor statement must consume the forthcoming owner profile / `DocumentOwnerHeader`, not `COMPANY_INFO`. **Fix:** wire to the owner module when it lands; do not add new `COMPANY_INFO` consumers.
- **VEND-FE-3 · Medium · "Export Excel" button on the list view is a dead stub** — `<Button variant="outline-secondary">…Export Excel</Button>` has no `onClick`, and there is no vendor Excel endpoint in `vendorApi.ts` (unlike invoices/quotes/expenses). Same dead-control problem as S-1. **Fix:** wire to a vendor export endpoint or remove the button until implemented.
- **VEND-FE-4 · Medium · "Print Statement" relies on `window.print()` of the live DOM.** No print stylesheet/dedicated layout is referenced; the surrounding app chrome (tabs, dropdowns) will print unless CSS hides it, and there's no server-rendered PDF (unlike invoices via `pdf.py`). **Fix:** provide a print stylesheet or a server PDF for a reliable, branded artifact.
- **VEND-FE-5 · Medium · Detail page fires three sequential, partly-swallowed requests.** `fetchVendorData` awaits `getVendor` then `getVendorPayables` in series (payables errors are only `console.error`’d), and `fetchTransactions` runs separately — the `VendorResponse` already includes `payables`/`overdue_total`, so the separate `/payables` call is redundant for the cards (the JSX even falls back to `vendor.total_unpaid`). **Fix:** drop the redundant call (or the redundant fields) and parallelise/await what remains; surface payables errors instead of swallowing them.
- **VEND-FE-6 · Low · Statement description parsing is brittle.** The table splits `transaction.description` on the `—` em-dash to recover the payment detail. This couples the UI to a server-side string format; any wording change silently breaks the two-line cell. **Fix:** return structured fields (e.g. `detail`, `ref`) on `VendorStatementTransaction` instead of packing them into one string.

### What the `vendors` module does well

- **No destructive cascade and a real delete guard:** `Expense.vendor_id` is FK `RESTRICT`, `Vendor.expenses` is `lazy="noload"`, and `delete()` blocks on `_has_open_transactions` — a markedly safer posture than the `customers`/`quotes` cascade (CUST-DBA-1).
- **Statement generation delegates to the shared `StatementGenerator`** (debit/credit entries) on `develop` rather than the earlier hand-rolled ledger loop — a genuine DRY win and consistent with customers.
- **Strong schema hygiene:** reuses `empty_str_to_none`, normalises email to lowercase at both schema and ORM layers, normalises website scheme, enforces non-blank `vendor_name` in three layers (schema before/after + ORM `@validates` + DB `CheckConstraint`), and keeps list payloads lean via a dedicated `VendorSummary`.
- **Thoughtful indexing on the model:** composite `ix_vendors_status_name`, the partial unique `uq_vendors_email_not_null` (correctly allowing multiple NULL emails), and `created_at`/`contact_id` indexes.
- **Consistent, typed exception handling and structured logging** (`extra={...}`) on every service method; the duplicate-email race is also backstopped by catching `IntegrityError` on the partial unique index.
- **List payables are batched** (`_compute_payables_bulk`) — no N+1 on the list view.

### Vendors module — priority order

1. VEND-SEC-1 (auth + audit attribution; gate hard-delete) — platform-wide via V-REL-1, but hard-delete makes it acute here.
2. VEND-BE-1 (status-bypass via `PUT`) + VEND-OPS-1 (one endpoint/service test would catch it and VEND-BE-2).
3. VEND-BE-2 (`get_detail` AttributeError — remove or implement), VEND-BE-3 (atomic optimistic lock via `version_id_col`).
4. VEND-FE-1 (`vendor.phone` undefined on statement), VEND-FE-2 (`COMPANY_INFO` → owner profile), VEND-SEC-2 (enumeration surface).
5. VEND-DBA-2 (trigram index + `LIKE` escaping), VEND-DBA-1 (currency CHECK), VEND-DBA-3 (counts/soft-delete), VEND-BE-5 (DRY the three aggregates), VEND-FE-3/FE-4/FE-5.
6. VEND-BE-4/BE-6/BE-7, VEND-DBA-4, VEND-OPS-2/OPS-3, VEND-SEC-3, VEND-FE-6.

---

## Module 6 — `invoices`

Files reviewed end-to-end on `develop`: `modules/invoices/{models,schemas,service,router}.py`, `frontend/src/services/invoiceApi.ts`, `frontend/src/pages/sales/invoices/{index,add,edit,detail}` (detail traced in full); cross-checked `common/{financial,reference,pdf,excel}.py`, `common/dependencies.py`, `modules/customers/schemas.py` (the `CustomerSummary` the response embeds). Flow: create (line items + discount + bounded reference-retry) → list/counts/statistics → update (status-gated, recalc) → mark-sent / send (email) / record-payment / duplicate / cancel → PDF + Excel export. This module is the financial core; several master-review findings are confirmed against the live code here. No tests exist for this module.

### 🖥️ @senior-backend-engineer

- **INV-BE-1 · Critical · `duplicate_invoice` returns the wrong type → guaranteed runtime failure (confirms & sharpens V-DI-3).** The method is annotated `-> InvoiceDuplicateResponse` but `return duplicate` is the ORM `Invoice`; the router then calls `InvoiceDuplicateResponse.model_validate(duplicate)`. `InvoiceDuplicateResponse` **requires** `original_invoice_id`, `new_invoice_id`, `new_invoice_number` — none of which exist on the `Invoice` object, so validation raises on **every** duplicate call. The frontend compounds it: `handleDuplicate` reads `dup.id` and navigates to `/invoices/${dup.id}/edit`, but the schema exposes `new_invoice_id`, not `id`. **Fix:** construct and return `InvoiceDuplicateResponse(original_invoice_id=..., new_invoice_id=duplicate.id, new_invoice_number=duplicate.invoice_number)`; align the FE to `new_invoice_id`. Add a test.
- **INV-BE-2 · Critical · `cancel_invoice` bypasses the state machine (confirms V-REL-2).** It sets `invoice.status = InvoiceStatus.CANCELED` directly instead of `self._transition(...)`, so `ALLOWED_TRANSITIONS` is never consulted. Today every state lists `CANCELED` as allowed, so the *effect* is currently equivalent — but the guard is dead, and the moment the matrix changes (e.g. "cannot cancel a PAID invoice") this silently violates it. It also duplicates the version-bump logic `_transition` owns. **Fix:** route through `_transition(invoice, CANCELED)`.
- **INV-BE-3 · High · `record_payment` mutates status directly **and** has no row lock (confirms V-REL-3, plus a state-machine bypass).** It loads via `get_by_id` (no `with_for_update()`, unlike `send_invoice` which does it correctly), then sets `invoice.status = PAID/PARTIAL` directly rather than via `_transition`. Two concurrent payments can both pass the `amount > balance_due` check and overpay (the DB `balance_due >= 0` CHECK would then reject one mid-flush, surfacing as a 500 rather than a clean error). **Fix:** `SELECT … FOR UPDATE` the invoice, re-read balance, and transition via the state machine.
- **INV-BE-4 · High · No path ever sets `OVERDUE`; overdue is computed three different ways.** `ALLOWED_TRANSITIONS` permits `→ OVERDUE`, but nothing calls it — there is no scheduled job or on-read transition. Meanwhile "overdue" is recomputed inline in `Invoice.is_overdue` (model), `InvoiceResponse.is_overdue`/`InvoiceSummary.is_overdue` (schema), and the `get_status_counts`/`get_invoice_statistics` SQL `case` expressions — four separate implementations of the same rule, none using `common/financial.check_is_overdue` (reaffirms V-DRY-4). **Fix:** centralize the overdue predicate in `common/financial`, decide whether OVERDUE is a persisted status (needs a job) or purely derived, and use one definition everywhere.
- **INV-BE-5 · High · Near-duplicate create/duplicate blocks and shared state machine with `QuoteService` (confirms V-SOLID-1/V-DRY-2).** `create` and `duplicate_invoice` repeat the same `begin_nested()` + retry + `IntegrityError`-collision loop almost verbatim; `_transition`, `_ref_gen`, the email-body/subject helpers, and `ALLOWED_TRANSITIONS` are parallel to `QuoteService`. **Fix:** extract a `BaseDocumentService` / mixins (`StateMachineMixin`, `ReferenceRetryMixin`) and a shared create-with-retry helper.
- **INV-BE-6 · Medium · `_update_customer_balance` is a separate query + flush, drift-prone (confirms V-DI-6).** After `record_payment` it re-sums outstanding balances and writes `Customer.balance` in a second statement. Any other balance-affecting path (e.g. cancel of a SENT invoice, line-item edit changing `total_due`) does **not** call it, so `Customer.balance` silently drifts. Notably `cancel_invoice` and `update` do not refresh customer balance. **Fix:** recompute within the same unit of work for every balance-affecting transition, or derive `balance` on read.
- **INV-BE-7 · Medium · `update()` recalculates `balance_due = total_due - amount_paid` but never re-evaluates status.** Editing line items on a SENT invoice can lower `total_due` below `amount_paid` — the code computes a negative `balance_due` and writes it, which the DB `balance_due >= 0` CHECK rejects as a 500; and even when positive, an invoice that becomes fully covered isn't moved to PAID. **Fix:** clamp/validate against `amount_paid` and re-run the status evaluation after recalculation.
- **INV-BE-8 · Low · `send_invoice` claims to attach a PDF but never does.** It takes `attach_pdf` and logs `attached_pdf`, but `email_service.send_document_email(recipient, subject, body_text)` sends text only — no PDF is generated or attached. The API contract (and the FE "attachPdf") is misleading. **Fix:** generate via `generate_pdf` and attach, or drop the parameter until supported.

### 🔒 @ai-security-analyst

- **INV-SEC-1 · Critical · Every invoice endpoint is unauthenticated; `created_by`/`recorded_by` always NULL (reaffirms V-REL-1 / V-DI-1).** All mutating routes carry `user_id = None  # TODO`. Anyone can create/edit/send/cancel invoices, **record payments**, and read financial statistics for the whole business. Payment attribution (`recorded_by`) and invoice authorship are unrecoverable. For a financial ledger this is the highest-impact gap. **Fix:** apply `CurrentUser`, thread the id into `create`/`record_payment`/`duplicate`, and gate cancel/payment behind RBAC. Route to GitLab's Security Analyst Agent.
- **INV-SEC-2 · High · `POST /invoices/calculate` and `/export/excel` are unauthenticated, uncapped compute.** `calculate` accepts an arbitrary, unbounded `list[InvoiceLineItemCreate]` and runs full financial math with no auth and no length cap; `/export/excel` builds an entire workbook in memory (with the N+1 below). Both are trivial unauthenticated DoS amplifiers. **Fix:** require auth; cap line-item count; stream/limit exports.
- **INV-SEC-3 · Medium · `send_invoice` allows an arbitrary `to_email` override with no authorization or domain check.** An unauthenticated caller can send a real, branded invoice email to any address (`to_email`), enabling spoofed-invoice phishing from the company's SES domain. **Fix:** require auth; restrict overrides (or remove), and log/limit outbound sends.
- **INV-SEC-4 · Low · Email body/subject are unsanitised template strings.** `_generate_email_body` interpolates `customer.display_name` and invoice fields directly; a malicious customer name flows into outbound email. Low risk for text email, but relevant once HTML email/PDF is added. **Fix:** treat customer-controlled fields as untrusted when richer rendering lands.

### 🗄️ @senior-database-admin

- **INV-DBA-1 · High · List/search query can't use indexes and isn't `LIKE`-safe (and joins customers unconditionally).** `list_invoices` always `JOIN`s `customers` and, on search, `ilike('%term%')` across five columns (invoice number/reference + customer name parts) with unescaped `%`/`_` and a leading wildcard — a full scan per keystroke on large tables; `ix_invoices_customer_status`/`status_due_date` don't help. **Fix:** `pg_trgm` GIN indexes for the searched text columns, escape `LIKE` metacharacters, and only join/search customer name when a search term is present.
- **INV-DBA-2 · Medium · Reference generation uses COUNT-based strategy, collision-prone after deletes (confirms V-DI-5/W-8).** `_generate_invoice_number`/`_generate_invoice_reference` call `ReferenceGenerator.generate(...)` without `use_max_strategy=True`, while `ExpenseService` uses the safe MAX strategy. The bounded-retry loop masks collisions at runtime but is a workaround for an avoidable race. **Fix:** pass `use_max_strategy=True` for invoice number and reference.
- **INV-DBA-3 · Medium · `avg_days_to_payment` casts `transaction_date` to the `paid_at` (timestamptz) type — Postgres-only and untested.** The `func.extract("epoch", paid_at - cast(transaction_date, paid_at.type))` arithmetic only behaves on PostgreSQL and is silently wrong/erroring on the SQLite test DB (MW-DBA-1). It also assumes a single payment date per invoice (uses `paid_at`, ignoring partial-payment timelines). **Fix:** test on Postgres; document the single-`paid_at` assumption.
- **INV-DBA-4 · Low · `currency` is free-text `String(3)` with no DB CHECK (same as VEND-DBA-1), and per-invoice currency with no FX policy.** Cross-currency customer balances (`_update_customer_balance` sums `balance_due` across invoices regardless of currency) silently add unlike currencies. **Fix:** add a currency domain/CHECK; define whether a customer can hold mixed-currency invoices before summing balances.

### 🛠️ @ai-devops-engineer

- **INV-OPS-1 · High · N+1 in `export_invoices_to_excel` (confirms V-SCALE-1).** When `include_line_items=True`, the router does `[service.get_by_id(item.id) for item in result.items]` — one fully-joined fetch per row, in-request, synchronously, capped only by `settings.BATCH_SIZE` (1000). **Fix:** batch-fetch with `selectinload(Invoice.line_items)`; move large exports to a background job.
- **INV-OPS-2 · Medium · No tests for the financial core.** Discount math, state transitions, payment/overpay guards, the duplicate response (INV-BE-1), and statistics SQL are entirely uncovered — INV-BE-1 and INV-BE-3 would be caught immediately. Must run on PostgreSQL given the `case`/`extract`/`cast` SQL (MW-DBA-1). **Fix:** add service + endpoint tests against Postgres, prioritising money paths.
- **INV-OPS-3 · Medium · Advertised features return errors/stubs at runtime.** `download_invoice_pdf` is wired but the FE "Download PDF" action is hardcoded to show "PDF generation is not yet implemented" (the backend `generate_pdf` exists — FE/BE disagree on availability), and `send_invoice`'s PDF attachment is a no-op (INV-BE-8). Operationally this is confusing: a working endpoint behind a disabled button. **Fix:** reconcile FE/BE feature flags; wire the PDF action to the live endpoint.
- **INV-OPS-4 · Low · `email_service` built at import (reaffirms AUTH-OPS-4/SH-OPS-3).** `send_invoice` imports the module-level `email_service` (SES client constructed on import), coupling invoice sending to AWS config at process start. **Fix:** lazy/DI construction.

### 🎨 @senior-frontend-engineer

- **INV-FE-1 · High · Duplicate navigation uses a field the API doesn't return (pairs with INV-BE-1).** `handleDuplicate` does `navigate(`/invoices/${dup.id}/edit`)`, typing the response as `InvoiceResponse`, but the endpoint's `response_model` is `InvoiceDuplicateResponse` (`new_invoice_id`, no `id`). Even after the backend is fixed, `dup.id` is `undefined` → navigation to `/invoices/undefined/edit`. **Fix:** type the call as `InvoiceDuplicateResponse` and navigate to `new_invoice_id`.
- **INV-FE-2 · Medium · PDF action is a dead stub despite a working endpoint.** The detail page's "Download PDF" sets an error string instead of calling `GET /invoices/{id}/pdf`. Same dead-control class as S-1/VEND-FE-3. **Fix:** call the live PDF endpoint (stream/download), or hide the action behind a real feature flag.
- **INV-FE-3 · Medium · No optimistic-locking version is sent from the edit flow.** `updateInvoice` supports `expected_version`, but the detail/edit pages never read or pass `invoice.version`, so concurrent edits silently last-write-win despite the backend guard existing. **Fix:** thread `version` from the loaded invoice into the update call.
- **INV-FE-4 · Low · `InvoiceCustomer.address` is optional but the embedded `CustomerSummary` never includes it.** The response's `customer` is a `CustomerSummary` (`display_name`/`email`/`phone`, no `address`), yet `InvoiceCustomer` types `address?`. Harmless today (optional), but the viewer may expect an address that never arrives. **Fix:** align the TS type to `CustomerSummary` or have the API embed the fuller customer record where the viewer needs it.

### What the `invoices` module does well

- **Concurrency-correct `send_invoice`:** uses `SELECT … FOR UPDATE` to avoid the TOCTOU race — the pattern the master review (V-REL-3) wants applied to `record_payment` too.
- **Bounded reference-collision retry** with `begin_nested()` savepoints and targeted `IntegrityError` inspection — robust create/duplicate even under contention (the COUNT strategy aside, INV-DBA-2).
- **Statistics/counts pushed entirely into SQL aggregates** (`case`/`coalesce`/`avg`) rather than table scans — good scalability instinct (just Postgres-test it, INV-DBA-3).
- **Strong DB-level integrity:** non-negativity CHECKs on every money column, the discount-type XOR consistency CHECK, `due_date >= transaction_date`, FK `RESTRICT` to customers (no destructive cascade), and useful composite indexes.
- **Shared financial helpers** (`build_line_items`, `sum_line_totals`, `calculate_discount`) and the shared `ReferenceGenerator` are used consistently — a real DRY win at the calculation layer (subject to the SH-BE-1 rounding caveat).
- **Editing is status-gated** (`is_editable`, restricted-field checks on SENT) — sensible lifecycle protection.

### Invoices module — priority order

1. INV-SEC-1 (auth + payment/author attribution) — highest impact for a ledger.
2. INV-BE-1 + INV-FE-1 (duplicate is broken end-to-end today) and INV-OPS-2 (one test catches it).
3. INV-BE-3 (payment row-lock + state machine), INV-BE-2 (cancel via `_transition`), INV-BE-7 (update recalc vs `amount_paid`/status).
4. INV-BE-4 (single overdue definition / decide persisted vs derived), INV-BE-6 (customer-balance drift), INV-SEC-2/SEC-3 (calculate/export/send abuse).
5. INV-DBA-1 (trigram + `LIKE` escaping + conditional join), INV-DBA-2 (MAX reference strategy), INV-OPS-1 (export N+1), INV-FE-2/FE-3.
6. INV-BE-5 (shared base service with quotes), INV-BE-8 (PDF attach), INV-DBA-3/DBA-4, INV-OPS-3/OPS-4, INV-SEC-4, INV-FE-4.

---

## Module 7 — `expenses`

Files reviewed end-to-end on `develop`: `modules/expenses/{models,schemas,service,router}.py`, `lib/storage.py`, `frontend/src/services/expenseApi.ts`, `frontend/src/pages/purchases/expenses/{index,add,edit,detail}.tsx` (detail traced in full); cross-checked `common/{financial,reference}.py`, `common/dependencies.py`, `modules/vendors/models.py` (the FK target). Flow: create (vendor-validated, line items, bounded reference-retry) → list/counts/statistics → update (status-gated) → mark-paid / record-payment / duplicate → document upload/download/delete → soft/hard delete → nightly overdue bulk-transition. **This is the most mature module in the codebase** — it uses `SELECT FOR UPDATE` on both payment paths, the MAX reference strategy, the shared overdue helpers, and a real file-upload pipeline. It also introduces the platform's **first file-storage attack surface**, where the most serious findings live. No tests exist for this module.

### 🔒 @ai-security-analyst

- **EXP-SEC-1 · Critical · Unauthenticated document download serves a raw filesystem path (path-traversal / broken access control).** `get_expense_document_download` returns `FileResponse(path=document.storage_key, ...)` where `storage_key` is the stored local path, and the route has **no auth**. Two compounding problems: (1) every attached document (vendor invoices, receipts — sensitive financial PII) is downloadable by anyone who can enumerate `expense_id`/`document_id` UUIDs; (2) `StorageService` never confines paths to `base_dir` (no `Path.resolve()` + prefix check — confirms SH-SEC-1), so any code path that ever writes a non-sanitised `storage_key` becomes an arbitrary-file-read. Uploads are sanitised today, but the defense lives at the call site, not the service. **Fix:** require auth + ownership check on download; in `StorageService`, resolve and assert the key is inside `base_dir` before any open/serve/delete; serve via a streamed handle, never a client-influencable path. Route to GitLab's Security Analyst Agent.
- **EXP-SEC-2 · High · Unauthenticated file upload that trusts the client-supplied MIME type.** `attach_expense_document` has no `CurrentUser` and validates `file.content_type` (attacker-controlled) rather than sniffing content. An unauthenticated caller can write up to 10 MB to the server filesystem per call and mislabel content (e.g. an HTML/SVG payload sent as `image/png`). Extension allow-list + size cap + `secrets.token_hex` prefix + `PurePosixPath(...).name` traversal strip are good mitigations, but auth and content-sniffing are missing. **Fix:** require auth; verify magic bytes (e.g. `python-magic`) against the claimed type; keep the existing guards.
- **EXP-SEC-3 · High · The "internal" scheduler endpoint is publicly reachable.** `POST /expenses/internal/transition-overdue` is only `include_in_schema=False` (hidden from docs) — it has no network restriction, no shared-secret, and no auth. Anyone who guesses the path can trigger a bulk write (`version += 1` on every past-due PENDING row), an audit-noise / contention vector. **Fix:** protect with an internal-only auth (mTLS, header secret, or network policy), not schema-hiding.
- **EXP-SEC-4 · Medium · Every other expense endpoint is unauthenticated; attribution is NULL (reaffirms V-REL-1 / V-DI-1).** All mutating routes set `user_id = None  # TODO`; `created_by`, `recorded_by`, `uploaded_by` are never populated. For a payables ledger with money movement and document handling this is a material audit gap. **Fix:** apply `CurrentUser`, thread the id, and enforce the "Manager/Owner role required" the `delete` docstring already promises (EXP-BE-5).

### 🖥️ @senior-backend-engineer

- **EXP-BE-1 · High · `mark_as_paid` contradicts its own API contract and isn't idempotent.** The router description says "set status to PAID **without creating a payment record**," but the service **does** create an `ExpensePayment` (`AUTO-SETTLE-...`) for the remaining balance. Beyond the doc/behaviour mismatch, calling it on an already-PAID expense is blocked only by `_transition` (PAID is terminal → `BadRequestException`), which is fine — but on a partially-paid expense it settles the residual correctly yet the FE has no separate "settle remainder" affordance, so the semantics are easy to misuse. **Fix:** reconcile the docstring with the (better) audit-trail behaviour; document that it settles `balance_due`.
- **EXP-BE-2 · Medium · `record_payment` sets `status = PAID` directly, bypassing `_transition` (state-machine inconsistency).** The inline comment acknowledges it: "set status directly to avoid double version bump." It is currently correct (PENDING/OVERDUE → PAID is allowed), and the row is `SELECT … FOR UPDATE`-locked (good, unlike `InvoiceService.record_payment`), but it duplicates transition logic the state machine owns and will silently diverge if the matrix changes. **Fix:** add a transition variant that bumps the version once, or centralise the "apply payment → maybe settle" logic so both `record_payment` and `mark_as_paid` share one path.
- **EXP-BE-3 · Medium · Soft-delete writes a status the state machine and lifecycle don't recognise.** `delete()` sets `status = CANCELED` for expenses with payments, but `CANCELED` is absent from `ALLOWED_TRANSITIONS`, `ExpenseStatus` lifecycle docs say "No CANCELED terminal state — removal is hard-delete," yet the model CHECK *does* permit `canceled`. So a CANCELED expense still appears in single-vendor payables unless every read filters it (list does; `_compute_payables_*` in vendors does **not** — it filters only on `pending`/`overdue`, so CANCELED is correctly excluded there by omission, but `get_vendor_transactions` will still surface it). **Fix:** make CANCELED a first-class lifecycle state (add to transitions, exclude consistently) or use a dedicated soft-delete flag.
- **EXP-BE-4 · Medium · Hard-delete orphans stored document files.** `delete()` hard-deletes the `Expense`; `ExpenseDocument` rows cascade away via FK, but the **files in object storage are never removed** (only the per-document `delete_document` endpoint cleans storage). Every hard-deleted expense with attachments leaks files on disk. **Fix:** enumerate and delete storage objects for all documents before/after the row delete (and reconcile in a sweep job).
- **EXP-BE-5 · Medium · `delete` promises RBAC it doesn't enforce.** The endpoint doc says "Manager/Owner role required when the expense has recorded payments," and the service returns `had_payments` "for future role-gate enforcement," but nothing checks a role (auth is unwired). Today any caller soft- or hard-deletes any expense. **Fix:** enforce the role gate once auth lands (depends on EXP-SEC-4 / AUTH-BE-4).
- **EXP-BE-6 · Low · Near-duplicate create/duplicate + state machine shared with invoices/quotes (reaffirms V-SOLID-1).** `create`/`duplicate`, the `begin_nested()`-retry loop, `_transition`, `_ref_gen`, and `_build_line_items`/`_sum_line_totals` (which already just delegate to `common/financial`) parallel `InvoiceService`/`QuoteService`. The static `_build_line_items`/`_sum_line_totals` wrappers add an indirection layer over the shared helpers for no benefit. **Fix:** fold into a `BaseDocumentService`/mixins; call the shared helpers directly.

### 🗄️ @senior-database-admin

- **EXP-DBA-1 · Medium · List/search `ILIKE '%term%'` is unindexed and not `LIKE`-safe (same class as INV-DBA-1/VEND-DBA-2).** Leading wildcard across `expense_number`/`expense_reference`/`vendor_name` (with an unconditional vendor join) can't use the btree indexes and doesn't escape `%`/`_`. **Fix:** `pg_trgm` GIN indexes + `LIKE`-metacharacter escaping; the model already has good btree coverage (`ix_expenses_vendor_status`, `ix_expenses_status_due_date`).
- **EXP-DBA-2 · Medium · `avg_days_to_payment` casts `expense_date` to the `paid_at` timestamptz type — Postgres-only, untested, single-payment assumption.** Same construction and caveats as INV-DBA-3: silently wrong/erroring on the SQLite test DB (MW-DBA-1), and uses the single `paid_at` so it ignores partial-payment timelines. **Fix:** test on Postgres; document the assumption.
- **EXP-DBA-3 · Medium · `currency` is free-text `String(3)` with no DB CHECK (same as INV-DBA-4/VEND-DBA-1).** `status` is constrained by `ck_expenses_valid_status` but currency is not. **Fix:** add a currency domain/CHECK.
- **EXP-DBA-4 · Low · `bulk_transition_overdue` is correct but interacts with optimistic locking.** It does `Expense.version = Expense.version + 1` with `synchronize_session=False` then `expire_all()` — efficient and correct. The only caveat: a concurrent user editing an expense the job flips will hit a (correct) version conflict; ensure the FE surfaces that cleanly. No code change required.

### 🛠️ @ai-devops-engineer

- **EXP-OPS-1 · High · N+1 in `export_expenses_to_excel` (same as INV-OPS-1 / V-SCALE-1).** `[service.get_by_id(item.id) for item in result.items]` issues one fully-eager-loaded fetch per row, synchronously, capped only by `settings.BATCH_SIZE`. **Fix:** batch-load with `selectinload`; move large exports to a background job.
- **EXP-OPS-2 · High · Local-filesystem storage won't survive horizontal scaling (reaffirms V-SCALE-2).** `StorageService` writes under `./uploads`; documents uploaded to one instance are invisible to others and lost on container recycle. The class is shaped for S3 but only the local backend exists. **Fix:** implement the S3 backend and store object keys (not local paths) in `storage_key` — which also fixes EXP-SEC-1's path-serving.
- **EXP-OPS-3 · Medium · No tests for the module's risk-bearing paths.** Payment/overpay guards, `mark_as_paid` settlement, soft-vs-hard delete, the upload guards, and the overdue job are all uncovered. Must run on PostgreSQL (the `case`/`extract`/`cast` SQL and `with_for_update()` semantics don't hold on SQLite — MW-DBA-1). **Fix:** add service + endpoint tests (incl. an upload-rejection test for bad MIME/size) against Postgres.
- **EXP-OPS-4 · Medium · `/calculate` is unauthenticated, uncapped compute (same as INV-SEC-2).** Accepts an unbounded `list[ExpenseLineItemCreate]` with no auth/length cap. **Fix:** require auth; cap line-item count.
- **EXP-OPS-5 · Low · Storage write/serve has no async offload or content scanning.** Synchronous `shutil.copyfileobj` in-request, no AV/malware scan on uploaded documents. **Fix:** offload large writes; add scanning before the file is downloadable.

### 🎨 @senior-frontend-engineer

- **EXP-FE-1 · High · Document download fetches a Blob but never saves it — the action does nothing.** `downloadExpenseDocument` returns `response.blob()`, and `handleFileDownload` just `await`s it with no `URL.createObjectURL` + anchor click (or save). The user confirms "Download" and nothing happens. **Fix:** create an object URL and trigger a download anchor (and revoke the URL after).
- **EXP-FE-2 · Medium · Upload/download bypass the shared `api` client and send no auth.** `uploadExpenseDocument`/`downloadExpenseDocument` use raw `fetch(new URL(..., appConfig.apiUrl))` instead of `apiPost`/`apiGet`, so once auth lands (V-REL-1/AUTH-FE-1) they will **not** carry the `Authorization` header the rest of the app will add centrally. **Fix:** route multipart upload and binary download through the shared client (or a shared helper that injects auth).
- **EXP-FE-3 · Medium · "Download PDF" is a dead stub with no backing endpoint.** The detail page's PDF action sets "not yet implemented," and — unlike invoices — there is **no** `/expenses/{id}/pdf` route at all. Same dead-control class as S-1/VEND-FE-3/INV-FE-2. **Fix:** implement the endpoint or remove the action.
- **EXP-FE-4 · Low · Edit/update flow sends no `expectedVersion` (same as INV-FE-3).** `updateExpense` doesn't pass the optimistic-lock version, and the FE payload type omits it, so the backend `expectedVersion` guard is never exercised — concurrent edits silently last-write-win. **Fix:** thread `expense.version` into the update call.
- **EXP-FE-5 · Low · `accept` attribute and server allow-list disagree.** The file input `accept="image/*,.pdf,.doc,.docx"` is narrower than the server's allow-list (xls/xlsx/csv/txt/webp/gif), so users can't pick valid types the backend accepts. **Fix:** align the `accept` list to `ALLOWED_EXTENSIONS` (ideally from one shared source — V-DRY-3).

### What the `expenses` module does well

- **Both money paths are concurrency-correct:** `record_payment` and `mark_as_paid` use `SELECT … FOR UPDATE` — the row-lock the master review wants back-ported to `InvoiceService.record_payment` (V-REL-3). `mark_as_paid` also writes a real audit-trail payment.
- **Collision-safe references:** `_generate_expense_reference` uses `use_max_strategy=True` (the safe pattern the rest of the platform should adopt — V-DI-5/W-8) and both generators sit behind a bounded retry loop.
- **Overdue handled properly, in one place:** schemas delegate to `common/financial.check_is_overdue`/`calculate_days_overdue` (no inline recomputation, unlike invoices, INV-BE-4), and a real nightly `bulk_transition_overdue` job persists the status efficiently (`synchronize_session=False` + `expire_all`).
- **Genuinely thoughtful upload guards:** extension allow-list, size cap, empty-file rejection, `secrets.token_hex` filename prefix, `PurePosixPath(...).name` traversal strip, and a storage-then-DB ordering that avoids orphaned metadata. (The gaps are auth, content-sniffing, and centralising the guards — EXP-SEC-1/2, V-DRY-3.)
- **Sound delete posture by intent:** soft-delete when payments exist, hard-delete otherwise; `documents`/`payments`/`line_items` cascade cleanly; FK from expenses→vendors is `RESTRICT` (no destructive vendor cascade).
- **Statistics/counts pushed into SQL aggregates**, with the overdue bucket computed in-query (stored OVERDUE + PENDING-past-due) — good scalability instinct (Postgres-test it, EXP-DBA-2).
- **Strong DB integrity:** non-negativity CHECKs on every money column, `due_date >= expense_date`, unique reference constraints, and well-chosen composite indexes.

### Expenses module — priority order

1. EXP-SEC-1 (unauthenticated raw-path document download + storage path-confinement) — highest impact; pairs with EXP-OPS-2 (S3 keys).
2. EXP-SEC-2 (upload auth + content-sniffing), EXP-SEC-3 (protect the internal scheduler route).
3. EXP-SEC-4 (auth + attribution) and EXP-BE-5 (enforce the delete RBAC it already advertises).
4. EXP-FE-1 (download does nothing) + EXP-FE-2 (upload/download via shared auth client), EXP-OPS-1 (export N+1).
5. EXP-BE-3 (CANCELED lifecycle consistency), EXP-BE-4 (orphaned files on hard-delete), EXP-BE-1/BE-2 (mark-paid contract + payment state machine), EXP-DBA-1 (trigram/LIKE).
6. EXP-DBA-2/DBA-3, EXP-OPS-3/OPS-4/OPS-5, EXP-BE-6, EXP-FE-3/FE-4/FE-5, EXP-DBA-4.

---

## Module 8 — `quotes`

Files reviewed end-to-end on `develop`: `modules/quotes/{models,schemas,service,router}.py`, `frontend/src/services/quoteApi.ts`, `frontend/src/pages/sales/quotes/{index,add,edit,detail}` (detail traced in full); cross-checked `common/{financial,reference}.py`, `modules/invoices/service.py` (the conversion target + shared generators), `modules/customers/models.py` (the `Customer.quotes` relationship). Flow: create (customer-validated, bounded retry) → list/counts/statistics → update (status-gated) → mark-sent / send (email) / approve / convert-to-invoice / duplicate / delete (DRAFT-only) → PDF. **This is the cleanest of the document modules** — it routes *every* status change through `_transition()`, wraps the conversion in a single SAVEPOINT, uses `SELECT FOR UPDATE` on send, and delegates invoice-number generation to `InvoiceService` (so the master review's DI-1/DI-3/M-2/R-1/R-2/R-3 fixes are present here). The remaining findings are mostly cross-cutting patterns plus a few quote-specific lifecycle gaps. No tests exist for this module.

### 🗄️ @senior-database-admin

- **QT-DBA-1 · High · ORM `Customer.quotes` destructive cascade conflicts with the DB-level FK `RESTRICT` (confirms V-DI-2, with a twist).** `Quote.customer_id` is FK `ondelete="RESTRICT"`, but the master review records `Customer.quotes` as `cascade="all, delete-orphan"`. These two contradict: the ORM cascade will try to delete a customer's quotes (and their line items) *before* the parent delete, while the DB FK is meant to block it. Depending on the delete path (ORM `session.delete(customer)` vs SQL), this either silently destroys quote history (ORM cascade wins) or errors confusingly (RESTRICT wins). Either way it is unsafe and asymmetric with `Customer.invoices` (`save-update, merge`). **Fix:** change `Customer.quotes` to `save-update, merge` to match invoices; rely on `RESTRICT` + soft-delete. (Quote-side `line_items` cascade is correct.)
- **QT-DBA-2 · Medium · List/search `ILIKE '%term%'` is unindexed and not `LIKE`-safe (same class as INV-DBA-1/EXP-DBA-1/VEND-DBA-2).** Leading wildcard across quote number/reference + three customer name columns, unconditional customer join, unescaped `%`/`_`. **Fix:** `pg_trgm` GIN indexes + `LIKE`-metacharacter escaping; existing btree coverage is good (`ix_quotes_customer_status`, `ix_quotes_status_due_date`).
- **QT-DBA-3 · Medium · Reference generation uses the COUNT strategy, not MAX (confirms V-DI-5/W-8).** `_generate_quote_number`/`_generate_quote_reference` omit `use_max_strategy=True`, unlike `ExpenseService`. The advisory-lock + retry loop masks collisions but the safe pattern already exists. **Fix:** pass `use_max_strategy=True` for both. (The advisory-lock serialization is a genuine strength — see strengths below.)
- **QT-DBA-4 · Medium · `currency` is free-text `String(3)` with no DB CHECK (same as INV-DBA-4/EXP-DBA-3/VEND-DBA-1).** `status` is constrained by `ck_quotes_valid_status`; currency is not. **Fix:** add a currency domain/CHECK.
- **QT-DBA-5 · Low · `avg_days_to_approval` casts `transaction_date` to the `approved_at` timestamptz type — Postgres-only, untested (same as INV-DBA-3/EXP-DBA-2).** Silently wrong/erroring on the SQLite test DB (MW-DBA-1). **Fix:** test on Postgres.

### 🖥️ @senior-backend-engineer

- **QT-BE-1 · High · Nothing ever sets `EXPIRED`; the lifecycle has a dead state and a dead transition (same class as INV-BE-4).** The model has `expired_at` and `is_expired`, `ALLOWED_TRANSITIONS` permits `DRAFT/SENT → EXPIRED` and `EXPIRED → SENT`, the docstring says expiry is "auto-set by scheduled job" — but there is **no job** (unlike `ExpenseService.bulk_transition_overdue`). Expiry is only ever *derived* in `is_expired`/counts/statistics, so `expired_at` is always NULL and a quote is never actually in the EXPIRED state. **Fix:** add a scheduled `bulk_transition_expired` mirroring the expenses job (and set `expired_at`), or formally treat EXPIRED as derived-only and remove the unreachable transitions/column.
- **QT-BE-2 · High · Converted quotes can be orphaned if the resulting invoice is canceled.** `convert_to_invoice` moves the quote to `INVOICED` (terminal — no outgoing transitions) and sets `related_invoice_id`. If that invoice is later canceled (`InvoiceService.cancel_invoice`), the quote stays `INVOICED` forever, pointing at a canceled invoice, with `can_convert_to_invoice` permanently false — no re-conversion path and no signal to the user. **Fix:** define the cancel-after-convert policy (e.g. allow `INVOICED → APPROVED` when the related invoice is canceled, or clear `related_invoice_id`), and consider creating the invoice as `DRAFT` vs `SENT` intentionally.
- **QT-BE-3 · Medium · `can_convert_to_invoice` permits `SENT` but the API contract says "approved."** The property allows `APPROVED` **or** `SENT`; the router/summary docs and the `convert` error message all say only approved quotes convert. So a SENT quote can skip APPROVED entirely — either a real feature or an oversight, but the contract is inconsistent. **Fix:** decide whether SENT→INVOICED is allowed and align property, docs, and the FE gating.
- **QT-BE-4 · Medium · `convert_to_invoice`'s SAVEPOINT is not paired with rollback on failure.** It opens `sp = self._db.begin_nested()` and `sp.commit()`s on success, but the `except IntegrityError/SQLAlchemyError` branches re-raise **without** `sp.rollback()` (unlike `create`/`duplicate`, which roll the savepoint back). The outer `get_db()` will roll back the whole transaction, so it's not corrupting data, but the savepoint is left to unwind implicitly — inconsistent with the module's own pattern and fragile if a caller ever wraps this. **Fix:** `sp.rollback()` in the except branches for symmetry.
- **QT-BE-5 · Medium · Near-duplicate of `InvoiceService` (confirms V-SOLID-1/V-DRY-2).** `_transition`, `_ref_gen`, `create`/`duplicate` retry loops, `calculate_totals`, the email subject/body helpers, and `get_*_statistics` are all parallel to invoices. **Fix:** extract the shared `BaseDocumentService`/mixins the master review proposes; quotes and invoices are the canonical pair to deduplicate first.
- **QT-BE-6 · Low · `send_quote`'s `attach_pdf` is a no-op (same as INV-BE-8).** Takes/logs `attach_pdf` but `email_service.send_document_email(...)` sends text only; no PDF generated or attached. **Fix:** attach via `generate_pdf` or drop the parameter.
- **QT-BE-7 · Low · Email helpers depend on the global `settings.APP_NAME` (confirms V-SOLID-2).** Subject/body interpolate `settings.APP_NAME`; should consume the forthcoming owner profile, not the singleton.

### 🔒 @ai-security-analyst

- **QT-SEC-1 · Critical · Every quote endpoint is unauthenticated; `created_by`/`approved_by` always NULL (reaffirms V-REL-1 / V-DI-1).** All routes carry `user_id = None  # TODO`; crucially `approve_quote` records **no approver** and converting to an invoice (a financially binding action) requires no auth. `approved_by` exists but is never set. **Fix:** apply `CurrentUser`, thread the id into create/approve/convert/duplicate, and gate approve/convert behind RBAC. Route to GitLab's Security Analyst Agent.
- **QT-SEC-2 · High · `POST /quotes/{id}/send` allows an arbitrary `to_email` override, unauthenticated (same as INV-SEC-3).** Anyone can send a branded quote email from the company SES domain to any address — spoofing/phishing vector. **Fix:** require auth; restrict or remove the override.
- **QT-SEC-3 · Medium · `/quotes/calculate` and `/export/excel` are unauthenticated, uncapped compute (same as INV-SEC-2).** Unbounded `list[QuoteLineItemCreate]` and in-memory workbook build, no auth/cap. **Fix:** require auth; cap line-item count; stream/limit exports.
- **QT-SEC-4 · Low · Email subject/body interpolate customer-controlled `display_name` unsanitised (same as INV-SEC-4).** Low risk for plain text; relevant once HTML email/PDF lands.

### 🛠️ @ai-devops-engineer

- **QT-OPS-1 · High · N+1 in `export_quotes_to_excel` (same as INV-OPS-1/EXP-OPS-1 / V-SCALE-1).** `[service.get_by_id(item.id) for item in result.items]` when `include_line_items=True`. **Fix:** batch-load with `selectinload`; background job for large exports.
- **QT-OPS-2 · High · No EXPIRED scheduler exists (operational gap behind QT-BE-1).** Expenses ship a nightly `bulk_transition_overdue` + an `/internal/transition-overdue` trigger; quotes have neither, so the "auto-set by scheduled job" contract is unfulfilled and dashboards rely entirely on derived computation. **Fix:** add the scheduled expiry job + an internal (authenticated) trigger endpoint.
- **QT-OPS-3 · Medium · No tests for the module.** State transitions, the conversion atomicity, discount recalculation, and statistics SQL are uncovered; QT-BE-2/BE-4 and the FE duplicate bug (QT-FE-1) would surface immediately. Must run on PostgreSQL (advisory locks, `case`/`extract`/`cast`, `with_for_update()` — MW-DBA-1). **Fix:** add service + endpoint tests against Postgres, prioritising the convert-to-invoice flow.
- **QT-OPS-4 · Low · `email_service` built at import (reaffirms AUTH-OPS-4/SH-OPS-3/INV-OPS-4).** **Fix:** lazy/DI construction.

### 🎨 @senior-frontend-engineer

- **QT-FE-1 · High · Duplicate navigation uses a field the API doesn't return (same class as INV-FE-1, but here the backend is correct).** The router correctly returns `QuoteDuplicateResponse` (`new_quote_id`), yet `duplicateQuote` is typed `QuoteResponse` and `handleDuplicate` does `navigate(`/quotes/${dup.id}/edit`)` — `dup.id` is `undefined`, so it navigates to `/quotes/undefined/edit`. **Fix:** type the call as `QuoteDuplicateResponse` and navigate to `new_quote_id`. (The error toast also wrongly says "Failed to duplicate **invoice**" — copy-paste from invoices.)
- **QT-FE-2 · High · "Send" is a dead `alert("coming soon")` despite a fully-working backend.** `send_quote` (with `SELECT FOR UPDATE`, email dispatch, state transition) is implemented and wired, but the detail page's Send action just `alert`s — and `quoteApi.ts` has no `sendQuote`/`markQuoteAsSent`-equivalent wrapper for send. Working backend, disabled UI (worse than INV-FE-2 since invoices at least call the modal). **Fix:** add a `sendQuote` API wrapper and a send modal; remove the alert.
- **QT-FE-3 · Medium · "Download PDF" is a dead `alert("coming soon")` despite a working `/pdf` endpoint.** Same dead-control class as INV-FE-2/EXP-FE-3. **Fix:** stream the live endpoint to a download.
- **QT-FE-4 · Medium · Edit/update flow sends no `expectedVersion` (same as INV-FE-3/EXP-FE-4).** `updateQuote` supports it; the pages never pass `quote.version`, so the optimistic-lock guard is never exercised. **Fix:** thread `quote.version`.
- **QT-FE-5 · Low · Inconsistent confirm UX.** Delete uses the native `confirm()` while invoices/expenses/vendors use the app's `useConfirm` dialog; errors here are only `console.error`'d (no user-facing banner on mark-sent/approve/delete failures). **Fix:** use `useConfirm` and surface errors consistently.

### What the `quotes` module does well

- **Every status change goes through `_transition()`** — the single, correct enforcement point the master review wants invoices to adopt (contrast V-REL-2/INV-BE-2/INV-BE-3 which bypass it). The state machine is explicit and complete.
- **`convert_to_invoice` is atomic and well-factored:** one SAVEPOINT around invoice + line-item creation + quote transition, and it **delegates invoice-number/reference generation to `InvoiceService`** (the M-2 SRP fix) rather than duplicating it. Line `item_name` is preserved on conversion (DI-1 fix).
- **Concurrency-correct `send_quote`** via `SELECT … FOR UPDATE`, and **advisory-lock-serialized reference generation** (`pg_advisory_xact_lock` in `ReferenceGenerator`) plus bounded retry — a strong race posture (only the COUNT-vs-MAX strategy, QT-DBA-3, remains).
- **Delete is correctly restricted to DRAFT** (no destroying sent/approved/invoiced history), and `related_invoice_id` uses `ondelete="SET NULL"` so deleting an invoice won't cascade into quotes.
- **Statistics pushed into a single SQL aggregate** (conversion rate, avg-days-to-approval, expired bucket) — good scalability instinct (Postgres-test it, QT-DBA-5).
- **Strong DB integrity:** money non-negativity CHECKs, the discount-type XOR CHECK, `due_date >= transaction_date`, unique number/reference constraints, FK `RESTRICT` to customers, and good composite indexes.

### Quotes module — priority order

1. QT-SEC-1 (auth + approver/author attribution; gate approve/convert) — highest impact; convert-to-invoice is financially binding.
2. QT-DBA-1 (fix the `Customer.quotes` cascade vs RESTRICT conflict — data-loss hazard) and QT-FE-1 (duplicate navigation broken).
3. QT-BE-1 + QT-OPS-2 (implement the expiry job, or make EXPIRED derived-only), QT-BE-2 (cancel-after-convert orphaning policy).
4. QT-FE-2 ("Send" dead despite working backend), QT-SEC-2 (send override), QT-BE-3 (SENT-vs-APPROVED convert contract).
5. QT-DBA-2 (trigram + LIKE escaping), QT-DBA-3 (MAX reference strategy), QT-OPS-1 (export N+1), QT-BE-4 (savepoint rollback symmetry), QT-FE-3/FE-4.
6. QT-BE-5 (shared base service with invoices), QT-BE-6/BE-7, QT-DBA-4/DBA-5, QT-SEC-3/SEC-4, QT-OPS-3/OPS-4, QT-FE-5.

---

# Part III — Cross-Module Synthesis

> After reviewing every module in isolation, the same defects recur across modules with near-identical shape. Fixing them once — at the shared layer or as a single sweep — is far cheaper and safer than the per-module patches listed above. This section consolidates the **systemic patterns**, maps each to its per-module instances, and gives a platform-wide remediation order. Pillar tags: 🔒 Reliability/Security, 📈 Scalability, 🔧 Maintainability, 🧾 Data Integrity.

## A. Systemic patterns (one root cause, many call sites)

### P-1 · 🔒 Critical · No authentication or write attribution anywhere
Every module ships `user_id = None  # TODO` on every mutating route and applies no `CurrentUser` dependency, even though the plumbing exists (`security.py`, `dependencies.CurrentUser`). The entire API — including payments, quote approval, invoice cancellation, vendor hard-delete, and document upload/download — is publicly callable, and every `created_by`/`updated_by`/`recorded_by`/`approved_by`/`uploaded_by` column is permanently NULL.
- **Instances:** V-REL-1, V-DI-1, AUTH-FE-1, CUST-SEC-2, VEND-SEC-1, INV-SEC-1, EXP-SEC-4, QT-SEC-1; enumeration/abuse surfaces VEND-SEC-2, INV-SEC-2/SEC-3, EXP-SEC-1/SEC-2/SEC-3, QT-SEC-2/SEC-3.
- **Single fix:** land auth (AUTH module hardening) + a client token interceptor (AUTH-FE-1), apply `CurrentUser` via the service factories (SH-BE-4) so attribution is threaded once, then add RBAC for destructive/financial actions. **This one change unblocks the audit trail across all modules and closes the largest attack surface.**

### P-2 · 📈 High · Unindexed, `LIKE`-unsafe search on every list endpoint
Every list view does `ilike('%term%')` across several columns (often with an unconditional customer/vendor join) with a leading wildcard and no escaping of `%`/`_`. None can use a btree index; each is a full scan per keystroke, and user input alters the pattern.
- **Instances:** VEND-DBA-2, INV-DBA-1, EXP-DBA-1, QT-DBA-2 (and the customers detail-load concern CUST-DBA-3).
- **Single fix:** add `pg_trgm` GIN indexes for the searched text columns and a shared search helper that escapes `LIKE` metacharacters and only joins the related party when a term is present.

### P-3 · 🧾 High · Money is never quantized; totals can drift
The shared `financial.calculate_line_item` returns unrounded `line_total`/`tax_amount`; only the percentage-discount path quantizes. Summing unrounded products produces sub-cent drift that can violate the 2-dp money columns and the `balance_due >= 0` CHECK, and makes totals non-reproducible. Every document module (invoices, quotes, expenses) and every statement/PDF/Excel path inherits this.
- **Instances:** SH-BE-1 (root), SH-FE-1 (client must match), VEND-DBA-4 / INV-* / QT-* / EXP-* statement & total paths.
- **Single fix:** define one rounding policy in `common/financial` (quantize per line, `ROUND_HALF_UP`, 2 dp), reuse it in discounts/statements/PDF/Excel, and mirror it in the frontend `calculateTotals`.

### P-4 · 🧾 Medium · Reference generation split between COUNT and MAX strategies
`ExpenseService` uses the collision-safe `use_max_strategy=True`; invoices and quotes use the COUNT(*)-based default, which collides after hard-deletes and is only saved by the retry loop.
- **Instances:** V-DI-5, W-8, SH-DBA-1, INV-DBA-2, QT-DBA-3 (expenses is the correct reference implementation).
- **Single fix:** make MAX the default in `ReferenceGenerator` (or pass `use_max_strategy=True` for invoice/quote number + reference) so all four generators behave like expenses.

### P-5 · 📈 High · Excel export N+1 + in-memory, in-request workbook build
Every export endpoint does `[service.get_by_id(item.id) for item in result.items]` then builds the whole workbook synchronously in memory, capped only by `settings.BATCH_SIZE` (1000).
- **Instances:** V-SCALE-1, SH-OPS-2, INV-OPS-1, EXP-OPS-1, QT-OPS-1.
- **Single fix:** batch-load with `selectinload(line_items)` in a shared export helper; move large exports to a background job with a download link.

### P-6 · 🔧 Medium · "Overdue/Expired" computed three-to-four ways; lifecycle states only partly persisted
The time-based status (invoice/expense OVERDUE, quote EXPIRED) is recomputed inline in the model, the schema, and the stats/counts SQL. Expenses persist it via a nightly job; invoices and quotes have the transition in the matrix but **no job**, so the state is effectively unreachable and `expired_at`/overdue is NULL.
- **Instances:** V-DRY-4, INV-BE-4, QT-BE-1, QT-OPS-2 (expenses' `bulk_transition_overdue` is the reference pattern).
- **Single fix:** centralize the predicate in `common/financial.check_is_overdue` (already used by expenses), and either add the missing scheduled jobs (invoices OVERDUE, quotes EXPIRED) or formally declare those states derived-only and drop the dead transitions/columns.

### P-7 · 🔧 High · `InvoiceService`/`QuoteService` (and much of `ExpenseService`) are near-duplicates
The state machine (`_transition`, `ALLOWED_TRANSITIONS`), `_ref_gen`, the `begin_nested()` create/duplicate retry loops, `calculate_totals`, email subject/body helpers, and the SQL-aggregate statistics are parallel across the three document services.
- **Instances:** V-SOLID-1, V-DRY-2, INV-BE-5, QT-BE-5, EXP-BE-6.
- **Single fix:** extract a `BaseDocumentService` + focused mixins (`StateMachineMixin`, `ReferenceRetryMixin`, `DocumentEmailMixin`); start with the invoices/quotes pair (most identical) then fold expenses in.

### P-8 · 🧾 High · Status mutated directly, bypassing the state machine
Some write paths set `status = ...` directly instead of calling `_transition`, defeating `ALLOWED_TRANSITIONS` and duplicating the version bump. Quotes is the clean counter-example (always `_transition`).
- **Instances:** V-REL-2 (`cancel_invoice`), INV-BE-2, INV-BE-3 (`record_payment`), EXP-BE-2 (`record_payment`), VEND-BE-1 (`update()` lets `PUT` set status), CUST-BE-4.
- **Single fix:** route all status changes through the shared `StateMachineMixin._transition`; remove `status` from update schemas; the mixin owns the single version-bump.

### P-9 · 🔒 Medium · Optimistic locking is advisory-only and never exercised by the client
The `version` column + `expected_version` query param exist on invoices/quotes/expenses/vendors, but the check is a non-atomic Python compare on a row loaded without `FOR UPDATE` (vendors), and **no frontend ever sends the version**, so concurrent edits silently last-write-win everywhere.
- **Instances:** VEND-BE-3, INV-FE-3, EXP-FE-4, QT-FE-4.
- **Single fix:** adopt SQLAlchemy `version_id_col` (DB-enforced atomic check) and thread the loaded `version` into every update call from the frontend.

### P-10 · 🔧 Medium · Working backend endpoints fronted by dead UI stubs
Several implemented endpoints are hidden behind `alert("coming soon")` / disabled buttons, while some buttons point at unimplemented features — FE and BE disagree on what ships.
- **Instances:** S-1 (owner "Update"), VEND-FE-3 (Export Excel), INV-FE-2 / EXP-FE-3 / QT-FE-3 (Download PDF works on invoices/quotes, missing on expenses), QT-FE-2 (Send works, UI alerts), EXP-FE-1 (download fetches but never saves).
- **Single fix:** reconcile a single feature-flag/contract source; wire the live endpoints (PDF, send, export) and remove or gate the genuinely-unimplemented ones.

### P-11 · 📈/🔧 Medium · `currency` is free-text `String(3)` with no DB domain, and balances sum across currencies
Every money table stores currency as an unconstrained `String(3)` (only `status` gets a CHECK), and `Customer.balance`/vendor payables sum `balance_due` across invoices/expenses regardless of currency.
- **Instances:** VEND-DBA-1, INV-DBA-4, EXP-DBA-3, QT-DBA-4; balance aggregation INV-BE-6 / V-DI-6.
- **Single fix:** add a currency domain/CHECK (or FK to a currencies table) and decide the multi-currency policy before summing balances.

### P-12 · 🔒 High · Tests run on SQLite while production is PostgreSQL, and most modules have no tests
The test DB is SQLite, so `gen_random_uuid()`, partial/GIN indexes, `pg_advisory_xact_lock`, `with_for_update()`, and the `case`/`extract`/`cast` statistics SQL are silently no-ops or behave differently — the suite validates against a database that doesn't represent production. Combined with broken CI (settings can't import) and near-zero feature-module coverage, several confirmed runtime bugs (INV-BE-1 duplicate, VEND-BE-2 `get_detail`, CUST-BE-1) would each be caught by a single endpoint test.
- **Instances:** MW-DBA-1, V-MAINT-2, SH-OPS-1, V-MAINT-3, and the per-module `*-OPS` "no tests" findings.
- **Single fix:** fix CI env so settings import, point tests at the `postgres:16` service already declared in CI, and add endpoint smoke tests per module (reserve SQLite for pure-logic unit tests only).

### P-13 · 🔒 High · File-storage attack surface (expenses today, owner-logo next)
Local-filesystem storage with path-confinement and MIME/size guards living at the call site (router) rather than in `StorageService`; unauthenticated upload that trusts client MIME; unauthenticated download that serves a raw filesystem path; orphaned files on hard-delete.
- **Instances:** EXP-SEC-1/SEC-2, EXP-BE-4, EXP-OPS-2, SH-SEC-1, V-DRY-3, V-SCALE-2 (and the forthcoming owner-logo uploader will inherit all of it).
- **Single fix:** centralize MIME/size/extension guards and `Path.resolve()` confinement **inside** `StorageService`, require auth + ownership on upload/download, content-sniff uploads, and implement the S3 backend before the logo feature copies the local pattern.

## B. Module maturity snapshot

A relative read of how close each module is to the four pillars (not a grade — a sequencing aid):

- **`quotes`** — strongest service: every transition via `_transition`, atomic conversion, advisory-lock references, `FOR UPDATE` on send. Gaps: no expiry job, cascade conflict, FE wiring.
- **`expenses`** — most operationally complete: `FOR UPDATE` on both payment paths, MAX references, real overdue job, upload pipeline. Gaps: the storage security surface (P-13).
- **`invoices`** — functionally rich but the financial core has the most correctness bugs (duplicate response, cancel/payment bypass the state machine, no payment row-lock).
- **`customers`** — strong delete-guard design undermined by a 500 on the detail endpoint and the destructive quote cascade.
- **`vendors`** — safest delete posture (no cascade) but `PUT`-status bypass and dead/unrouted code (`get_detail`).
- **`auth` / middleware / shared** — good primitives (bcrypt, exception envelope, pagination, `StatementGenerator`) undercut by OTP brute-force exposure, the broken 429 path, and the SQLite-vs-Postgres test gap.

## C. Platform-wide remediation order

**Wave 1 — Security & correctness foundation (unblocks everything else)**
1. **P-1** auth + `CurrentUser` threading + client token interceptor + RBAC for destructive/financial actions.
2. **P-12** fix CI env, run tests on PostgreSQL, add per-module endpoint smoke tests — needed to safely make every subsequent change.
3. **P-13** centralize storage guards + path confinement + auth on upload/download (before the owner-logo feature).
4. Confirmed runtime breakers: INV-BE-1 (duplicate), VEND-BE-2 (`get_detail`), CUST-BE-1 (customer detail 500), V-REL-2/P-8 (cancel/payment state-machine bypass + payment row-lock), V-DI-2/QT-DBA-1 (quote cascade), W-1 (429 path).

**Wave 2 — Data integrity & shared correctness**
5. **P-3** money rounding policy (backend + frontend).
6. **P-8** route all status changes through one state machine; remove `status` from update schemas.
7. **P-4** MAX reference strategy everywhere; **P-11** currency domain/CHECK + multi-currency balance policy; **P-6** centralize overdue/expired + add missing jobs.
8. **P-9** DB-enforced optimistic locking + frontend version threading.

**Wave 3 — Scalability, maintainability, polish**
9. **P-2** trigram search + `LIKE` escaping; **P-5** export batching/background jobs; **P-13**/V-SCALE-2 S3 backend.
10. **P-7** extract `BaseDocumentService`/mixins (invoices+quotes first, then expenses).
11. **P-10** reconcile FE/BE feature flags; wire PDF/send/export; fix the duplicate-navigation bugs (INV-FE-1/QT-FE-1).
12. Remaining per-module Low items, ORM-style standardization (V-MAINT-1), and the owner-details feature (Phase 2 of the roadmap), which depends on Wave 1 auth + Wave 3 storage.

## D. Stakeholder routing (consolidated)

- **🔒 @ai-security-analyst** — P-1, P-13, AUTH-SEC-1–5 (OTP brute-force, plaintext OTP, refresh revocation), CORS (MW-SEC-2), security headers (MW-SEC-1). Route to GitLab's **Security Analyst Agent** for autonomous triage.
- **🛠️ @ai-devops-engineer** — P-12 (CI + Postgres tests), P-5 (export jobs), P-6 (schedulers), rate-limiter shared store (W-4), import-time SES client (P‑adjacent OPS items).
- **🗄️ @senior-database-admin** — P-2 (trigram), P-4 (MAX refs), P-11 (currency domain), QT-DBA-1 (cascade), Postgres-only stats casts.
- **🖥️ @senior-backend-engineer** — P-3 (rounding), P-7 (base service), P-8 (state machine), P-9 (locking), the confirmed runtime breakers.
- **🎨 @senior-frontend-engineer** — P-1 client token, P-9 version threading, P-10 dead stubs + duplicate-navigation, P-3 client rounding.
- **🏗️ @systems-architect / 📋 @product-owner** — multi-currency policy (P-11), cancel-after-convert policy (QT-BE-2), owner-details bounded context + document immutability (Section 0 / V-DI-4), and the build-sequencing in Part III·C.

---

# Part IV — `lib/` Layer Review (config, email, storage + frontend lib)

> Reviewed end-to-end on `develop`: backend `api/app/lib/{config,email,storage}.py` and frontend `frontend/src/lib/{api,constants,utils,dateUtils}.ts`, `lib/types/index.ts`. `storage.py` is covered under Module 7 (EXP-SEC-1/P-13) and only cross-referenced here. The `lib/` layer holds process-wide configuration, the SES email client, object storage, the frontend HTTP client, and shared constants/types — so defects here have whole-application blast radius. Stakeholder tags as before.

## Backend — `app/lib/config.py`

### 🛠️ @ai-devops-engineer

- **LIB-OPS-1 · High · `ENVIRONMENT` Literal omits `test` — the root cause of the broken CI (confirms SH-OPS-1 / V-MAINT-2 / AUTH-OPS-1).** `ENVIRONMENT: Literal["development", "staging", "production"]`, but `api-ci.yml` sets `ENVIRONMENT: test`, which fails validation, so `Settings()` cannot instantiate and `pytest` never runs meaningfully. **Fix:** add `"test"` to the Literal (or set CI to `development`), alongside the ≥32-char `JWT_SECRET_KEY` and valid `DATABASE_URL` CI fixes.
- **LIB-OPS-2 · Medium · No production hardening assertions.** Nothing prevents `DEBUG=True`, empty `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or wildcard-ish `CORS_ORIGINS` when `ENVIRONMENT == "production"`. A misconfigured prod deploy will boot happily with debug error leakage (the unhandled-exception handler exposes type/message only in development — but "staging" is not "development", good) and no email creds. **Fix:** add a `model_validator(mode="after")` that fails fast in production on `DEBUG=True`, missing SES creds, or default secrets.
- **LIB-OPS-3 · Low · `settings = get_settings()` evaluated at import.** Module-level instantiation means any import of `app.lib.config` triggers env validation immediately — fine in app runtime, but it couples test collection and tooling to a complete env. The `@lru_cache`d `get_settings()` is the right primitive; the module-global is the anti-pattern. **Fix:** prefer injecting `get_settings()` (or a `Depends`) over importing the `settings` singleton.

### 🔒 @ai-security-analyst

- **LIB-SEC-1 · Medium · Dead `MOCK_PAYMENT_GATEWAY_KEY` secret-shaped config (confirms SH-SEC-2).** An unused, secret-named field with a hardcoded default invites confusion and accidental reliance. **Fix:** remove until a real gateway integration exists.
- **LIB-SEC-2 · Low · `JWT_SECRET_KEY` insecure-defaults check is shallow.** It rejects three exact (lowercased) strings and enforces ≥32 chars, but won't catch a 32-char low-entropy value (e.g. `"aaaa…"`). Acceptable as a guardrail; note it is not an entropy check. **Fix (optional):** add a basic entropy/charset-variety heuristic.
- **LIB-SEC-3 · Low · `CORS_ORIGINS` default includes localhost dev origins.** Harmless in dev, but combined with the wide-open `allow_methods/headers=["*"]` + credentials in `main.py` (MW-SEC-2) the default posture is permissive; ensure prod overrides it. **Fix:** pair with the MW-SEC-2 CORS tightening and a production assertion (LIB-OPS-2).

## Backend — `app/lib/email.py`

### 🖥️ @senior-backend-engineer

- **LIB-BE-1 · High · Retry/backoff protects only `send_otp`, not document emails.** The `@retry(stop=stop_after_attempt(SES_MAX_RETRIES), wait=wait_exponential(...))` decorator is applied to `send_otp` only. `_send` (the actual SES call) and `send_document_email` (invoices/quotes/statements) get **no** retry, so transient SES `ClientError`s fail those sends immediately despite `SES_MAX_RETRIES` existing. The natural place to retry is `_send`. **Fix:** move the `@retry` to `_send` so every send path benefits; remove it from `send_otp`.
- **LIB-BE-2 · Medium · Dev-mode SES skip guards only `send_document_email`, not `send_otp`.** `send_document_email` short-circuits when `ENVIRONMENT == development and not AWS_ACCESS_KEY_ID`; `send_otp` has no such guard, so in local dev without SES creds, login OTP delivery throws `EmailDeliveryException` instead of being logged-and-skipped. (Auth's own `_send_otp_email` dev path logs the code — AUTH-OPS-2 — but if it routes through this client it will fail.) **Fix:** apply the same dev-mode guard to `send_otp` (and centralise the skip in `_send`).
- **LIB-BE-3 · Medium · `EmailService()` constructed at import (reaffirms AUTH-OPS-4 / SH-OPS-3 / INV-OPS-4 / QT-OPS-4).** The module-level `email_service = EmailService()` builds the boto3 SES client on import; missing AWS config can fail import in some environments and couples every importer to SES. **Fix:** lazy/DI construction (factory or `Depends`).
- **LIB-BE-4 · Low · `send_document_email` HTML fallback wraps text in `<pre>` with no escaping.** When no `body_html` is supplied it sends `f"<pre>{body_text}</pre>"`; since the document email bodies interpolate customer-controlled `display_name` (INV-SEC-4/QT-SEC-4), unescaped HTML injection is possible into the email. Low risk today (plain wording) but real once names flow in. **Fix:** HTML-escape `body_text` before embedding, or send text-only when no HTML template exists.

### 🔧 @senior-backend-engineer / DRY

- **LIB-BE-5 · Low · Brand color `#1A1A2E` hardcoded again (confirms SH-DRY-2/SH-FE-2).** The OTP HTML template repeats the brand color and `APP_NAME` already duplicated in `pdf.py`, `excel.py`, and the frontend. **Fix:** single brand-token source consumed by PDF/Excel/email/UI.

## Backend — `app/lib/storage.py`

- Covered under Module 7. Key items recap: **EXP-SEC-1** (no `Path.resolve()` confinement to `base_dir`; raw-path `FileResponse`), **EXP-OPS-2 / V-SCALE-2** (local-only backend won't survive horizontal scaling; S3 backend unimplemented), **V-DRY-3** (upload guards live in the expenses router, not here). The owner-logo uploader (Section 0 / Phase 2) must not copy the local pattern before these are fixed.

## Frontend — `frontend/src/lib/api.ts`

### 🎨 @senior-frontend-engineer

- **LIB-FE-1 · Critical · The HTTP client sends no `Authorization` header and has no 401→refresh flow (root of P-1 on the client; reaffirms V-REL-1 / AUTH-FE-1).** `apiGet/apiPost/apiPut/apiDelete` build a bare `fetch` with only `Content-Type`; there is no token storage, no bearer header, and no refresh interceptor. Every module's service goes through this client, so wiring auth **here once** is the single change that authenticates the entire frontend. **Fix:** add a request interceptor that injects `Authorization: Bearer`, and a 401→`/auth/refresh`→retry wrapper.
- **LIB-FE-2 · Medium · No request timeout / `AbortController` (reaffirms MW-FE-2).** A slow/hung backend ties up the fetch indefinitely with no client-side cancellation on unmount. **Fix:** add a default timeout via `AbortController` and abort in-flight requests on component unmount.
- **LIB-FE-3 · Medium · 429 / `Retry-After` and `X-Request-ID` are ignored.** `handleResponse` maps error bodies to `ApiError(message, status)` but never honours `Retry-After` on 429 (pairs with the broken server-side 429 path W-1/MW-OPS-1) and never surfaces `X-Request-ID` for support correlation (MW-FE-1). **Fix:** back off on 429 using `Retry-After`; attach `X-Request-ID` to `ApiError` for error reporting.
- **LIB-FE-4 · Low · Multipart upload / binary download bypass this client (confirms EXP-FE-2).** The expenses document upload/download call raw `fetch(new URL(…, appConfig.apiUrl))` directly instead of going through `api.ts`, so they will miss the auth header added in LIB-FE-1. **Fix:** add shared `apiUpload`/`apiDownload` helpers here so multipart/binary paths inherit auth and error handling.

### 🔧 @senior-frontend-engineer / contract integrity

- **LIB-FE-5 · Low · `flattenPaginated` correctly reads `raw.metadata.*` but drops `has_next`/`has_prev`.** Verified the backend `PaginatedResponse` nests under `metadata` (matches `PaginatedApiResponse`) — so this is **correct**, not a bug. However `PaginatedResult` discards `has_next`/`has_prev`, forcing pages to recompute "is there a next page" from `total_pages`. **Fix (minor):** carry `has_next`/`has_prev` through for cheaper pagination controls.

## Frontend — `frontend/src/lib/constants.ts`

### 🧾 @senior-frontend-engineer / Data Integrity & DRY

- **LIB-FE-6 · High · `COMPANY_INFO` is the dual-source-of-truth that has already drifted (confirms V-DRY-1).** `COMPANY_INFO` (name/address/phone/email) is the frontend's hardcoded org identity, separate from the backend's `settings.APP_NAME`; the values differ (the backend has no address/phone/email at all). It is consumed by `DocumentEditor`, `DocumentViewer`, `ExpenseViewer`, and the vendor statement (VEND-FE-2). **Fix:** delete `COMPANY_INFO` in favour of the forthcoming owner-profile API + a single `DocumentOwnerHeader` (Section 0 / Phase 2).
- **LIB-FE-7 · Medium · Frontend `TAX_RATES` duplicates the backend `financial.TAX_RATES` and can drift.** A second authoritative tax table on the client risks calculating previewed totals with a different rate than the server persists (ties to the rounding-policy mismatch P-3/SH-FE-1). **Fix:** derive client tax rates from a single shared source (e.g. an API/config endpoint or generated constants) rather than re-declaring them.
- **LIB-FE-8 · Medium · `API_URL` has no fallback when `VITE_API_BASE_URL` is unset.** `const API_URL = API_BASE_URL + "/api/v1/"` yields the literal string `"undefined/api/v1/"` if the env var is missing, producing confusing request failures instead of a clear boot error. **Fix:** validate the env var at startup and fail fast (or default to a sensible dev base).

## Frontend — `frontend/src/lib/utils.ts`, `dateUtils.ts`, `types/index.ts`

### 🔧 @senior-frontend-engineer

- **LIB-FE-9 · Medium · Hand-maintained API type mirrors drift from the backend (contract integrity).** `types/index.ts` `CustomerStatement` (and the per-service `Vendor`/`Invoice`/`Quote` interfaces) are manually mirrored from Pydantic schemas — the source of VEND-FE-1 (`statement.vendor.phone` that doesn't exist) and the duplicate-response mismatches (INV-FE-1/QT-FE-1). **Fix:** generate TypeScript types from the OpenAPI schema (FastAPI emits it) so the contract can't silently drift.
- **LIB-FE-10 · Low · Date formatting duplicated across `utils.ts` and `dateUtils.ts`.** `formatDate`/`formatDisplayDate` live in `utils.ts` while a separate `dateUtils.ts` exists; overlapping date helpers invite inconsistency (e.g. `en-KE` vs `en-US` locale used in different formatters within the same file). **Fix:** consolidate date helpers into `dateUtils.ts` and pick one locale policy.
- **LIB-FE-11 · Low · `formatCurrency` is display-only and won't match server rounding.** It uses `toLocaleString` with 2 dp for display, which is fine for rendering but is **not** the place totals are computed; the actual client total math (`components/documents/utils.ts`) must adopt the backend rounding policy (P-3/SH-FE-1). No change to `formatCurrency` itself — flagged so it isn't mistaken for the rounding fix.

### What the `lib/` layer does well

- **`config.py` is otherwise strong:** typed `BaseSettings`, bounded `Field` ranges on pool/JWT/rate-limit values, a real `JWT_SECRET_KEY` length+default check, strict `PostgresDsn` validation, `@lru_cache`d accessor, and clean `is_development`/`is_production`/`cors_origins_list` helpers.
- **`email.py` error handling is solid:** typed `EmailDeliveryException`, structured logging of SES error codes, and a polished, self-contained OTP HTML template — the only gaps are *where* the retry/dev-guard are applied (LIB-BE-1/BE-2).
- **`api.ts` error normalization is good:** it unpacks FastAPI validation `details.errors` into readable `field: msg` strings and maps to a typed `ApiError(status)` — a clean single choke point that makes the auth/timeout/429 fixes (LIB-FE-1/2/3) easy to add in one place.
- **`flattenPaginated` + the `PaginatedApiResponse`/`metadata` contract align correctly** with the backend `PaginatedResponse` — verified, no drift here.
- **`constants.ts` centralizes pagination/OTP/currency/VAT options** sensibly; the issue is only the org identity (`COMPANY_INFO`) and the duplicated tax table.

### `lib/` layer — priority order

1. LIB-OPS-1 (CI `ENVIRONMENT` — unblocks the whole test suite) and LIB-FE-1 (client auth — the single point that authenticates the frontend; P-1).
2. LIB-BE-1 (retry on `_send` so document emails are resilient), LIB-BE-2 (dev-mode OTP skip), LIB-OPS-2 (production hardening assertions).
3. LIB-FE-6 (`COMPANY_INFO` → owner profile; V-DRY-1), LIB-FE-9 (generate TS types from OpenAPI — removes a whole class of FE/BE drift bugs), LIB-FE-7 (single tax-rate source).
4. LIB-FE-2/FE-3 (timeout + 429/request-id), LIB-FE-4 (shared upload/download helpers), LIB-FE-8 (`API_URL` fallback).
5. LIB-BE-3 (lazy SES client), LIB-BE-4 (HTML-escape), LIB-SEC-1/SEC-2/SEC-3, LIB-OPS-3, LIB-BE-5, LIB-FE-5/FE-10/FE-11.

> Cross-reference: LIB-OPS-1 → P-12, LIB-FE-1/FE-4 → P-1, LIB-FE-6 → V-DRY-1/Section 0, LIB-FE-7/FE-11 → P-3, LIB-FE-9 → the FE/BE contract-drift bugs (VEND-FE-1, INV-FE-1, QT-FE-1), storage items → P-13.

---

# Part V — Independent Verification Addendum

> This addendum records an independent re-review of the codebase on `develop`, performed by directly reading the source files rather than re-summarizing the report above. Its purpose is to (a) confirm or correct the most consequential findings against the actual code, (b) flag one wording inaccuracy, and (c) add net-new observations. Each item below cites the file inspected.

## V.1 — Confirmed findings (verified against `develop` source)

- **VER-1 · `Customer.quotes` destructive cascade — CONFIRMED (V-DI-2 / CUST-DBA-1 / QT-DBA-1).** `api/app/modules/customers/models.py`: `quotes = relationship("Quote", … cascade="all, delete-orphan", lazy="select")`, while `invoices = relationship("Invoice", … cascade="save-update, merge", lazy="dynamic")`. The destructive cascade and the invoices/quotes asymmetry are both real. The `lazy="dynamic"` on invoices (V-SCALE-4) is also confirmed.
- **VER-2 · `cancel_invoice` bypasses the state machine — CONFIRMED (V-REL-2 / INV-BE-2).** `api/app/modules/invoices/service.py` `cancel_invoice()` sets `invoice.status = InvoiceStatus.CANCELED` and `invoice.version += 1` directly, never calling `_transition()`.
- **VER-3 · `record_payment` has no row lock and mutates status directly — CONFIRMED (V-REL-3 / INV-BE-3).** `record_payment()` loads via `self.get_by_id(invoice_id)` (a plain `joinedload` query, no `with_for_update()`), then sets `invoice.status = PAID/PARTIAL` inline. By contrast `send_invoice()` in the same file uses `.with_for_update()` — confirming the asymmetry the report calls out.
- **VER-4 · `duplicate_invoice` response-type mismatch — CONFIRMED (V-DI-3 / INV-BE-1).** `duplicate_invoice()` is annotated `-> InvoiceDuplicateResponse` but every success path executes `return duplicate`, where `duplicate` is an ORM `Invoice`. The router validating this against `InvoiceDuplicateResponse` (which requires `original_invoice_id`/`new_invoice_id`/`new_invoice_number`) will raise at response time.
- **VER-5 · Rate-limit middleware raises instead of returning a response — CONFIRMED (W-1 / MW-OPS-1).** `api/app/common/middleware.py` `RateLimitMiddleware.dispatch()` executes `raise RateLimitException(retry_after=60)` from within a `BaseHTTPMiddleware`, and keys solely on `request.client.host` (confirms W-5). No `Retry-After` header is set on the response path.
- **VER-6 · Customer detail endpoint swallows errors — CONFIRMED (CUST-BE-2).** `api/app/modules/customers/router.py` `get_customer()` wraps statement generation in a bare `except Exception: statement = None`.
- **VER-7 · Statement query params mistyped — CONFIRMED (CUST-OPS-1).** In `router.py` `generate_customer_statement()`, `period_start`/`period_end` are `Annotated[date, Query(…)] = None` (non-optional type defaulting to `None`).
- **VER-8 · Soft-deleted customers leak through reads — CONFIRMED (CUST-SEC-1).** In `api/app/modules/customers/service.py`, neither `list_customers()` (default path) nor `get_by_id()` excludes `CustomerStatus.DELETED`; `delete()` only sets `status = DELETED`.
- **VER-9 · Case-sensitive customer email uniqueness — CONFIRMED (CUST-DBA-2).** `create()`/`update()` match and store `data.email` verbatim with no lowercasing; `Customer.email` is `unique=True` on the raw string.
- **VER-10 · No audit attribution on customers — CONFIRMED (V-DI-1 / CUST-BE-6).** `CustomerService.create/update/delete` take no `user_id`, and the `Customer` model defines no `created_by`/`updated_by` columns.

## V.2 — Correction to a stated finding

- **VER-CORR-1 · CUST-BE-1 is a REAL bug, but the headline wording is imprecise.** The finding's summary line reads “`CustomerService` only defines `get_invoices` (the method is `get_customer_invoices` on the *router*, not the service).” Verified: the **router endpoint function** is named `get_customer_invoices`, and within `get_customer()` the code calls `service.get_customer_invoices(…)`. The **service** (`api/app/modules/customers/service.py`) defines only `get_invoices(…)` — there is no `get_customer_invoices` method on `CustomerService`. So the call **does** raise `AttributeError` → 500 on the detail view (bug CONFIRMED), but the recommendation should be stated precisely: in `router.py::get_customer`, change `service.get_customer_invoices(customer_id, …)` to `service.get_invoices(customer_id, …)` (note the existing `get_invoices` signature uses `status_filter=`, not a positional). The fix is a one-line call-site change plus an endpoint smoke test (CUST-OPS-2).

## V.3 — Net-new observations (not previously itemized)

- **VER-NEW-1 · Medium · `CustomerService.batch_update_status` can drive a customer into `DELETED`/any status with no guards and is unreachable/untested.** `service.py` exposes `batch_update_status(ids, new_status)` which issues a bulk `UPDATE ... synchronize_session=False` with **no** balance/open-quote checks (the same class as CUST-BE-4's status bypass, but in bulk) and no audit. It is not wired to any router endpoint reviewed, so it is latent dead code today — but if exposed it bypasses every lifecycle rule in `activate`/`deactivate`/`delete`. **Fix:** either remove it, or route it through the same guards + RBAC before exposing; add it to the test matrix.
- **VER-NEW-2 · Low · `check_delete_eligibility` undercounts “unpaid” by excluding `PARTIAL`.** In `service.py`, the unpaid-invoice warning filters `status.in_([SENT, OVERDUE])` only, omitting `PARTIAL` (which still carries a `balance_due > 0`). A customer with only partially-paid invoices can be reported as safe to hard-delete. **Fix:** include `PARTIAL` (and align with the `_update_customer_balance` notion of outstanding, which excludes only `CANCELED`/`DRAFT`).
- **VER-NEW-3 · Low · `_calculate_balance_at_date` opening balance ignores invoice status (counts DRAFT/CANCELED).** The statement opening balance sums `Invoice.total_due` for all invoices before the period start with no status filter, so DRAFT and CANCELED invoices inflate the opening balance — inconsistent with `_update_customer_balance`, which excludes `CANCELED`/`DRAFT`. This compounds the unrounded-money policy (SH-BE-1). **Fix:** exclude `DRAFT`/`CANCELED` from the opening-balance debit sum to match the live-balance definition.
- **VER-NEW-4 · Low · `list_customers` service return annotation mismatch reconfirmed.** `service.py` `list_customers()` is annotated `-> PaginatedResponse[CustomerResponse]` but constructs `CustomerSummary` items (reconfirms CUST-BE-5 against the service source).

## V.4 — Verification summary

Of the high-impact findings spot-checked against `develop` source, **10/10 confirmed** (V.1), **1 wording correction** (V.2, the underlying bug still real), and **4 net-new items** added (V.3). The report's overall accuracy is high; the Part III systemic groupings (P-1 auth, P-3 money rounding, P-8 state-machine bypass, P-12 SQLite-vs-Postgres tests) are well-supported by the code actually shipped on `develop`. No change is warranted to the prioritized roadmap; VER-NEW-1 should be slotted alongside CUST-BE-4 under P-8, and VER-NEW-2/NEW-3 under P-3 / data-integrity polish.
