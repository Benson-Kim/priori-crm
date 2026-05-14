# Priori Technologies — Accounting Software

> Modern accounting & CRM platform built with FastAPI and React.

## Architecture

```
accounting-software/
├── api/          # Python FastAPI backend
├── ui/           # React + TypeScript + TailwindCSS frontend
├── .github/      # CI/CD workflows
└── docker-compose.yml
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic |
| **Frontend** | React 18, TypeScript, Vite, TailwindCSS, lucide-react |
| **Database** | PostgreSQL 16 |
| **Email** | AWS SES |
| **CI/CD** | GitHub Actions |

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
cd ui
npm install
npm run dev
```

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

## License

© 2026 Priori Technologies — All Rights Reserved
