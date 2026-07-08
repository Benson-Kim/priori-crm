# ADR-0004: Authentication, authorization & security

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** Engineering
- **Related:** ADR-0006, ADR-0008, WI-04, WI-08, WI-12

## Context
The system holds financial and contact data for multiple staff roles. It needs strong authentication, least-privilege authorization on destructive/financial actions, resistance to common web attacks (enumeration, brute force, injection, clickjacking), and secrets hygiene — without a heavyweight external IdP.

## Decision
We use **custom JWT + email-OTP two-step auth** with **refresh-token rotation & family fencing**, **role-based authorization** on privileged endpoints, **Redis-backed rate limiting + token denylist**, security headers, strict CORS, and a **fail-fast production config validator**.

## What it does today
- **Auth flow** ([api/app/modules/auth/service.py](../../api/app/modules/auth/service.py), [api/app/common/security.py](../../api/app/common/security.py)): `login` (bcrypt password verify → emails a 6-digit OTP) → `verify_otp` → issues access (30 min) + refresh (7 day) JWTs (HS256). OTPs and password-reset tokens are stored **SHA-256 hashed, never plaintext**.
- **Refresh rotation**: refresh tokens carry a `jti`; rotation detects reuse and **revokes the whole token family** — a leaked token is usable at most once.
- **Authorization**: `require_role()` / `require_privileged()` ([api/app/common/dependencies.py](../../api/app/common/dependencies.py)); `PRIVILEGED_ROLES = ADMIN/MANAGER` gate destructive/financial endpoints (e.g. vendor delete).
- **Abuse protection**: global `RateLimitMiddleware` (identity = JWT sub → trusted XFF → socket IP) returns clean 429 + Retry-After; auth-specific throttles (`AUTH_LOGIN_MAX_ATTEMPTS`, OTP attempt lockout) keyed by hashed email.
- **Enumeration safety**: login/OTP/reset return one generic error; password-reset always returns success regardless of account existence.
- **Hardening**: `SecurityHeadersMiddleware` (nosniff, `X-Frame-Options: DENY`, CSP `default-src 'none'`, HSTS outside dev); explicit CORS allow-lists (no `*` with credentials), regex-scoped ([config.py](../../api/app/lib/config.py)); request-log middleware **redacts** token/otp/password/email; path-traversal defense in storage.
- **Injection**: all queries use SQLAlchemy ORM/Core parameterization; raw `text()` is limited to server defaults + the FTS expression — SQLi risk is low.
- **Secrets**: env-driven (`JWT_SECRET_KEY`, `INTERNAL_API_SECRET`, AWS) with `sync: false` in [render.yaml](../../render.yaml); none committed. The prod validator rejects DEBUG=True, missing SES creds, a default/short (<32-char) JWT secret, in-memory rate-limit backend, or missing Redis.

## Business logic & rules
- **Two factors always** for interactive login (password + emailed OTP).
- **Least privilege**: financial/destructive actions require ADMIN/MANAGER; everything else is authenticated-user scope.
- **Never leak account existence**; never log a secret; **never store an OTP/reset token in plaintext**.
- **Prod won't boot misconfigured** — the config validator fails fast rather than running insecure.

## Consequences
- (+) Strong auth with reuse detection; broad hardening; secrets stay out of the repo.
- (−) `get_current_user` hits the DB every request (WI-08) — a hot-path cost.
- (−) Rate-limit/denylist **fail open** on Redis outage (WI-12) — availability over security, to be ratified + alerted.

## Improvements
- Cache the per-request user lookup with denylist-aware invalidation (WI-08).
- Enable bandit/`S` SAST in CI (WI-04).
- Ratify + alert on the fail-open window; consider a stricter degraded mode for auth-sensitive routes (WI-12).

## Resilience & <1s response rules
- Auth checks are O(1) (JWT verify + a single indexed user lookup, to be cached) — no heavy work on the hot path.
- Shared limiter/denylist live in Redis so limits/revocations are consistent across workers/instances (ADR-0008).
- The fail-open tradeoff is explicit and **observable** (alert when Redis-unavailable activates it), never silent.
