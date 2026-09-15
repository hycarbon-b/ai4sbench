# ai4sbench control plane

This directory contains the private FastAPI control plane. It owns GitHub
OAuth, Proposal-to-Discussion publishing and synchronization, structured
reviews, SQLite state, database-backed jobs, EC2 lifecycle management, worker
callbacks, migrations, and operator APIs.

`data/`, `.env`, credentials, OAuth tokens, and SQLite files are operator state.
They are ignored by Git and must not be copied into either public submodule.

## Development setup

Use Python 3.12 or newer and `uv`:

```bash
cd backend
uv sync --extra dev --extra aws
uv run alembic upgrade head
uv run ai4sbench-api
```

Start the background job runner separately:

```bash
cd backend
uv run ai4sbench-jobs
```

Configuration is loaded from `backend/.env` with the variable names documented
in `.env.example`. For local work, use `TBCP_ENVIRONMENT=development`, a local
SQLite URL, and `TBCP_EXECUTION_MODE=fake` unless an EC2 test is intentional.
Production secrets do not belong in a development commit or terminal capture.

Useful routes after startup:

| Route | Purpose |
| --- | --- |
| `/docs` | Swagger UI with request and response schemas |
| `/health/live` | Process liveness |
| `/health/ready` | Database readiness |
| `/` | Committed Dashboard build |
| `/website` | Committed Website build |

## Dashboard and Website assets

Build Dashboard source before starting the API when React code changes:

```bash
cd dashboard-frontend/frontend
npm ci
npm run check
npm run build
```

Vite writes the build directly to `backend/control_panel/static/`. Commit the
source, `static/index.html`, and hashed assets in the same parent-repository
commit.

The public Website is maintained in the `ai4s-bench-website` submodule. Prepare
and push its own branch first, update the parent submodule pointer, then replace
`control_panel/website_dist/` with the reviewed Website build. Commit the
snapshot instead of copying application files directly to EC2.

## Proposal, review, and synchronization APIs

| Method and route | Access | Behavior |
| --- | --- | --- |
| `POST /api/v1/proposals/preview` | Public or signed in | Validate and render without writing |
| `POST /api/v1/proposals` | Signed in | Publish a Discussion and persist the Proposal |
| `GET /api/v1/proposals` | Public | List active locally tracked Proposals |
| `GET /api/v1/proposals/{proposal_id}` | Original author | Load all stored form fields for editing |
| `PUT /api/v1/proposals/{proposal_id}` | Original author | Replace the Proposal and its existing Discussion |
| `DELETE /api/v1/proposals/{proposal_id}` | Administrator | Set the local deletion tombstone |
| `POST /api/v1/proposals/sync-discussions` | Administrator | Import or update active Discussion records |
| `GET /api/v1/public/proposals` | Public | Return valid active task-board records |
| `POST /api/v1/proposals/reviews/preview` | Reviewer | Render a structured review reply |
| `POST /api/v1/proposals/{proposal_id}/reviews` | Reviewer | Publish and persist a structured review |

Create, preview, and Discussion sync all pass through the same
`ProposalSubmission` validation and normalization entry point. Discussion sync
parses only the configured repository's `Task Proposals` category and upserts by
GitHub Discussion node ID, falling back to its URL.

Proposal editing is a full replacement using that same current contract. The
client first reloads the stored form fields for the original author, and the API
uses that author's GitHub OAuth token to update the original Discussion title
and canonical Markdown body before committing the normalized fields locally.
If GitHub rejects the mutation, the local Proposal remains unchanged. Logically
deleted Proposals and records without a Discussion node identity cannot be
edited.

Proposal deletion is deliberately local and logical:

- `deleted_at` is set; the Proposal row is retained.
- Dashboard and Website list queries exclude the row.
- Review and pull-request-guide routes treat it as not found.
- Linked task revisions, plans, runs, jobs, and webhook history remain intact.
- The GitHub Discussion is not deleted.
- Full Sync sees the tombstone and skips the Discussion, so it cannot recreate
  or update the deleted Proposal.

The Website task board combines each active valid Proposal with its latest valid
structured review reply and latest linked task revision. Review comments are
accepted only from administrators, approved reviewer applicants, or GitHub
logins configured in `TBCP_REVIEWER_GITHUB_LOGINS`.

## Reviewer applications and Discord deliveries

The Website reviewer form submits to `POST /api/v1/reviewers`.
Administrators manage records through:

- `GET /api/v1/reviewer-applications`
- `PATCH /api/v1/reviewer-applications/{application_id}`

An approved application grants review access only when it has a GitHub
username. `TBCP_DISCORD_WEBHOOK_URL` enables queued Proposal and review
notifications. The job runner sends them, while administrators inspect or retry
them through:

- `GET /api/v1/webhook-deliveries`
- `POST /api/v1/webhook-deliveries/{delivery_id}/resend`

`TBCP_WEBSITE_PUBLIC_BASE_URL` controls task-detail links in those messages.

## Database and migrations

Production uses SQLite with foreign keys, WAL, and a busy timeout enabled by the
application. Schema changes require an Alembic migration:

```bash
cd backend
uv run alembic upgrade head
uv run alembic current
```

Create a consistent backup from the Dashboard's **Database snapshots** page or
`POST /api/v1/database-snapshots` before migrations, bulk sync, or other data
changes. Do not treat a raw copy of the live WAL database file as a consistent
backup.

## Tests and containers

```bash
cd backend
uv run pytest
uv run ruff check control_panel tests
```

Run Ruff explicitly on every new migration file as part of its review.

Run the complete local stack from the repository root with:

```bash
docker compose -f backend/compose.yaml up --build
```

The public benchmark repository is selected through
`TBCP_GITHUB_REPOSITORY`. It remains external to the control plane's private
data directory.
