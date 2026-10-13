# Architecture and Ownership

## Application composition

`src/main.py` builds the FastAPI application, mounts the domain routers, serves
the committed Dashboard and Website assets, and configures lifecycle behavior.
`src/api_docs.py` publishes separate OpenAPI contracts so each public API
surface can evolve without relying on the combined legacy schema.

The backend has three main areas:

| Area | Owns | Key code |
| --- | --- | --- |
| Community | Proposals, reviewer applications, reviews, GitHub synchronization, and outbound deliveries | `src/community/` |
| Control panel | Benchmark plans and runs, execution backends, task data, and worker-facing APIs | `src/control_panel/` |
| Shared runtime | Configuration, authentication, database sessions/models, and cross-domain jobs | `src/core/`, `src/db/`, `src/jobs/` |

Community and control-panel schemas and services belong to their domains. The
database is shared. `src/jobs/runner.py` orchestrates queued community and v1
work; Harbor v2 has its own runner under `src/control_panel/v2/`.

## API contracts

The current separately versioned API surfaces are:

| Surface | Swagger UI | OpenAPI JSON |
| --- | --- | --- |
| Community v1 | `/docs/community/v1` | `/openapi/community/v1.json` |
| EC2 control panel v1 | `/docs/ec2/v1` | `/openapi/ec2/v1.json` |
| Harbor EC2 v2 | `/docs/ec2/v2` | `/openapi/ec2/v2.json` |

`/docs` and `/openapi.json` remain the combined legacy surface for existing
consumers. When changing an API, update the owning schema/route and its
contract-level documentation together.

## Persistence and migrations

ORM models and database session setup live under `src/db/`. Alembic settings
are in `alembic.ini`; versioned schema changes live in `migrations/versions/`.
Do not treat a model-only change as a complete schema change: add and review a
migration, and keep upgrade behavior aligned with supported existing databases.

## Frontend assets

Dashboard source is in `dashboard-frontend/frontend/`; its Vite build is
committed to `src/static/`. The public Website source is the
`ai4s-bench-website/` submodule, while its deployable snapshot is
`src/website_dist/`. Follow the build and submodule workflow in
`backend/README.md` when updating either asset set.

## Change checklist

For a behavior change, identify the owning domain, update the route/schema or
service, review persistence and migrations if data changes, and update this
wiki when the interface or operational behavior changes. Existing tests under
`tests/` document many integration contracts; add or adjust coverage with the
implementation change.
