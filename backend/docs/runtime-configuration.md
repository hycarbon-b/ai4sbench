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
operator-facing environment variable names. Local Compose setups pass
environment through `env_file`. Use the AI review page for its service-specific
secrets and workflow settings.

`data/`, `.env`, credentials, OAuth tokens, and local database files are
operator state. They must stay out of public submodules and commits.

## Production deployment

Production EC2 runs the API and job runners under systemd. After the parent PR
is merged into `main` and CI passes, connect to the server and run its deployment
entry point:

```bash
ssh ec2-user@3.237.65.103
sudo bash /opt/ai4sbench/deploy/deploy.sh <main-commit-sha>
```

The script lives in the parent repository's `deploy/` directory and runs on
EC2. Omitting the SHA selects the latest fetched `origin/main`; the script
freezes the exact commit before changing the checkout. It checks checkout
cleanliness and main ancestry, creates and validates an online SQLite snapshot,
fast-forwards Git and pinned submodules, syncs changed locked dependencies,
applies Alembic migrations, and restarts the API and job runners. The Harbor v2
runner is included when enabled or active.

`deploy/verify.py` then checks readiness, proposal/review API routes and published
Dashboard/Website files through loopback and the public HTTPS URL. Public HTML
checks allow Cloudflare's analytics and email protection rewriting.

Production commands load `/etc/ai4sbench/control-panel.env` as the service
account. The database is `/var/lib/ai4sbench/control-panel.sqlite3`; the deployment
log records the snapshot path and target commit under
`/var/lib/ai4sbench/deployments/`. Failure stops the sequence and reports its
stage. Inspect the log and snapshot before recovery; the script does not
automatically restore the database or downgrade migrations.

See the [EC2 runbook](https://github.com/hycarbon-b/ai4sbench/blob/main/EC2_RUNBOOK.md#run-the-deployment-script-on-ec2)
for the first-release bootstrap command, prerequisites and recovery details.
Update the runbook and `deploy/` together when the release procedure changes.

## Docs development

MkDocs uses the same Read the Docs theme as the contributor documentation:

```bash
uv run mkdocs serve
uv run mkdocs build --strict
```

The config is `mkdocs.yml`; pages are in `docs/`. Draft material under
`docs/draft/` is excluded from the published site.
