# ADR-0009: Rebrand to "Business Central" at the presentation layer only

- **Status:** Accepted
- **Date:** 2026-08-09
- **Deciders:** PM / engineering (issue #38)
- **Related:** Issue #38

## Context
Per PM direction, the accounting system is renamed to **Business Central** to reflect
expanded capabilities (Sales Desk module joining accounting), ahead of the 2026-08-15
go-live. Renaming internal identifiers (packages, database names, environment
variables, deployment resource names, the repository name) would require schema and
infrastructure migrations with real breakage risk and no user-visible benefit before
go-live.

## Decision
We rename **only user-facing occurrences** of the old name ("Priori",
"Priori Technologies", "Priori CRM", "PrioriTech") to "Business Central".
All internal identifiers stay unchanged.

## What it does today
The new name is applied to:

- **Frontend:** app `<title>` and meta/OG tags (`frontend/index.html`), PWA manifest
  (`frontend/public/site.webmanifest`), `appName` fallback (`frontend/src/lib/constants.ts`),
  sidebar branding and footer (`frontend/src/components/layout/sidebar.tsx`), auth screens
  (`frontend/src/pages/auth/*`), default email subject/body in the send modals
  (`SendInvoiceModal.tsx`, `SendQuoteModal.tsx`).
- **Backend:** `Settings.APP_NAME` default (`api/app/lib/config.py`), which feeds the
  OpenAPI title (`api/app/main.py`), transactional email subjects/footers
  (`api/app/lib/email.py`), and the PDF owner-header fallback (`api/app/common/pdf.py`).
  OpenAPI documentation examples in module schemas.
- **Docs:** `README.md`, `docs/database.md`, `docs/adr/README.md`.
- **Deployment values (not names):** `APP_NAME` value in `render.yaml` and `api/.env.example`.

## Business logic & rules
The rename boundary — these stay **unchanged**:

- Python/npm package names (`prioritech-api`, `frontend/package.json` name).
- Database names, users, and Docker resources (`prioritech`, `priori`,
  `prioritech_network`, `docker-compose.yml`, `api/init.sql`), and all Alembic history.
- Environment variable **names** (`APP_NAME`, `VITE_APP_NAME`, …) — only default
  **values** change.
- Repository name/URLs (`priori-crm`), deployed hostnames, and Render service names.
- Code-level identifiers: CSS design tokens (`--priori-purple` family), PDF color
  constants (`_PRIORI_*`), localStorage keys (`priori.*`), OTel meter name
  (`priori.api`), temp-file prefixes, and test fixture data.
- Existing ADRs (0001–0008) are immutable and keep the old name as historical record.

## Consequences
- Positive: zero schema/env/infra migration risk before go-live; the rename is a pure
  presentation change reviewable as string diffs.
- Negative: internal names diverge from the public brand; engineers must know that
  `priori*` identifiers are historical. The logo asset (`frontend/public/Logo Priori.svg`)
  still carries the old artwork/filename — replacing it needs a design asset and is out
  of scope here.

## Improvements
- Replace the logo SVG with Business Central artwork (design dependency), then update
  asset references.
- If/when a major infra migration is scheduled, consider renaming DB/package/repo
  identifiers in a dedicated, migration-tested change; supersede this ADR then.

## Resilience & <1s response rules
No runtime behavior changes: only string constants and static assets metadata changed.
Deploys stay compatible because deployments that set `APP_NAME` explicitly override the
new default.
