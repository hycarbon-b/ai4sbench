# ai4sbench

ai4sbench is the contribution and execution control plane for scientific
Harbor benchmarks. It connects the public Website and GitHub Discussions to a
private operator Dashboard, SQLite state, immutable task revisions, background
jobs, and EC2 workers.

## Repository layout

```text
ai4sbench/
|-- backend/                     FastAPI API, SQLite, migrations and EC2 lifecycle
|-- dashboard-frontend/          React operator and contribution Dashboard
|-- ai4s-bench-website/          Public Website submodule
|-- benchmark-repository/        Public ai4s-benchmark task submodule
`-- reference/                   Upstream reference implementations
```

- [`backend/`](backend/README.md) owns GitHub OAuth, proposal and review APIs,
  Discussion synchronization, SQLite jobs, database snapshots, migrations, and
  worker orchestration.
- [`dashboard-frontend/frontend/`](dashboard-frontend/frontend/README.md) is
  compiled into `backend/control_panel/static/` and served at `/`.
- `ai4s-bench-website/` is developed in its own repository. A reviewed static
  build is committed under `backend/control_panel/website_dist/` and served at
  `/website`.
- `benchmark-repository/` points to
  `https://github.com/AI4S-Bench/ai4s-benchmark.git` and contains public task
  definitions rather than private control-plane state.

## Clone and initialize

Prerequisites are Git, Python 3.12 or newer, `uv`, Node.js, and npm.

```bash
git clone --recurse-submodules <repository-url>
cd ai4sbench
git submodule update --init --recursive
```

Develop a submodule on a branch inside that submodule. Commit and push the
submodule change first, then commit the updated submodule pointer in this
repository. Do not make long-lived work on a detached submodule commit.

## Run locally

Create `backend/.env` from the documented variable names in
`backend/.env.example`. Use development values and a local SQLite path; never
copy production credentials into a commit.

```bash
cd backend
uv sync --extra dev --extra aws
uv run alembic upgrade head
uv run ai4sbench-api
```

Run the durable job worker in a second terminal:

```bash
cd backend
uv run ai4sbench-jobs
```

Build the Dashboard after changing React source:

```bash
cd dashboard-frontend/frontend
npm ci
npm run check
npm run build
```

The production build writes directly to `backend/control_panel/static/`.
Commit the generated `index.html` and hashed assets together with the source
change. The API then serves:

- Dashboard: `http://127.0.0.1:8080/`
- Website snapshot: `http://127.0.0.1:8080/website`
- Swagger UI: `http://127.0.0.1:8080/docs`
- Readiness: `http://127.0.0.1:8080/health/ready`

Docker Compose is also available from the repository root:

```bash
docker compose -f backend/compose.yaml up --build
```

## Verify a change

Run backend and frontend verification before committing a release:

```bash
cd backend
uv run pytest
uv run ruff check control_panel tests
```

```bash
cd dashboard-frontend/frontend
npm run check
npm run build
```

If a migration is added, run Ruff on that new migration file and run
`uv run alembic upgrade head` against a disposable SQLite database as well as
the normal test suite.

## Continuous integration

Run the repository-wide checks from the repository root:

```powershell
python scripts/ci.py
```

The command works from Windows and Linux. It installs locked backend and
frontend dependencies, runs backend linting and tests, type-checks the
Dashboard, builds the Dashboard, and confirms committed static assets are
current. GitHub Actions only provides the Python, uv, and Node runtimes then
invokes this same script; it contains no separate CI logic.

## Proposal lifecycle

1. A signed-in contributor previews or submits a Proposal.
2. The backend validates the request with the same contract used when importing
   a GitHub Discussion.
3. The Proposal is rendered as a Discussion in the configured `Task Proposals`
   category and tracked locally.
4. Authorized reviewers publish structured review replies. Full Sync reparses
   the Discussion and review into the local database.
5. The Website task board reads only `GET /api/v1/public/proposals`.

The signed-in Website author can load an active Proposal with
`GET /api/v1/proposals/{proposal_id}` and fully replace it with
`PUT /api/v1/proposals/{proposal_id}`. The update uses the current
`ProposalSubmission` contract and the original author's GitHub OAuth token to
edit the Discussion before committing the corresponding local fields, so a
GitHub failure cannot leave the database showing content that was not published.

Dashboard deletion is logical. `DELETE /api/v1/proposals/{proposal_id}` sets a
`deleted_at` tombstone, hides the Proposal from Dashboard and Website lists,
and preserves its GitHub Discussion and linked task revisions. Full Sync
recognizes the tombstone and will not import that Discussion again.

## Production deployment

Production is a Git-based systemd deployment. Application files are not copied
directly with `scp`: push the reviewed branch, create an SQLite snapshot, pull
the branch on EC2, run Alembic, restart the API and job services, and verify the
public routes. See [`EC2_RUNBOOK.md`](EC2_RUNBOOK.md) for the current host,
commands, rollback boundaries, and verification checklist.

Secrets remain in ignored local `backend/.env` files or the protected production
environment file. They must never be added to the repository, generated static
assets, logs, issues, or pull requests.
