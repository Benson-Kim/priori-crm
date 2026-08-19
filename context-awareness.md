# Context-aware access control (#67) — independent test report

**Branch under test:** `duo/feature/67-context-aware-access-control`
(head `b3c39763`, fetched from `gitlab`, checked out locally as `test/67-review`)
**Baseline:** `develop`
**Tester:** automated adversarial pass — no product code modified, nothing committed or pushed
**Report status:** iteration 4 — 2026-08-18 (Africa/Nairobi)

> **Headline:** five confirmed defects. One nightly cron job (`expenses`) has
> never run and takes two more jobs down with it (F-1); two tests fail for
> eight hours of every day (F-9); two security gaps let a stolen token operate
> largely unchallenged (F-5, F-6); and exfiltration detection silently degrades
> per-worker under a configuration nothing validates (F-10). Everything else in
> the ADR held up under direct attack — including the concurrency and
> multi-worker claims, which were the last untested ones — see §3, which is
> long on purpose.

---

## 0. How this was tested

Testing was deliberately *not* run through the branch's own test suite alone.
Those ~2,200 lines of new tests were written by the code's author, so green
only proves self-consistency. Every claim below was re-derived from
`docs/adr/0012-context-aware-access-control-abac-zero-trust.md` and then
attacked against a **live server**.

| Surface | Method |
|---|---|
| Test suite | Full 1,039-test run against **real PostgreSQL 16**, not the SQLite fallback — the SQLite path skips `SELECT … FOR UPDATE`, on which every durability claim in the ADR rests. Run **twice, on both sides of the off-hours boundary**, since the suite turned out to be wall-clock dependent (F-9) |
| Migrations | `alembic upgrade head` on a virgin DB, then downgrade × 5 and re-upgrade; schema reflected and diffed against `Base.metadata` |
| ABAC / risk engine | Live `uvicorn` against a **migrated** Postgres DB, driven over HTTP at **03:00–04:00 Africa/Nairobi — inside the production 22:00→06:00 off-hours window**, so the time-of-day rules were live, not simulated |
| Forged contexts | JWTs signed with the deployment's own key to reach shapes a client cannot produce (absent / stale / future / malformed `sua`, unknown `sid`, another user's `sid`) |
| Rate-limiter interaction | A second server run with `RATE_LIMIT_ENABLED=true` against the real Redis on `:6379` — the production shape |
| Time travel | Session rows backdated in SQL rather than waiting out real clocks (decay is per-hour, max age 24h) |
| Config space | `Settings` constructed directly with hostile `RISK_*` combinations |
| Cron jobs | Each internal endpoint POSTed with the real `X-Internal-Secret`, exactly as `.gitlab/ci/scheduled-jobs.yml` calls it |
| Concurrency | Eight same-session requests released together from a thread barrier, so the `SELECT … FOR UPDATE` lock was actually contended rather than assumed |
| Multi-worker | **Two independent server processes** on `:8077` / `:8078` sharing one Postgres and one Redis — the shape of `uvicorn --workers 2`; every concurrency test re-run split across both, and the volume store tested on `memory` and `redis` backends |

Temporary harnesses live in the session scratchpad, outside the repo. The only
change inside the working tree is this file. SES has no local credentials, so
outbound mail was redirected to a JSONL sink **by monkeypatching the factory at
process start** — `app/lib/email.py` itself was not edited.

---

## 1. Findings — cron / scheduled jobs

### F-1 (HIGH, confirmed) `POST /api/v1/expenses/internal/transition-overdue` can never succeed

Driven with the correct internal secret it returns **401**, while its four
siblings return 200:

```
/api/v1/invoices/internal/transition-overdue    HTTP 200  {"transitioned":0,…}
/api/v1/quotes/internal/transition-expired      HTTP 200  {"transitioned":0,…}
/api/v1/expenses/internal/transition-overdue    HTTP 401  {"error":"Authentication required.",
                                                           "error_code":"UnauthorizedException"}
/api/v1/auth/internal/purge-otps                HTTP 200  {"message":"Purged 0 …"}
/api/v1/internal/email-outbox/drain?limit=100   HTTP 200  {"processed":0,"delivered":0,…}
```

Cause — `api/app/modules/expenses/router.py:649`:

```python
def trigger_overdue_transition(service: ExpenseServiceDep) -> dict:
```

`ExpenseServiceDep` resolves through `get_expense_service(db, current_user)`
(`api/app/common/dependencies.py:232`), which depends on `CurrentUser` and so
demands a bearer token. The scheduler is a machine with only
`X-Internal-Secret`. Every other internal endpoint takes `DbSession` instead
(compare `api/app/modules/invoices/router.py:632`).

**Pre-existing on `develop`, not a #67 regression** — but it is a nightly job
that has never once run, and CI cannot see it for two independent reasons:

- `tests/test_overdue_expired_schedulers.py` exercises the *service* layer
  (`InvoiceService` / `QuoteService`) and never issues an HTTP request, so the
  dependency graph is never resolved;
- the branch's new `tests/test_zero_trust_enforcement.py` pins the *invoices*
  internal path four times and never touches the expenses one.

**Blast radius is larger than one job.** `.gitlab/ci/scheduled-jobs.yml`
(authoritative) runs the nightly set as sequential script lines that abort on
the first non-2xx:

```
invoices → quotes → expenses ✗ → purge-otps (never runs) → drain (never runs)
```

So the OTP purge and the nightly 200-row outbox drain have also never run on
GitLab. `render.yaml`'s nightly cron uses `set -e` and has the same cascade.

*Fix shape (not applied):* take `DbSession` and build the service inline, as
invoices does.

### F-2 (MEDIUM, confirmed) The GitHub mirror silently drops the expenses job

`.github/workflows/scheduled-jobs.yml:3` says it is a "GitHub-side mirror of
`.gitlab/ci/scheduled-jobs.yml` … kept in sync". It is not: its
`nightly-transitions` branch (lines 100–104) omits
`/api/v1/expenses/internal/transition-overdue`, which GitLab lines 98–103
include. Its header comment (lines 10–13) also lists only four endpoints.

Ironically this makes the GitHub path *more* functional today — dropping the
broken call means `purge-otps` and the nightly drain do run — but the
divergence is undocumented, and fixing F-1 without fixing this leaves expenses
permanently unprocessed on GitHub.

### F-3 (MEDIUM, confirmed by inspection) `render.yaml`'s nightly cron is shell-broken

`render.yaml:150-156`:

```sh
H="-H \"X-Internal-Secret: $INTERNAL_API_SECRET\"";
curl -fsS -X POST $H "$API_BASE_URL/api/v1/invoices/internal/transition-overdue";
```

Unquoted `$H` word-splits into three arguments — `-H`, `"X-Internal-Secret:`,
and `<secret>"` — so curl receives a malformed header name and treats the
secret fragment as a second URL. Every nightly call on Render fails. The
5-minute outbox-drain cron (lines 129–132) inlines `-H` correctly and is fine.
The three schedulers also disagree on when nightly runs: GitLab/GitHub at
02:00 UTC, Render at 00:15 UTC.

### F-4 (LOW) A drifted internal secret reports as "step up", not "unauthorized"

At 03:00 local, a wrong or absent `X-Internal-Secret` on a RESTRICTED or
CONFIDENTIAL-write internal path returns
`401 STEP_UP_REQUIRED — "Please sign in again to receive a verification code."`
The ABAC gate is an app-level dependency and runs before
`verify_internal_secret`, so a machine caller whose secret drifted sees a
message about email codes. Security is unaffected (wrong and absent secrets
produce the identical response — no oracle) but the cron log points an operator
at the wrong problem, and only between 22:00 and 06:00.

### Cron job matrix — what was verified

| Job | Endpoint | Endpoint works when invoked? | Schedule fires in prod? |
|---|---|---|---|
| Outbox drain (5 min) | `/internal/email-outbox/drain` | ✅ 200 `{"processed":0,…}` | not verifiable from here |
| Invoices overdue | `/invoices/internal/transition-overdue` | ✅ 200 | not verifiable from here |
| Quotes expired | `/quotes/internal/transition-expired` | ✅ 200 | not verifiable from here |
| **Expenses overdue** | `/expenses/internal/transition-overdue` | ❌ **401 — F-1** | never succeeds even if fired |
| OTP purge | `/auth/internal/purge-otps` | ✅ 200 | blocked behind F-1 on GitLab/Render |
| Synthetic probe | `/health`, `/ping` | ✅ 200, correct markers | not verifiable from here |
| DLQ inspect / requeue | `/internal/email-outbox/dead`, `/requeue` | ✅ reachable (operator-triggered, not cron) | n/a |

**"Endpoint works when invoked" was proven locally. "The schedule actually
fires in the deployed environment" was not, and cannot be from this
machine** — it depends on GitLab pipeline schedules, GitHub Actions schedules
and Render cron services that live outside the repo. Recent history
(`be653e9d`, `11ce9c84`) is all staging fork-deadlock and smoke-test timeout
work, so staging is not a trustworthy oracle for this right now either.

One thing worth stating plainly, because it was the obvious hypothesis and it
is **false**: the nightly jobs are *not* blocked by the off-hours rule.
`_rule_off_hours` returns `None` for `principal == "service"`
(`engine.py:147-160`), and `_is_verified_service_caller` requires a
constant-time match against `INTERNAL_API_SECRET`, so the exemption cannot be
claimed by attaching a garbage header. That part is correct.

---

## 2. Findings — ABAC / zero trust / session risk

Findings are numbered in the order they were discovered, not in severity or
file order. Index for readers:

| | Severity | In one line |
|---|---|---|
| F-5 | HIGH | A stolen token inherits `sua`, voiding off-hours protection for up to 8h |
| F-6 | HIGH | Mid-session country changes are scored only when coordinates imply impossible speed |
| F-7 | MEDIUM | Non-browser clients trip `device_change` on their own request paths |
| F-9 | HIGH | Two tests fail for eight hours of every day |
| F-8 | LOW | `RISK_SCORE_IMPOSSIBLE_TRAVEL ≥ terminate` is accepted at startup |
| F-10 | MEDIUM | Exfiltration detection degrades per-worker when the rate limiter is off |

### F-5 (HIGH) A stolen token *inherits* `sua`, so off-hours protection is void for up to 8 hours

ADR §5 defends `ABAC_STEP_UP_TTL_MINUTES = 480` with:

> "the security property is identical at any TTL because an attacker holding a
> stolen token has no inbox and so can never mint the claim at all."

The reasoning is about *minting* and skips *inheritance*. `sua` rides **inside
the access token** (`context.py:304`) and is carried across refresh rotation
unchanged. An attacker who steals a token pair from a user who stepped up ten
minutes ago holds a valid `sua` for the remaining ~7h50m — and
`_rule_off_hours` returns `None` for anyone with a fresh `sua`
(`engine.py:161`). Verified live at 03:00:

```
real session, CONFIDENTIAL write POST /invoices   422 VALIDATION_ERROR  (ABAC allowed)
real session, RESTRICTED  read   GET  /owner      200
forged: sua fresh    POST /invoices               422 (allowed)
forged: sua absent   POST /invoices               401 STEP_UP_REQUIRED
forged: sua stale 9h POST /invoices               401 STEP_UP_REQUIRED
forged: sua future   POST /invoices               401 STEP_UP_REQUIRED
forged: sua garbage  POST /invoices               401 STEP_UP_REQUIRED
sua before=1786927269 after=1786927269 unchanged=True   (refresh rotation)
```

The claim handling itself is exactly right — stale, future-dated and malformed
all fail closed, and rotation does not re-stamp. The defect is the *stated
rationale*: the property is **not** identical at any TTL. At 30 minutes the
stolen-token window is 30 minutes; at 480 it is 8 hours, which spans the entire
off-hours window the rule exists to guard.

**Scope of the impact:** off-hours only covers RESTRICTED paths (any method)
and CONFIDENTIAL *writes*. CONFIDENTIAL reads at night were never gated, by
design. So an inherited `sua` voids night-time protection for payment
recording, owner/platform surfaces, the audit trail and financial writes — not
for reading invoices, which was already allowed. The finding stands; it is
narrower than "all night-time access".

### F-6 (HIGH) A mid-session country change is scored only when coordinates imply impossible speed

The session-start soft signals (`new_device` 25, `new_country` 25,
`unusual_hour` 10) fire **only** when `session.last_seen_at is None`
(`risk.py:326`) — the session's first-ever scored request. Mid-session there is
no country detector: only `_detect_impossible_travel`, which needs
**coordinates** on both fixes and an implied speed above 900 km/h.

Two gaps follow. A **country-only edge** (an edge that stamps
`X-Geo-Country` but not `X-Geo-Lat`/`-Lon`) produces no relocation signal at
all. And a **patient attacker** defeats the speed test even with coordinates:
Moscow → Nairobi is ~6,300 km, so waiting 8 hours implies ~787 km/h, under the
900 km/h cap.

Live, with `ABAC_TRUST_CONTEXT_HEADERS=true`:

```
I1  token used for the first time from a foreign context (user made no request first)
    first-ever request: RU + unknown device        200   risk_score 50  risk_floor 50   ← works

I2  the ORDINARY shape — user makes one request, then the token is hijacked
    legit first request from KE/HomeBrowser        200
    hijack: RU + unknown device (country only)     200   risk_score 25  risk_floor 0

I3  hijack that also replays the device fingerprint
    three consecutive requests from RU             200 / 200 / 200   risk_score 25, session active
```

In I2 the only thing that fired was `device_change` (25), well under the
challenge threshold of 60. In I3 nothing fired at all.

**Corroborating evidence: `last_country` is write-only.** `grep -rn last_country
app/ tests/` returns exactly two hits — the column definition
(`app/modules/auth/models.py:198`) and the assignment
(`app/common/authz/risk.py:688`). It is faithfully maintained on every
geolocated request and **never read by any detector or test**. That reads as a
detector that was designed and never wired, not as an oversight in one branch.

The fingerprint in I3 is easy to match: in derived mode it is
`sha256(version-stabilised User-Agent + primary Accept-Language)`
(`context.py:206-228`), reproducible by anyone who knows the victim's browser
and locale; in trusted-header mode `X-Device-Fingerprint` is client-supplied
outright.

Note the gap is *structural*: absorption at `verify_otp` folds the login
context into the baseline, and the session-start check then runs against a
baseline that already contains it — so in normal operation the I1 path is
effectively unreachable, and `risk_floor` (the whole H10 non-decaying-floor
mechanism) stays 0. Every session produced by a *consistent* client in this run
had `risk_floor = 0`.

*Fix shape (not applied):* score `context.geo.country not in
baseline.known_countries` on every request, not only the first, gated on a geo
signal being configured so fail-safe degradation is preserved. The
`last_country` column already exists for it.

### F-7 (MEDIUM) Non-browser clients trip `device_change` on their own request paths

The derived fingerprint hashes the stabilised User-Agent **plus the primary
`Accept-Language` tag**. Browsers send that header consistently; API clients,
mobile SDKs, curl-based integrations and server-side callers frequently do not
send it on every code path. Observed accidentally in this run: a session whose
login sent `Accept-Language` and whose subsequent export loop did not scored a
spurious `new_device` (+25) purely from that difference.

`_stable_user_agent` was added specifically to stop browser auto-updates from
faking device changes; the `Accept-Language` component reintroduces the same
false-positive shape for non-browser clients, at +25 each and three flips to a
challenge.

### F-9 (HIGH, confirmed) Two tests fail for eight hours a day — the suite is wall-clock dependent

`tests/conftest.py` deliberately keeps the off-hours window **live** at its
production 22→6 setting, with this rationale:

> "Now that a completed OTP stamps the `sua` claim and the rule honours it, a
> normally-authenticated caller passes at any hour. `auth_headers` below mints
> that claim by default, so business tests are time-of-day independent
> *because the product is*, not because the rule is off."

That holds for tests using `auth_headers`. It does **not** hold for tests that
authenticate by FastAPI dependency override instead of a bearer token. Those
produce an anonymous access context with no `sua` at all, so a RESTRICTED path
is challenged.

Run at 04:11 Africa/Nairobi:

```
FAILED tests/test_deal_quotes_integration.py::test_sales_pricing_settings_read
       GET /api/v1/owner/settings/sales-pricing → 401 STEP_UP_REQUIRED (expected 200)
FAILED tests/test_onboarding.py::test_owner_endpoint_exposes_resolved_template_and_validates
       GET /api/v1/owner → 401 STEP_UP_REQUIRED (expected 200)
```

Isolated to the wall clock — same two tests, same database, only the window
changed (via a scratchpad pytest plugin, because conftest hard-codes 22/6 at
import time so an env var cannot override it):

```
off-hours window LIVE (22→6), local time 04:11       → 2 failed
off-hours window DISABLED (0/0), local time 04:11    → 2 passed
off-hours window LIVE (22→6), local time 18:48–19:16 → 2 passed
off-hours window LIVE (22→6), local time 22:59–23:21 → 2 failed
```

Lines 3 and 4 are natural-clock arms: unmodified settings, unmodified conftest,
no plugin — the same two files simply run on either side of the boundary,
inside the two full-suite runs below. So the failures are reproduced twice and
cleared twice, by two independent means (muting the rule, or moving the clock),
and nothing else distinguishes the runs.

All four times are measured rather than inferred, from per-chunk timestamps and
log mtimes.

Both tests call `_install_actor(owner)`, a dependency override, and never mint
a token. `/api/v1/owner*` classifies RESTRICTED, so `_rule_off_hours` challenges
any-method access without a fresh `sua`.

**Impact.** Off-hours is 22:00–06:00 Africa/Nairobi = **19:00–03:00 UTC**, so
for eight hours of every day any `api:test` run is red with no code change:
merge-request pipelines, `develop`/`main` pushes, and local runs by anyone
working an evening in the deployment's own timezone. The scheduled pipelines
are *not* affected — `api:test`'s rules (`.gitlab-ci.yml:116-125`) cover only
`merge_request_event` and develop/main pushes, not
`$CI_PIPELINE_SOURCE == "schedule"`.

This is the same defect shape the ADR describes as already-shipped-once, in a
new place: the conftest comment asserts the whole suite is time-independent,
and two tests quietly are not.

**Bounded — and the bound is measured, not argued.** The whole suite was run
twice, once on each side of the boundary, and the two runs are otherwise
identical: same 101 files, same database, same commit, no plugin, no settings
override.

```
                        inside 22→6            outside 22→6
                        (22:51 → 00:02)        (18:41 → 19:55)
  passed                1037                   1039
  failed                   2                      0
  total                 1039                   1039

  the 2:  test_deal_quotes_integration.py::test_sales_pricing_settings_read
          test_onboarding.py::test_owner_endpoint_exposes_resolved_template_and_validates
```

Every chunk of the night run carried a timestamp inside the window, including
the two that matter — `test_deal_quotes_integration.py` ran 22:59–23:12 and
`test_onboarding.py` 23:15–23:21. Nothing else in 1,039 tests moved.

This replaces what was previously a static argument. The earlier bound came
from intersecting `grep -rln dependency_overrides tests/` with the
RESTRICTED/CONFIDENTIAL-write route set, backed by a 122-test run of the other
seven dependency-override files at 04:30. That reasoning could have missed a
test reaching a gated path indirectly — through a fixture or helper rather than
a literal route string. The paired full-suite run cannot: it exercises every
test on both sides of the boundary and finds exactly two differences.

So F-9 is two tests, not a systemic rot — but two tests are enough to redden
every pipeline for a third of each day.

*Fix shape (not applied):* give the dependency-override tests a token carrying
`sua` the way `auth_headers` does, so they clear the rule for the product's own
reason rather than by muting it.

### F-8 (LOW) `RISK_SCORE_IMPOSSIBLE_TRAVEL ≥ terminate` is accepted at startup

ADR §6 pins travel at 70 because "termination [comes] only with corroboration,
because carrier-NAT geolocation jitter is a real false-positive source". The
startup validator (`config.py:281-341`) checks the challenge/terminate ordering
and both soft-batch sums, but nothing stops
`RISK_SCORE_IMPOSSIBLE_TRAVEL = 100`, under which one jitter event terminates
outright. Not a defect at defaults; an unguarded edge of the config space.

### F-10 (MEDIUM, confirmed) Exfiltration detection silently degrades per-worker when the rate limiter is off

`_volume_store()` (`risk.py:116`) builds the risk engine's counter from
`settings.RATE_LIMIT_BACKEND`, and its docstring states the intent plainly:

> "Built the same way as `_auth_throttle_store`, so a multi-worker deployment
> shares one window."

That holds only when the backend is `redis`. The production guard
(`config.py:367`) forces `redis` **only when `RATE_LIMIT_ENABLED` is true** —
and its own error message offers the escape hatch:

> "set `RATE_LIMIT_ENABLED=false` only for a single-process deployment"

So an operator who disables the limiter keeps `RATE_LIMIT_BACKEND=memory` (the
default), and the *exfiltration detector* — a separate subsystem the message
never mentions — quietly becomes per-worker. Nothing validates it, and outside
`ENVIRONMENT=production` the validator does not run at all.

Measured against two independent server processes sharing one Postgres and one
Redis — the shape of `uvicorn --workers 2`. The identical 66-request export
burst, once sent to a single process and once alternated across both:

```
RATE_LIMIT_BACKEND=memory (per-process store)
  arm 1  all 66 to :8077          refused at 61   terminated  score 130
  arm 2  alternating :8077/:8078  refused at 16   challenge_required score 60

RATE_LIMIT_BACKEND=redis (shared store)   ← positive control
  arm 1  all 66 to :8077          refused at 61   terminated  score 130
  arm 2  alternating :8077/:8078  refused at 61   terminated  score 130
```

The audit trail shows two distinct failures in the memory arm, not one:

```
arm 1 (single process)   volume_anomaly=1 (30) · exfiltration_volume (130) · session_terminated
arm 2 (split, memory)    volume_anomaly=2 (30 then 60) · session_challenged — no exfiltration event
```

- **The HARD ceiling is missed.** `risk:volx:{sid}` never reaches 1,500 units on
  either worker, so the signal that is supposed to *terminate* never fires. The
  effective ceiling multiplies by the worker count.
- **The SOFT signal double-counts.** The "already fired" latch
  (`risk:vol:fired:{sid}:{cls}`) is per-process too, so each worker raised the
  mild anomaly independently — 30 became 60 for one burst. Identical traffic
  scores differently depending on how the load balancer splits it.

The second half is the more insidious of the two: it makes the score itself
non-deterministic under a configuration that raises no error anywhere.

**Is this live or latent?** Latent on the one deployment the repo actually
configures, and reachable on any other:

- `render.yaml` sets `RATE_LIMIT_ENABLED=true`, `RATE_LIMIT_BACKEND=redis` and
  wires `REDIS_URL` from the Redis service (lines 52-80). **Render is safe.**
- `api/.env.example` — what an operator copies for a self-hosted deploy —
  ships `RATE_LIMIT_ENABLED=true`, `RATE_LIMIT_BACKEND=memory` and an empty
  `REDIS_URL` (lines 159-162). In production that exact combination *fails
  startup*, and the error it raises is the one quoted above, which names
  `RATE_LIMIT_ENABLED=false` as the way out. So the shortest path from the
  shipped template to a running production server passes straight through the
  affected configuration.
- No Passenger, lswsgi, gunicorn or `--workers` configuration exists anywhere
  in the repo, so **the deployed worker count could not be determined from
  here**. That is the fact that would settle live-vs-latent for the non-Render
  environments, and it lives outside this tree. Recent branch history is
  Passenger fork-deadlock work, which is suggestive of multi-worker but is not
  evidence, and is not treated as any here.

**The one concrete thing to check.** Staging runs under lswsgi rather than
Render, and its known failure mode is worker processes exhausting the host
memory cap — i.e. more than one worker. Whoever picks this up should read
staging's `RATE_LIMIT_BACKEND` and `REDIS_URL`. If the backend is not `redis`
there, F-10 is live on staging today and the grade above is too generous.

Graded MEDIUM on that basis: the mechanism is proven, the shipped deployment
config is safe, and the exposure depends on a worker count this review cannot
see. **If any environment runs more than one worker without
`RATE_LIMIT_BACKEND=redis`, re-grade to HIGH** — the exfiltration detector is
the only thing standing between a stolen session and a bulk export, and under
that configuration it does not fire.

*Fix shape (not applied):* decouple `_volume_store()` from
`RATE_LIMIT_ENABLED`, or extend the guard so `RATE_LIMIT_BACKEND=redis` is
required whenever `ABAC_ENABLED` is true, regardless of the limiter.

---

## 3. What held up under attack

Everything below was tested adversarially and **passed**.

**The full suite is green outside the off-hours window, and carries exactly two
failures inside it.** All 101 test files against real PostgreSQL 16, run twice
in eleven sequential chunks — once in clean hours and once inside the window:

```
18:41 → 19:55 Africa/Nairobi   1039 passed, 0 failed, 0 errors   (~67 min)
22:51 → 00:02 Africa/Nairobi   1037 passed, 2 failed, 0 errors   (~71 min)
```

The two runs differ only in the wall clock, and differ in exactly two tests —
both F-9's. This closes the coverage gap left by three earlier runs that were
killed by process cleanup at 68%, 22% and 4% (those deaths were harness
artefacts, not defects), and it turns F-9's blast radius from a static
grep-and-reason argument into a measured one.

**Step-up satisfiability.** The defect the ADR documents as already-shipped-once
(off-hours being an unanswerable 22:00→06:00 lockout) is genuinely fixed. At
03:00 local a full `login → verify-otp` round trip cleared CONFIDENTIAL writes
and RESTRICTED reads. Absent / stale / future-dated / malformed `sua` all fail
closed. Refresh rotation carries the claim unchanged.

**Graduated escalation** — precisely the documented arithmetic, live:

```
attempt 1 GET /platform/owners   403 ForbiddenException     (25)
attempt 2                         403 ForbiddenException     (50)
attempt 3                         403 ForbiddenException     (75 → crosses 60)
attempt 4                         401 STEP_UP_REQUIRED
next ordinary read                401 STEP_UP_REQUIRED
→ escalation_count=3, risk_score=75, status=challenge_required
```

The F4 semantics hold exactly: the crossing request keeps its RBAC 403, the
transition commits with it, every subsequent request is refused.

**Impossible travel → challenge, never terminate.** Nairobi → 4 km away seconds
later: no signal (the 100 km floor works). Nairobi → New York seconds later:
70 points, `challenge_required`, **not** terminated. Exactly as ADR §6 pins it.

**Exfiltration → terminate.** 60 × `/invoices/export/excel` at
`RISK_VOLUME_EXPORT_COST=25` = 1,500 units = the 300 × 5 ceiling; refused on
request 61 with `Session terminated by access policy`, final score 155. The
per-request export cost is doing real work.

**F8 rate-limiter latch — verified in the production shape** (`RATE_LIMIT_ENABLED=true`,
Redis backend, `RATE_LIMIT_PER_MINUTE=10`). The limiter rejects before the gate
runs, so without `note_rate_limit_rejection` the harder an attacker pulls the
less the risk model would see. In practice:

```
storm of 70 exports        → {200: 10, 429: 60}   session still active, score 25
t+10s served request       → 429 (limiter window not yet rolled)
t+20s served request       → 429
t+30s served request       → 401 Session terminated by access policy
                             score 125, "risk score 125 crossed terminate threshold"
audit: exfiltration_volume, rate_limited_evidence = true, scope = session
```

The 429 storm fed the counters, the latch bridged the window boundary, and the
first served request afterwards read it. Working as designed.

**Zero-trust DB guard — all seven cases** (driven directly against the
listener, not through a route):

```
request-scoped, NO verdict:  ORM query REFUSED · ORM 2.0 select REFUSED
                             raw text('SELECT 1') REFUSED · raw SELECT * FROM users REFUSED
                             flush REFUSED
request-scoped, DENY:        ORM REFUSED · raw SQL REFUSED
request-scoped, CHALLENGE:   ORM REFUSED
request-scoped, ALLOW:       ORM passes · raw SQL passes
NOT request-scoped:          passes (scheduler / script path, as documented)
audit_bypass():              passes inside the context manager, REFUSED after it exits
ABAC_ENABLED=False:          guard disabled entirely
```

Notably the fence is **wider than the ADR claims**: `Session.execute(text(...))`
is refused too, because SQLAlchemy 2.0 routes all `Session.execute` through
`do_orm_execute`. Raw-SQL routes are inside the fence, not outside it.

**Decay, floor and expiry** (session rows backdated in SQL):

```
2 device changes → score 50; backdate risk_updated_at −3h; next clean request
  → stored score still 50   ← correct: decay is computed on read and settled
                              only when a detector fires, so quiet sessions cost no writes
3 device changes → challenge_required score 75; backdate −48h (would shed 480)
  → still challenge_required, still 75   ← decay never restores a flipped session
session-start softs → score 50, floor 50; backdate −48h
  → score still 50            ← the non-decaying floor holds
last_seen_at −13h (idle timeout 12h) → terminated / "session idle timeout"
created_at −25h  (max age 24h)       → terminated / "max session age exceeded"
```

Each expiry carries its own audited reason, cleanly distinguishable from a risk
kill in the trail.

**Concurrency — the `FOR UPDATE` lock does what it claims.** Every other probe
in this run was sequential, so the lock was never actually contended. Eight
requests released together from a thread barrier, against one session:

```
8 racing device changes            → score 25, ONE device_change      (not 8 × 25)
8 racing escalations (primed 45)   → 1 × 403 + 7 × 401; esc=1, score 70
                                     audit: privilege_escalation=1, session_challenged=1
8 racing travel requests (primed 45) → 8 × 401; score 115, terminated
                                     audit: impossible_travel=1, session_terminated=1
same travel burst sent sequentially → score 115, identical audit
```

No lost updates, no double-counting, no 5xx, no deadlock. The crossing is
audited exactly once and the flipped state is visible to the racing peers
immediately — in the escalation burst, one request scored and the other seven
were already being refused. Concurrent and sequential execution converge on the
same state, which is the whole point of the lock.

**Multi-worker — identical results across two processes.** The same bursts
split across two independent server processes sharing one Postgres:

```
session minted on :8077, used on :8078        → 200 (honoured)
8 racing escalations, 4 per process           → esc=1, score 70, one session_challenged
8 racing travel requests, 4 per process       → score 115, terminated once
terminated on :8077, next request on :8078    → 401, refused by a process
                                                that never terminated it itself
```

Because the lock is a database row lock, it holds across processes exactly as
it does across threads. The session-state half of the risk model is genuinely
multi-worker safe. The counter half is not, under one configuration — see F-10.

**The baseline race — the one HEAD is about — holds.** The branch head is
`b3c39763` "Keep pre-loaded objects out of the baseline-race blast radius
(#67 H14)", so `get_or_create_baseline`'s SAVEPOINT upsert deserved contention
of its own rather than inheriting confidence from the session-row tests. The
baseline row was deleted and eight requests released together, split across
both processes, so all eight raced to create a row that did not exist:

```
after DELETE                    0 baseline rows
8 racing requests (4 per proc)  {200: 8}   → 1 row
repeated 3 more times           {200: 8} → 1 row, every time
session after the races         active, score 0   (no spurious signal)
```

Exactly one row each time, no unique-constraint 500s, and — worth noting — a
*missing* baseline does not itself score the user. Across two processes this is
the genuine unique-constraint race, not an intra-process one the identity map
could mask. H14's fix does what it says.

**Device-change false-positive suppression.** `ProbeBrowser/1.0 → 1.0.1` (a
browser patch bump) correctly produced **no** signal; three genuine browser
changes produced 75 → challenge. `_stable_user_agent` earns its place (see F-7
for the `Accept-Language` half).

**Session identity anomalies.** Unknown `sid` → terminate. `sid` with a
mismatched `sub` → terminate, and the *legitimate* token for that session is
dead afterwards too. Legacy no-`sid` tokens pass unscored, as documented.

**IP reputation**, exact / CIDR / literal / boundary:

```
198.51.100.4    200      203.0.113.7     403      10.9.9.9        403
203.0.113.255   403      203.0.114.1     200      "badclient"     403
denylisted IP on /health   200   (PUBLIC probes bypass evaluation, by design)
denylisted IP on /auth/login 403  (DENY applies before authentication)
```

With `RATE_LIMIT_TRUST_FORWARDED_FOR=true` a denylisted client can spoof a
clean first `X-Forwarded-For` hop and pass. That is the documented trust model
(the setting means "my edge overwrites XFF") and is identical to the rate
limiter's, so it is noted rather than filed as a finding.

**Geo blocklist**, case-insensitive (`kp` matched `KP`), PUBLIC probes exempt.

**Migrations.** `alembic upgrade head` clean on a virgin database; downgrade
through all five #67 revisions and back up, clean. Worth stating because the
pytest suite builds schema with `create_all` and so **never executes a single
migration** — a model/migration split would have passed every test.

Reflected schema vs `Base.metadata`: no missing or extra tables, no missing or
extra columns anywhere. `user_sessions` and `user_behavior_baselines` are
`timestamp with time zone` throughout, matching the models — which matters,
because `risk.py` does security-relevant datetime arithmetic on those columns.
Four **pre-existing, non-#67** nullability divergences exist and are recorded
here for completeness: `users.created_at`, `users.updated_at`,
`otp_codes.created_at`, `password_reset_tokens.created_at` are nullable in the
migrated database and `NOT NULL` in the models.

**Config validation.** The ADR's structural guarantee — "startup validation
keeps the maximum possible floor below the terminate threshold" — survives
attack. Attempts to push `risk_floor` (`ND+NC+UH`) to ≥ terminate while zeroing
`RISK_SCORE_VOLUME_ANOMALY` to dodge the batch check were still **rejected**,
because the validator's sum includes the volume weight and is therefore
strictly stronger than the floor. `challenge ≥ terminate` rejected. Max floor
at defaults = 60 < 100.

**Holds per worker, though — see F-10.** The validator's arithmetic assumes
each soft signal fires at most once per window, and that assumption is a
per-process one: the `risk:vol:fired:{sid}:{cls}` latch lives in the volume
store, so on the memory backend the mild volume anomaly can fire once *per
worker*. At four workers, volume anomaly alone reaches 120 — above the
terminate threshold the validator was proving unreachable. The soft-clamp rule
should still prevent a soft-only crossing from *terminating* (that path was
verified separately and held), so the practical consequence is an inflated
score and a spurious challenge rather than a spurious kill. The structural
guarantee is nonetheless narrower than it reads: it is a single-process
guarantee.

**Audit trail.** Every decision class lands, with both raw and decayed score:

```
policy_challenge 19 | baseline_absorbed 12 | device_change 6 | session_challenged 4
policy_terminate 4  | privilege_escalation 3 | session_terminated 2
impossible_travel 2 | soft_signal_new_device 2 | exfiltration_volume 1
soft_signal_new_country 1 | volume_anomaly 1
```

---

## 4. Not yet covered

- **The `ABAC_TRUST_CONTEXT_HEADERS` edge invariant now has an
  authenticated mode** (issue #83, superseding the original "cannot be
  tested in-repo" caveat here). Deploy-time checklist:

  1. Set `ABAC_EDGE_HMAC_KEY` (shared with the edge) so the edge stamps
     `X-Geo-Signature: v1=HMAC_SHA256(key, country|lat|lon|unix_minute)`
     — geo headers are then honoured ONLY with a valid, fresh signature;
     unsigned/spoofed geo degrades to "no geo signal" (fail-safe). Rotate
     with `ABAC_EDGE_HMAC_KEY_NEXT` (dual-key, zero downtime, mirrors
     `INTERNAL_API_SECRET_NEXT`).
  2. Set `ABAC_EDGE_CIDRS` to the edge's egress ranges as defence in
     depth (direct-peer check, never X-Forwarded-For) — or standalone
     for an edge that cannot stamp an HMAC.
  3. Check the startup log: the API logs the effective trust mode
     (`hmac` / `cidr` / `hmac+cidr` / `unauthenticated`); running
     trust-headers with NO edge authentication logs
     `ALERT: ABAC_EDGE_UNAUTHENTICATED`, because in that legacy mode the
     stripping invariant is purely operational and its failure is
     invisible at request time.

  The provenance split is now enforced in code, not just recorded:
  `client:`-prefixed fingerprints are **corroborating-only by
  construction** — the server-`derived:` fingerprint rides alongside in
  the `AccessContext`, a `client:` baseline match suppresses the
  new-device signal only when the derived form is also known, and a
  passed step-up absorbs both forms. `X-Device-Fingerprint` itself
  remains self-attested (browser-sent by design, F3) — no edge signature
  can cover it, which is exactly why it can no longer raise trust on its
  own. Coverage: `test_edge_authentication.py` (spoofed-geo rejection,
  skew, rotation, CIDR gating, fingerprint corroboration).
- Whether the schedules actually fire in the deployed environments (see §1).
  This is the only item left that cannot be closed from this machine.
- Real multi-worker deployment under a process manager (Passenger / lswsgi
  rather than two hand-started uvicorn processes). The lock and store
  behaviour proven in §3 and F-10 should carry, since both hinge on shared
  Postgres and Redis rather than on the supervisor, but that is an inference.
- Failure modes of the shared store itself: Redis dropping mid-session, and
  whether the in-memory fallback re-opens F-10's gap transiently when it does.

## 5. Environment / cleanup state

Left behind by this run, for whoever picks it up:

- Local branch `test/67-review` at `b3c39763` (was on
  `duo/fix/staging-passenger-fork-deadlock`).
- Scratch databases `prioritech_migtest` (migrated, seeded, full of probe
  sessions) and `prioritech_test` (suite scratch) in the `priori-crm-db-1`
  container. Both are disposable.
- Seeded dev user `abactest@priori.co.ke` in `prioritech_migtest` only.
- Redis DB 3 on `:6379` holds rate-limit and risk-volume keys from the F8 test;
  DB 4 was used for F-10's shared-store control and flushed at the start of it
  (its keys carry a 60-second TTL and are gone by now).
- No server left running — ports 8077 and 8078 are free and Postgres is back to
  idle. All harnesses are in the session scratchpad, outside the repo.
- Docker Desktop and the `priori-crm-db-1` / `prioritech_redis` containers were
  found stopped partway through and restarted; they are left running.
