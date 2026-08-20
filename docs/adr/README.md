# Architecture Decision Records (ADRs)

This directory records the significant architectural decisions of **Business Central** (formerly Priori CRM) — what the system does, how it does it, the business logic it enforces, how it can be improved, and the rules that keep it resilient and reliable within a **<1s response budget**.

There was no prior ADR convention in this repo; this set establishes one. Decisions were previously captured as prose + Mermaid in the root `README.md`, `docs/database.md`, and rich inline docstrings — those remain authoritative for detail; ADRs capture the *decisions and their rationale*.

## Format
Each ADR follows [`0000-template.md`](0000-template.md): **Status · Context · Decision · What it does today · Business logic & rules · Consequences · Improvements · Resilience & <1s rules**. ADRs are immutable once *Accepted*; to change a decision, add a new ADR that supersedes it.

## Index
| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-modular-monolith-architecture.md) | Modular-monolith architecture on FastAPI | Accepted |
| [0002](0002-document-domain-and-financial-logic.md) | Document domain & financial business logic | Accepted |
| [0003](0003-data-layer-and-query-performance.md) | Data layer & query performance | Accepted |
| [0004](0004-authentication-and-security.md) | Authentication, authorization & security | Accepted |
| [0005](0005-background-jobs-and-email-outbox.md) | Background jobs & transactional email outbox | Accepted |
| [0006](0006-performance-and-response-budget.md) | Performance & the <1s response budget | Accepted |
| [0007](0007-reliability-backups-and-operability.md) | Reliability, backups, DR & operability | Accepted |
| [0008](0008-scalability-and-horizontal-scaling.md) | Scalability & horizontal scaling | Accepted |
| [0009](0009-rebrand-to-business-central-presentation-only.md) | Rebrand to "Business Central" at the presentation layer only | Accepted |
| [0010](0010-billing-profile-sync-as-internal-posting-flag.md) | Billing-profile "sync" is an internal posting/readiness flag | Accepted |
| [0011](0011-platform-operator-and-tenant-scoped-module-entitlements.md) | Platform-operator role and operator-granted module entitlements | Accepted |
| [0013](0013-pitr-continuous-wal-archiving-pgbackrest.md) | Point-in-time recovery via continuous WAL archiving with pgBackRest | Proposed |
| [0013](0013-tenancy-strategy.md) | Tenancy strategy — shared schema with tenant keys and RLS, phased | Proposed |
| [0014](0014-operator-mfa-and-step-up-auth.md) | Operator MFA (TOTP) and step-up re-auth for the platform console | Proposed |

> 0012 is reserved by `duo/feature/67-context-aware-access-control`
> (context-aware access control / ABAC), not yet merged.
>
> **Known collision:** `0013` is used by BOTH the PITR ADR (merged via
> !74) and the tenancy-strategy ADR (arriving via !77). Renumbering one
> of them belongs to !77's merge; new ADRs start at 0014.

Related backlog: [`../work-items.md`](../work-items.md).
