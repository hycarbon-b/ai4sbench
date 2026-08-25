# ai4sbench control plane

This directory is the private control-plane service. It owns FastAPI APIs,
GitHub OAuth, Proposal-to-Discussion creation, SQLite state, durable database
jobs, EC2 lifecycle management, worker callbacks, migrations, and operator
infrastructure.

`data/` and `.env` are local operator state. They are ignored by Git and must
not be copied into `benchmark-repository/`.

## Development

```bash
cd backend
uv sync --extra dev --extra aws
uv run alembic upgrade head
uv run ai4sbench-api
```

In a second terminal:

```bash
cd backend
uv run ai4sbench-jobs
```

Build the dashboard before starting the API when changing its source:

```bash
cd dashboard-frontend/frontend
npm ci
npm run build
```

The generated assets are served from `backend/control_panel/static/`.

## Containers

Run from the project root so Docker can access both backend and dashboard build
contexts:

```bash
docker compose -f backend/compose.yaml up --build
```

The public task repository is configured through `TBCP_GITHUB_REPOSITORY` and
is intentionally external to this service's private data directory.
