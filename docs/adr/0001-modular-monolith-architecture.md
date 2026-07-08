# ADR-0001: Modular-monolith architecture on FastAPI

- **Status:** Accepted
- **Date:** 2026-07-06
- **Deciders:** Engineering
- **Related:** ADR-0003 (data), ADR-0006 (performance), ADR-0008 (scaling)

## Context
Priori CRM is a small-team B2B application spanning several accounting-adjacent domains — customers, quotes, invoices, vendors, expenses, purchase orders, statements, dashboard, owner/branding, and auth. It needs strong transactional integrity (financial documents), a single deployable unit that is cheap to operate, and clear internal boundaries so domains can evolve independently without the operational cost of microservices.

## Decision
We build a **modular monolith**: one FastAPI (Python 3.12) application, partitioned by domain module, deployed as a single service. Each module owns a strict, uniform layout and the service layer owns all business rules.

## What it does today
- Application factory + lifespan in [api/app/main.py](../../api/app/main.py); run via `uvicorn app.main:app` (4 workers, see [api/Dockerfile](../../api/Dockerfile)).
- All routers are mounted under `settings.API_V1_PREFIX` (`/api/v1`); OpenAPI docs are exposed **only in development** ([api/app/lib/config.py](../../api/app/lib/config.py)).
- Per-domain modules under [api/app/modules/](../../api/app/modules/), each with a consistent **`router.py` / `service.py` / `schemas.py` / `models.py` (+ `queries.py`)** layout. Cross-cutting infra lives in [api/app/common/](../../api/app/common/); config/email/storage in [api/app/lib/](../../api/app/lib/).
- Services are constructed via FastAPI dependency injection in [api/app/common/dependencies.py](../../api/app/common/dependencies.py).
- The **OpenAPI contract is exported and type-checked against the frontend** (`api:openapi-schema` in CI), preventing API/UI drift — the React client consumes generated types (`Schema<"PurchaseOrderResponse">`, etc.).

## Business logic & rules
- **The service layer is the only place business rules live** — routers are thin (validate → delegate → serialize); models hold schema + derived properties; `queries.py` holds read-optimized SQL. This keeps state machines, financial math, and locking in one testable place.
- **The OpenAPI schema is a contract.** Response shapes are owned by the backend; the frontend never hand-rolls types that the schema can generate (the PO-VAT `readPoVat` cast in `purchaseOrderApi.ts` is an explicit, temporary exception until the schema is regenerated).

## Consequences
- (+) One deploy, one datastore, cross-domain transactions are trivial (no distributed sagas).
- (+) Uniform module shape makes the codebase navigable and onboardable.
- (−) All domains scale together; a hot module can't be scaled in isolation (acceptable at current size — see ADR-0008).
- (−) Discipline required to keep module boundaries from eroding (enforced by review + the layered layout).

## Improvements
- Keep boundaries clean so any module could later be extracted if a domain genuinely needs independent scaling.
- Continue treating the exported OpenAPI schema as the single source of client types; retire hand-cast shapes promptly after backend changes.

## Resilience & <1s response rules
- Routers stay thin; no blocking/CPU-heavy work on the request path (heavy exports go through the concurrency gate — ADR-0006).
- Every new module follows the same layout so failure modes and performance characteristics stay predictable.
- The app is stateless (see ADR-0008); shared state lives in Postgres/Redis so any worker can serve any request.
