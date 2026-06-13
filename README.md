# Priori Technologies — Accounting Software

> Modern accounting & CRM platform built with FastAPI and React.

## Architecture

```
accounting-software/
├── api/             # Python FastAPI backend
├── ui/              # React + TypeScript + TailwindCSS frontend
├── .gitlab-ci.yml   # CI/CD pipeline (authoritative)
├── .github/         # GitHub Actions workflows (kept in sync with GitLab CI)
└── docker-compose.yml
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic |
| **Frontend** | React 18, TypeScript, Vite, TailwindCSS, lucide-react |
| **Database** | PostgreSQL 16 |
| **Email** | AWS SES |
| **CI/CD** | GitLab CI/CD (`.gitlab-ci.yml`, authoritative) + GitHub Actions (`.github/workflows/`, mirrored) |

## Getting Started

### Prerequisites

- Python 3.12+
- Node.js 20+
- PostgreSQL 16+ (or Docker)

### Quick Start (Docker)

```bash
docker compose up
```

- API: http://localhost:8000
- UI: http://localhost:5173
- API docs: http://localhost:8000/docs

### Manual Setup

#### API

```bash
cd api
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

#### UI

```bash
cd frontend
npm install
npm run dev
```

### OpenAPI Schema Generation

When you add new endpoints or modify Pydantic schemas in the backend, you must regenerate the OpenAPI JSON and the frontend TypeScript types:

```bash
# 1. Export the schema from the backend (does not require a running DB)
cd api
$env:JWT_SECRET_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
$env:ENVIRONMENT="development"
$env:RATE_LIMIT_ENABLED="false"
python export_api.py ../frontend/openapi.json

# 2. Generate TypeScript types for the frontend
cd ../frontend
npx -y openapi-typescript@^7 openapi.json -o src/lib/api-schema.d.ts
```

*(Note: Use the commands above instead of `npm run gen:api` if you are on Windows, as the package.json script contains bash-specific syntax.)*

## Git Workflow

| Branch | Purpose |
|--------|---------|
| `main` | Production releases |
| `develop` | Integration branch |
| `feature/*` | New features |
| `bugfix/*` | Bug fixes |
| `hotfix/*` | Critical production fixes |
| `release/*` | Release preparation |

### Commit Convention

```
<type>(<scope>): <description>

feat(api): add customer CRUD endpoints
fix(ui): correct OTP input focus behavior
```

## Health & Monitoring

The API exposes a small, intentional set of health/uptime endpoints:

| Endpoint | Audience | Response |
|----------|----------|----------|
| `GET /api/v1/ping` | Uptime checks | `{ "ping": "pong" }` |
| `GET /api/v1/health` | Load balancers (public) | `status`, `version`, `environment`, `timestamp` |
| `GET /api/v1/health/detailed` | Internal (requires `X-Internal-Secret`) | adds `database`, `pool`, `redis` |

`GET /health` is deliberately minimal: it carries no service-name field and
no infrastructure detail (those belong on the secret-gated `/health/detailed`).
`version` is the semantic application version (`settings.APP_VERSION`).

## Continuous Integration

`.gitlab-ci.yml` is the **authoritative** pipeline: it runs lint, an offline
OpenAPI contract export + generated-type check, the Postgres-guarded test
suite, and the managed security analyzers (SAST, dependency scanning, secret
detection) on every merge request and on `develop`/`main`.

The `.github/workflows/` Actions are kept **in sync** with that pipeline
(`api-ci.yml`, `ui-ci.yml`, `security.yml`) so the project stays CI-ready if
GitHub ever becomes the default VCS. Keep the two in step when changing either
one.

## License

© 2026 Priori Technologies — All Rights Reserved
