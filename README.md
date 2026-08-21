# ai4sbench control panel

FastAPI control plane for running immutable Harbor benchmark tasks on short-lived
EC2 workers. The service uses SQLAlchemy with SQLite WAL and a durable database
job queue; it does not require Redis, Celery, Dramatiq, or PostgreSQL.

## Architecture

- `ai4sbench-api`: GitHub-authenticated contribution portal, administration API and worker callback API.
- `ai4sbench-jobs`: separate process that leases durable SQLite jobs, calls EC2,
  retries transient failures, reconciles deadlines, and terminates workers.
- `ai4sbench-worker`: AMI entry point that claims a one-time job, checks out an
  exact Git commit, runs Harbor, posts events/results, and exits.
- SQLite: WAL mode, foreign keys, 30-second busy timeout, migrations via Alembic.

SQLite is suitable for one control node with API and job-runner processes on the
same host and local disk. Do not put the database on NFS/EFS or run replicas on
different hosts. A future multi-control-node deployment requires a server
database, but no such dependency is needed for the current design.

## Development

```powershell
$env:UV_CACHE_DIR=(Resolve-Path .uv-cache).Path
uv sync --extra dev --extra aws
uv run alembic upgrade head
uv run uvicorn control_panel.main:app --host 127.0.0.1 --port 8080
```

In a second terminal:

```powershell
$env:UV_CACHE_DIR=(Resolve-Path .uv-cache).Path
uv run ai4sbench-jobs
```

Copy `.env.example` to `.env`, register a GitHub OAuth App, and use independent
random values for the job-token and auth-JWT secrets. A GitHub login is a
`member` by default; only logins or emails explicitly listed in the environment
receive the `admin` role. Production configuration deliberately refuses fake EC2
mode, automatic schema creation, missing GitHub OAuth credentials, or short secrets.

## Identity and contribution workflow

The landing page starts GitHub OAuth through FastAPI Users. The application
stores the GitHub-linked user and OAuth account in SQLite, uses a secure HTTP-only
cookie for the browser session, and has exactly two human roles:

- `member` can create a task proposal. The proposal is posted as a GitHub
  Discussion using that member's OAuth token.
- `admin` can additionally manage non-secret cloud allocation profiles.

The repository configured in `TBCP_GITHUB_REPOSITORY` contains the companion
PR template and GitHub Actions check. A contribution PR must link the proposal
Discussion and include the required Terminal-Bench-Science-style task layout.
Worker endpoints remain separate: they use a one-time bootstrap token followed
by a run-scoped session token; neither token is stored in plaintext.

## Task repository synchronization

`POST /api/v1/task-revisions/sync-repository` resolves the requested branch tip,
records its exact commit hash, scans every `tasks/**/task.toml`, and imports the
complete task catalog in one SQLite transaction. Workers later check out that
immutable commit before running the selected task.

The task library intentionally does not infer task quality from repository-wide
CI. It records only the immutable repository commit and the task's declared
resource requirements. `TBCP_GIT_HTTPS_PROXY` optionally routes Git fetches
through a local HTTP proxy; it is not returned by the settings API.

## Tests and quality gates

```powershell
uv run pytest
uv run ruff check control_panel tests bench
uv run ruff format --check control_panel tests bench
```

The integration suite exercises administrator authentication, immutable task
revision intake, optimistic approval, idempotent run creation, atomic concurrency
limits, database-job leasing, EC2 launch idempotency, one-time worker claim,
event ingestion, result completion, and automatic instance termination.

## Deployment

`compose.yaml` runs a one-shot migration, the FastAPI service, and the database
job runner against one named local volume. The API port is bound to loopback by
default; terminate TLS at a reverse proxy. Use the AWS policies under
`infra/aws/` and pre-create a zero-ingress security group.  For Free-plan
testing, select the official public Amazon Linux 2023 AMI: cloud-init installs
Docker, the SHA-256-verified official prebuilt Docker Compose plugin, Git, and the
prebuilt `harbor==0.20.0` wheel (with builds and caches disabled),
and a stdlib-only worker callback runner.  Set `TBCP_EC2_BOOTSTRAP_MODE=baked_ami`
only once a worker image containing Docker, Git, Harbor, cloudflared, and this
package at `/opt/ai4sbench` has been published.

### Temporary AWS Free-plan E2E profile

For the initial, non-production end-to-end test, set `TBCP_EXECUTION_MODE=ec2`
and supply `AWS_ACCESS_KEY_ID` plus `AWS_SECRET_ACCESS_KEY` in the local `.env`.
The boto3 provider consumes those standard AWS variables directly.  Leave
`TBCP_EC2_INSTANCE_PROFILE_ARN` empty: the worker only needs outbound HTTPS to
claim and complete a run, so it has no AWS API dependency.

The defaults target `us-east-1` and use the current Free-plan eligible instance
families.  This mode deliberately does not enable IAM Identity Center or AWS
Organizations.  It is only a bridge to validate the full control-plane lifecycle;
replace the account credentials with a least-privilege role before production.
The selected `amr-poisson-optimize` reference task declares four CPUs, so a
Free-plan small worker validates launch, Docker, Harbor, callbacks, and cleanup
only—it is not a valid performance benchmark.

The pinned prebuilt Harbor release, benchmark matrix, and task revision are
documented under `bench/`. The rendered architecture, cloud resource estimate,
APIs, and current E2E status are in
`plan/terminal-bench-control-panel-proposal.html`.
