# ai4sbench backend

This directory contains the FastAPI backend for community workflows and
benchmark execution. The application, EC2 control panel, community workflows,
background jobs, and persistence have separate modules under `src/`.

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
Environment values in the ignored project `.env` are passed to the API and
runner containers through Compose `env_file`. Keep the real `.env` local to the
deployment checkout; `.env.example` contains placeholders for project setup.

Useful routes after startup:

| Route | Purpose |
| --- | --- |
| `/docs/ec2/v1` | EC2 control panel v1 Swagger UI |
| `/openapi/ec2/v1.json` | EC2 control panel v1 OpenAPI contract |
| `/docs/ec2/v2` | Harbor EC2 v2 Swagger UI |
| `/openapi/ec2/v2.json` | Harbor EC2 v2 OpenAPI contract |
| `/docs/community/v1` | Community v1 Swagger UI |
| `/openapi/community/v1.json` | Community v1 OpenAPI contract |
| `/docs` and `/openapi.json` | Combined legacy documentation and schema |
| `/health/live` | Process liveness |
| `/health/ready` | Database readiness |
| `/` | Committed Dashboard build |
| `/website` | Committed Website build |

## Source layout

```text
src/
├── main.py                  FastAPI composition and static mounts
├── api_docs.py              Independent Swagger/OpenAPI contracts
├── control_panel/           EC2, task plans/runs, worker and execution service
│   ├── api/v1/routes.py     Version 1 control panel endpoints
│   ├── api/v2/routes.py     Harbor EC2 v2 endpoints
│   ├── v2/service.py        Harbor CLI EC2 run service
│   ├── v2/runner.py         Separate v2 queue runner
│   ├── schemas.py           Execution request and response contracts
│   └── services.py          Execution business logic
├── community/               Proposals, reviewers, AI reviews and deliveries
│   ├── routes.py            Public and signed-in community endpoints
│   ├── admin_routes.py      Reviewer and delivery administration
│   ├── schemas.py           Community request and response contracts
│   └── deliveries.py        Outbound delivery logic
├── core/                    Configuration and identity/authentication
├── db/                      Shared SQLite session and ORM models
└── jobs/                    Background orchestration across domains
```

The control panel and community each own their schemas and services. The
database layer is shared because both domains use the same SQLite database;
`jobs/runner.py` orchestrates v1 and community work. Harbor v2 has its own
service, queue runner, and Swagger/OpenAPI pair. Existing `/api/v1` URLs are
unchanged. The combined `/openapi.json` remains for existing Website consumers.

## Dashboard and Website assets

Build Dashboard source before starting the API when React code changes:

```bash
cd dashboard-frontend/frontend
npm ci
npm run check
npm run build
```

Vite writes the build directly to `backend/src/static/`. Commit the
source, `static/index.html`, and hashed assets in the same parent-repository
commit.

The public Website is maintained in the `ai4s-bench-website` submodule. Prepare
and push its own branch first, update the parent submodule pointer, then replace
`src/website_dist/` with the reviewed Website build. Commit the
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

## Reviewer applications and outbound deliveries

The Website reviewer form submits to `POST /api/v1/reviewers`.
Administrators manage records through:

- `GET /api/v1/reviewer-applications`
- `PATCH /api/v1/reviewer-applications/{application_id}`

An approved application grants review access only when it has a GitHub
username. `TBCP_DISCORD_WEBHOOK_URL` enables queued Proposal and review
notifications. The generic outbound queue also supports SMTP through
FastAPI-Mail, but it is disabled by default and no current business flow queues
email. Configure SMTP only when a later internal flow calls the queue service.
GitHub Discussion delivery is a reserved type; existing Discussion creation and
review replies remain synchronous. The job runner sends enabled deliveries,
while administrators inspect or retry them through:

- `GET /api/v1/deliveries`
- `POST /api/v1/deliveries/{delivery_id}/resend`

After a Proposal-created Discord delivery succeeds, the backend records its
Discord permalink in `proposals.discord_message_url`. The field is returned by
Proposal list, edit-detail, and public task-board APIs. Review notifications do
not overwrite the Proposal link.

`TBCP_WEBSITE_PUBLIC_BASE_URL` controls task-detail links in those messages.

## Database and migrations

The running API and job runner use one SQLAlchemy `AsyncEngine` and one
`async_sessionmaker` for both application data and authentication. SQLite
foreign keys, WAL, and the busy timeout are therefore identical for every
runtime database connection. Blocking provider SDK calls run in worker threads;
HTTP integrations use async clients. Alembic remains a synchronous command-line
tool because it runs before the services start.

Schema changes require an Alembic migration:

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
uv run ruff check src tests
```

Run Ruff explicitly on every new migration file as part of its review.

Run the complete local stack from the repository root with:

```bash
docker compose -f backend/compose.yaml up --build
```

## Harbor EC2 v2

The v2 integration lives in `src/control_panel/v2`. It stores runs in
`harbor_v2_runs` and starts Harbor 0.20.0 through its supported CLI with
`harbor run -e ec2 --ek ...`. Harbor controls EC2 launch, SSH, Docker Compose,
and instance termination. The v1 EC2 runner and community services remain
separate. API requests enqueue work; the dedicated v2 process executes it.

V2 reuses v1's `TBCP_EC2_AMI_ID`, `TBCP_EC2_SUBNET_ID`,
`TBCP_EC2_SECURITY_GROUP_IDS`, instance type, root volume, and AWS region.
Put the standard AWS credentials (`AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY`) and the additional `TBCP_HARBOR_V2_*` settings in
`backend/.env`. Compose passes this project env file directly to the v2 runner;
its Harbor-launched EC2 instances are ephemeral, so this setup does not need a
separate credentials service. Set `TBCP_HARBOR_V2_ENABLED=true` to enable
enqueueing. The runner requires Git, OpenSSH, a matching EC2 key pair/private
key file, and SSH access to the launched instance. Use an AMI with Docker and
Compose installed, or set `TBCP_HARBOR_V2_BOOTSTRAP_DOCKER=true`. Apply
migrations before starting:

```bash
cd backend
uv sync --extra aws --extra harbor-ec2 --extra dev
uv run alembic upgrade head
uv run ai4sbench-v2-jobs
```

For containers, set `TBCP_HARBOR_V2_SSH_KEY_PATH` in `backend/.env` to
`/run/secrets/harbor-ec2-key` and `HARBOR_V2_SSH_KEY_HOST_PATH` to the key
file's path on the host. The Compose example mounts that file into the runner.
Then start the optional runner with:

```bash
docker compose -f backend/compose.yaml -f backend/compose.harbor-v2.example.yaml --profile harbor-v2 up --build
```

The override mounts the key into the runner and keeps job output on the
existing data volume. The API checks the v2 configuration before enqueuing;
the runner checks that its CLI, SSH client, and private key are available at
startup. A task
revision must point to an immutable GitHub commit and Harbor-compatible
`task.toml`. The runner checks out that commit locally before calling Harbor.

Admin endpoints: `POST /api/v2/control-panel/runs`,
`GET /api/v2/control-panel/runs`, `GET /api/v2/control-panel/runs/{run_id}`,
and `GET /api/v2/control-panel/runs/{run_id}/log`. The v2 contract has its own
Swagger page at `/docs/ec2/v2`; v1 stays at `/docs/ec2/v1`.

The runner marks a run `interrupted` if its lease expires and does not
automatically relaunch it, because an EC2 instance may still be active.
Inspect EC2 instances tagged `ai4sbench:v2-run=<run_id>` before retrying.

Run `uv run --extra dev --extra harbor-ec2 pytest tests/test_harbor_v2.py`.
These tests cover API-to-runner execution with a fake CLI process and check
the generated command against the official CLI using a Harbor-generated task
and `harbor run --print-config`. The CLI smoke test skips if Harbor is absent.

On 2026-10-05, the official Harbor 0.20.0 CLI completed a local-to-EC2 smoke
task using the v1 worker AMI, subnet, security group, and `t3.micro`: one trial
completed, no trial errors, CLI exit code 0, and the instance was terminated.
The Windows runner sets `PYTHONIOENCODING=utf-8` because Harbor's final Rich
output otherwise fails under the GBK console encoding after a successful job.

The public benchmark repository is selected through
`TBCP_GITHUB_REPOSITORY`. It remains external to the control plane's private
data directory.

## AI proposal reviews

See [AI review setup, API and recovery](docs/ai-proposal-reviews.md) for the service key, bot token,
Discord destination, benchmark Actions settings, migration and smoke-test sequence.
Deploy the backend before enabling the benchmark workflow.
