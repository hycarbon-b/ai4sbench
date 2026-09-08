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

The public Website snapshot is committed under `control_panel/website_dist` and
served at `/website`. Refresh that directory manually from the Website
submodule whenever a new static release is prepared.

The public task repository is configured through `TBCP_GITHUB_REPOSITORY` and
is intentionally external to this service's private data directory.

The Website task board reads `GET /api/v1/public/proposals`. Each item combines
the stored proposal, its latest valid structured review reply, and its latest
linked task revision. Review comments are accepted only from administrators or
GitHub logins configured by `TBCP_REVIEWER_GITHUB_LOGINS`. Reviewers can render
the canonical reply with `POST /api/v1/proposals/reviews/preview` and publish it
beneath a Discussion with `POST /api/v1/proposals/{proposal_id}/reviews`.

Set `TBCP_DISCORD_WEBHOOK_URL` to enqueue Discord notifications for newly
published proposals and reviews. `TBCP_WEBSITE_PUBLIC_BASE_URL` controls the
task-detail link in those messages. The existing `ai4sbench-jobs` process sends
the queued deliveries. Administrators can inspect the full destination, JSON
payload, response and retry state at `GET /api/v1/webhook-deliveries`, or queue a
delivery again with `POST /api/v1/webhook-deliveries/{delivery_id}/resend`.
