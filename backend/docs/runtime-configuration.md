# Runtime and Configuration

## Local development

Use Python 3.12 or newer and `uv` from the `backend/` directory:

```bash
uv sync --extra dev --extra aws
uv run alembic upgrade head
uv run ai4sbench-api
```

Run background jobs in a second terminal:

```bash
uv run ai4sbench-jobs
```

For local development, `.env.example` is the configuration reference. Use
`TBCP_ENVIRONMENT=development`, a local SQLite database, and
`TBCP_EXECUTION_MODE=fake` unless you intend to exercise EC2. Keep real secrets
in the ignored local `.env`; never commit them.

## Processes

| Entry point | Purpose |
| --- | --- |
| `ai4sbench-api` | FastAPI HTTP application |
| `ai4sbench-jobs` | Community and v1 background job orchestration |
| `ai4sbench-worker` | Control-panel worker |
| `ai4sbench-v2-jobs` | Harbor v2 queue runner |

The scripts are declared in `pyproject.toml`. Inspect the corresponding
`src/` module for exact polling, retry, and shutdown behavior.

## Configuration ownership

`src/core/config.py` defines settings and defaults; `.env.example` lists the
operator-facing environment variable names. Deployment Compose files pass
environment through `env_file`. Use the AI review page for its service-specific
secrets and workflow settings.

`data/`, `.env`, credentials, OAuth tokens, and local database files are
operator state. They must stay out of public submodules and commits.

## Docs development

MkDocs uses the same Read the Docs theme as the contributor documentation:

```bash
uv run mkdocs serve
uv run mkdocs build --strict
```

The config is `mkdocs.yml`; pages are in `docs/`. Draft material under
`docs/draft/` is excluded from the published site.
