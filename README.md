# AI4S-Bench Control Panel

[![CI](https://github.com/hycarbon-b/ai4sbench/actions/workflows/ci.yml/badge.svg)](https://github.com/hycarbon-b/ai4sbench/actions/workflows/ci.yml)

The control panel and Website backend for the open [AI4S-Bench](https://ai4sbench.org/) project, an interdisciplinary benchmark for AI agents in science. This repository runs the contribution workflow, operator Dashboard, APIs, persistence, and benchmark execution orchestration. The public Website and benchmark tasks are maintained in separate repositories and included here as submodules.

> **Coming later:** We plan to open-source a separate, self-hosted task runner platform similar in spirit to Harbor Hub. It is not part of this repository yet. Stay tuned.

## Current capabilities

- Submit and review scientific task proposals through the Website and GitHub Discussions.
- Manage task revisions, execution jobs, and EC2 workers from the operator Dashboard.
- Send Discord and SMTP notifications through a durable outbound delivery queue.
- Serve the Dashboard and a reviewed Website snapshot from the FastAPI backend.

## What this repository contains

| Area | Responsibility |
| --- | --- |
| [`backend/`](backend/README.md) | FastAPI APIs, GitHub OAuth and Discussion synchronization, proposals and reviews, SQLite migrations, outbound deliveries, jobs, and EC2 execution orchestration. |
| [`dashboard-frontend/frontend/`](dashboard-frontend/frontend/README.md) | React operator and contribution Dashboard. Its build is served by the backend at `/`. |
| [`ai4s-bench-website/`](https://github.com/hycarbon-b/ai4s-bench-website) | Public Website submodule. A reviewed static snapshot is served by the backend at `/website`. |
| [`benchmark-repository/`](https://github.com/AI4S-Bench/ai4s-benchmark) | Public benchmark task definitions, included as a submodule. |
| [`reference/`](reference/) | Upstream reference material; not the application runtime. |

The Website submits proposals through the backend, which publishes and synchronizes the corresponding GitHub Discussions. Reviewers publish structured reviews; the Dashboard manages operations and task execution. Background runners handle durable jobs and outbound notifications. The backend keeps task revisions immutable so runs remain tied to the version they used.

## Quick start

You need Git, Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js, and npm. Clone with submodules:

```bash
git clone --recurse-submodules https://github.com/hycarbon-b/ai4sbench.git
cd ai4sbench
```

Create `backend/.env` from [`backend/.env.example`](backend/.env.example). For local development, change the example's production settings to `TBCP_ENVIRONMENT=development`, `TBCP_EXECUTION_MODE=fake`, a local SQLite URL, and local host values. Keep SMTP disabled unless you intentionally test mail delivery. GitHub OAuth, Discussion publishing, and real EC2 runs require their own credentials and configuration; see the [backend guide](backend/README.md).

Start the API:

```bash
cd backend
uv sync --locked --extra dev --extra aws
uv run alembic upgrade head
uv run ai4sbench-api
```

Start the background job runner in a second terminal from `backend/`:

```bash
uv run ai4sbench-jobs
```

The local API serves the Dashboard at `http://127.0.0.1:8080/`, the Website snapshot at `/website`, API docs at `/docs`, and readiness at `/health/ready`. You can also start the application with `docker compose -f backend/compose.yaml up --build` from the repository root.

When editing the Dashboard, run `npm ci`, `npm run check`, and `npm run build` in `dashboard-frontend/frontend/`. The build writes to `backend/src/static/`; commit the generated assets together with the source change. Website changes belong in its submodule first, followed by an updated submodule pointer and reviewed `backend/src/website_dist/` snapshot.

## Development and CI

Run the same repository-wide checks locally and in GitHub Actions:

```bash
python scripts/ci.py
```

The script installs locked dependencies, lints and tests the backend, type-checks and builds the Dashboard, and verifies that committed static assets are current. It runs on both Windows and Linux. GitHub Actions only provisions the runtimes and calls this script.

For a change limited to one component, see the [backend](backend/README.md) or [Dashboard](dashboard-frontend/frontend/README.md) guide. Schema changes require an Alembic migration. Develop submodule changes on a branch inside the submodule, push that commit first, and only then update the parent repository's submodule pointer.

## Contributors

This list follows GitHub's [public repository contributors API](https://docs.github.com/en/rest/repos/repos#list-repository-contributors), which credits commits rather than all pull-request activity. It is refreshed weekly and can be refreshed manually through the [Update contributors workflow](https://github.com/hycarbon-b/ai4sbench/actions/workflows/update-contributors.yml). Contributions to the Website and benchmark task repositories are tracked in those repositories.

<!-- contributors:start -->
<table>
  <tr>
    <td align="center"><a href="https://github.com/hycarbon-b"><img src="https://github.com/hycarbon-b.png?size=80" width="64" height="64" alt="@hycarbon-b" /><br /><sub>@hycarbon-b</sub></a></td>
  </tr>
</table>
<!-- contributors:end -->

Want to contribute? Open an issue or pull request with the problem, proposed change, and relevant tests. Run `python scripts/ci.py` before submitting when possible.

## Deployment and security

Production uses a Git-based systemd deployment. Follow the [EC2 runbook](EC2_RUNBOOK.md) for database snapshots, migrations, service restarts, verification, and rollback. Do not copy application files directly to the server as a deployment shortcut.

Keep `.env` files, OAuth and SMTP credentials, tokens, and SQLite operator data out of commits, generated assets, logs, issues, and pull requests. The checked-in [environment example](backend/.env.example) documents variable names without production values.
