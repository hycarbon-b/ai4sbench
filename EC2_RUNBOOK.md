# AI4S-Bench Dashboard EC2 runbook

Last verified: 2026-09-13 (Asia/Shanghai)

This document records the current production control-plane deployment. It is
safe to commit because it intentionally excludes private keys, GitHub OAuth
credentials, JWT secrets, job-token secrets, and the full environment file.

## Service at a glance

| Item | Current value |
| --- | --- |
| Public Dashboard | `https://dashboard.ai4sbench.org` |
| Readiness endpoint | `https://dashboard.ai4sbench.org/health/ready` |
| AWS region | `us-east-1` |
| EC2 instance ID | `i-0ad6edfa737560f25` |
| Instance type | `t3.micro` |
| Public IPv4 / SSH host | `3.237.65.103` |
| Private hostname | `ip-172-31-4-109.ec2.internal` |
| OS | Amazon Linux 2023, Linux `6.18.41-94.142.amzn2023.x86_64` |
| Application listener | `0.0.0.0:8080` (Uvicorn) |
| Service account | `ai4sbench` |
| Git checkout | `/opt/ai4sbench` |
| Application directory | `/opt/ai4sbench/backend` |
| Python runtime | `/opt/ai4sbench/backend/.venv/bin/python` |
| Production environment file | `/etc/ai4sbench/control-panel.env` |
| SQLite database | `/var/lib/ai4sbench/control-panel.sqlite3` |

The public Dashboard is reached through its HTTPS domain. Port `8080` is the
application listener on the instance; it is not the public URL to give to
browser clients.

## Connecting

The local machine that maintains this deployment has an SSH identity already
configured for the `ec2-user` account:

```powershell
ssh ec2-user@3.237.65.103
```

Use `sudo` for operational actions. Run the application or access its files as
the service identity when reproducing service behavior:

```bash
sudo -u ai4sbench /opt/ai4sbench/backend/.venv/bin/python --version
sudo -u ai4sbench test -r /var/lib/ai4sbench/control-panel.sqlite3
```

Do not copy SSH private keys, OAuth client secrets, JWT secrets, job-token
secrets, or the complete `/etc/ai4sbench/control-panel.env` into this file,
issues, pull requests, logs, or chat.

## Running services

Two systemd units run the control plane. Harbor EC2 v2 adds a third, independent
runner when enabled:

| Unit | Command | Purpose |
| --- | --- | --- |
| `ai4sbench-api.service` | `python -m uvicorn src.main:create_app --factory --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips *` | HTTP API, Dashboard static assets, GitHub OAuth, proposal submission |
| `ai4sbench-jobs.service` | `python -m src.jobs.runner` | Database-backed background job runner |
| `ai4sbench-harbor-v2-jobs.service` | `ai4sbench-v2-jobs` | Harbor CLI EC2 v2 queue runner; enabled separately |

The API and v1 runner run as `ai4sbench:ai4sbench` with working directory
`/opt/ai4sbench/backend`. The optional v2 runner uses the same account and
working directory.

### How the instance and services start

1. AWS starts the `t3.micro` instance.
2. Amazon Linux boots systemd and reaches `network-online.target`.
3. Because the API and v1 runner are `enabled` for `multi-user.target`, systemd loads
   `/etc/ai4sbench/control-panel.env` and starts `ai4sbench-api.service`.
4. The API launches Uvicorn on port `8080`.
5. `ai4sbench-jobs.service` starts after the API unit and runs the SQLite job
   runner.
6. If a process exits unexpectedly, systemd restarts it after three seconds
   (`Restart=on-failure`).

Confirm startup wiring after a reboot or service change:

```bash
sudo systemctl is-enabled ai4sbench-api.service ai4sbench-jobs.service
sudo systemctl is-active ai4sbench-api.service ai4sbench-jobs.service
sudo systemctl cat ai4sbench-api.service
sudo systemctl cat ai4sbench-jobs.service
```

Useful commands:

```bash
sudo systemctl status ai4sbench-api.service ai4sbench-jobs.service --no-pager
sudo systemctl restart ai4sbench-api.service
sudo systemctl restart ai4sbench-jobs.service
sudo journalctl -u ai4sbench-api.service -n 100 --no-pager
sudo journalctl -u ai4sbench-jobs.service -n 100 --no-pager
sudo systemctl is-active ai4sbench-api.service ai4sbench-jobs.service
```

After an API restart, verify both the local listener and readiness route:

```bash
sudo ss -ltnp | grep ':8080'
curl -fsS http://127.0.0.1:8080/health/ready
```

```powershell
Invoke-WebRequest -UseBasicParsing https://dashboard.ai4sbench.org/health/ready
```

## Current application configuration

The following non-secret settings were verified from the production environment
file. Treat the environment file itself as secret because it also contains
credentials.

| Setting | Value |
| --- | --- |
| Environment | `production` |
| Host / port | `0.0.0.0:8080` |
| Allowed hosts | `dashboard.ai4sbench.org`, `ai4sbench.org`, `www.ai4sbench.org`, `3.237.65.103`, `ec2-3-237-65-103.compute-1.amazonaws.com`, `localhost`, `127.0.0.1` |
| CORS origins | `https://ai4s-bench.github.io`, `https://ai4sbench.org`, `http://198.18.0.1:3000`, `http://localhost:8000`, `http://127.0.0.1:8000` |
| Database URL | `sqlite:////var/lib/ai4sbench/control-panel.sqlite3` |
| Auto-create schema | `false` |
| Proposal GitHub repository | `AI4S-Bench/ai4s-benchmark` |
| GitHub OAuth callback | `https://dashboard.ai4sbench.org/auth/github/callback` |

Because the proposal repository is organization-owned, the Dashboard OAuth App
must be approved for the `AI4S-Bench` organization when OAuth App access
restrictions are enabled. Without that approval, GitHub accepts sign-in and
public reads but rejects `createDiscussion` mutations made with user tokens.

### CORS verification

Browser callers must use one of the exact origins above. To test a new or
existing origin without sending application data:

The two port-8000 loopback origins support the local static Website against
the deployed control plane. They do not cover other ports or hostnames.

```powershell
Invoke-WebRequest -UseBasicParsing -Method Options `
  -Headers @{ Origin = 'https://ai4sbench.org'; 'Access-Control-Request-Method' = 'POST'; 'Access-Control-Request-Headers' = 'content-type' } `
  https://dashboard.ai4sbench.org/api/v1/proposals
```

The response must include the same `Access-Control-Allow-Origin` and
`Access-Control-Allow-Credentials: true`.

### OAuth behavior

Website sign-in must open:

```text
https://dashboard.ai4sbench.org/auth/github/start
```

That Dashboard-origin page creates the OAuth CSRF/state cookie as a first-party
cookie, then redirects to GitHub. Do not replace it with a cross-origin fetch to
`/auth/github/authorize`: browsers may isolate that cookie and GitHub's callback
will fail with `OAUTH_INVALID_STATE`.

## AWS credentials for worker provisioning

**Current verified state (2026-08-30):** the `ai4sbench` service account has
no resolved boto3 credential source. Neither `AWS_ACCESS_KEY_ID` nor
`AWS_SECRET_ACCESS_KEY` is present in its runtime environment, and boto3 does
not resolve an instance-profile credential. As a result, any feature that
creates or manages EC2 worker instances will fail until AWS access is supplied.

Do not put an AWS access key ID, secret access key, session token, or a root
account credential in this repository or in this runbook. The application can
read these standard variable *names* from its protected environment file:

```text
AWS_ACCESS_KEY_ID=<retrieve from a secret manager at deployment time>
AWS_SECRET_ACCESS_KEY=<retrieve from a secret manager at deployment time>
TBCP_AWS_REGION=us-east-1
```

The preferred production solution is an IAM instance profile attached to
`i-0ad6edfa737560f25`, with a dedicated least-privilege role for the EC2
operations the worker provider needs. This avoids long-lived keys in
`/etc/ai4sbench/control-panel.env` and lets boto3 obtain rotating credentials
from EC2 instance metadata.

If an instance profile cannot be used, place credentials only in a protected
server-side secret store or `/etc/ai4sbench/control-panel.env`, grant read
access only to `root` and the `ai4sbench` service group, then restart the two
services. Use an IAM user or assumed role with narrowly scoped permissions;
never use the AWS root account's access keys.

## Worker image (baked AMI)

Production workers boot from a pre-installed AMI rather than installing Docker,
Harbor, and the worker package at launch time. `TBCP_EC2_BOOTSTRAP_MODE` selects
between the two launch paths:

| Mode | Launch behavior |
| --- | --- |
| `baked_ami` | Production. User data only exports `TBCP_API_BASE_URL`, `TBCP_RUN_ID`, `TBCP_JOB_TOKEN` and execs the preinstalled `/opt/ai4sbench/.venv/bin/ai4sbench-worker`. No package installation happens while a run is being provisioned. |
| `amazon_linux_2023` | Development/fallback only. Installs Docker, Compose, Buildx, and Harbor from a stock Amazon Linux 2023 AMI at launch. Kept so the control plane still works without a maintained worker AMI; do not use in production. |

### Building a new worker AMI

Run from a deployment operator's machine with EC2 create permissions (not from
the control-plane service account):

```powershell
python scripts/build_worker_ami.py `
  --base-ami-id ami-<amazon-linux-2023> `
  --subnet-id subnet-05c29d44238b8de28 `
  --security-group-id sg-0f6ebf5b304d4903e `
  --repo-url https://github.com/hycarbon-b/ai4sbench.git
```

The script launches a temporary builder instance, installs pinned Docker,
Compose, Buildx, and Harbor versions, installs the worker at the current (or
`--commit-sha`-pinned) commit, runs a smoke check (`docker info`, `docker
compose version`, `docker buildx version`, `harbor --version`, worker import),
stops the instance, registers the AMI, waits for it to become `available`, and
terminates the builder instance. It never writes a job token, run ID, or AWS
credential into the image; `src.control_panel.ami_build.assert_image_is_runtime_free`
enforces that at build time.

The AMI is tagged with `ai4sbench:worker-commit` and `ai4sbench:harbor-version`.
After a successful build, record in the deployment notes:

```text
control-plane commit: <git rev-parse HEAD>
worker AMI ID: ami-...
worker AMI commit: <commit the AMI was built from>
harbor version: 0.20.0
```

Then update the environment file and restart:

```text
TBCP_EC2_BOOTSTRAP_MODE=baked_ami
TBCP_EC2_AMI_ID=<new ami id>
TBCP_EC2_WORKER_AMI_COMMIT=<commit the AMI was built from>
```

Because the worker package is baked into the image, a worker code change has
no effect in production until a new AMI is built and `TBCP_EC2_AMI_ID` is
updated. Every EC2 instance the provider launches is tagged with
`ai4sbench:worker-ami` and `ai4sbench:worker-commit`, and the `worker_claimed`
run event records the same values, so a run can be traced back to the exact
worker image it executed after the instance has been terminated.

Verify the credential source without printing a key:

```bash
sudo -u ai4sbench /opt/ai4sbench/backend/.venv/bin/python - <<'PY'
import boto3

credentials = boto3.Session().get_credentials()
print(credentials.method if credentials else "no AWS credentials")
PY
```

## SQLite operations

The current database is owned by `ai4sbench:ai4sbench` and has mode `0644`.
It passed `PRAGMA quick_check` in read-only mode on 2026-08-30.

SQLite is a file, not a network database. Do **not** mount it over SSHFS for
read/write use and do not copy the live database file while treating it as a
consistent backup. Use an SQLite backup snapshot instead.

### Read-only integrity check

The instance does not currently have the `sqlite3` CLI installed. Use Python's
built-in driver in read-only mode:

```bash
sudo -u ai4sbench /opt/ai4sbench/backend/.venv/bin/python - <<'PY'
import sqlite3

connection = sqlite3.connect(
    'file:/var/lib/ai4sbench/control-panel.sqlite3?mode=ro', uri=True
)
print(connection.execute('PRAGMA quick_check').fetchone()[0])
connection.close()
PY
```

Expected output: `ok`.

### Create and download a consistent backup

Before a migration, Full Sync, or other material database operation, open the
Dashboard's **Database snapshots** page and select **Save SQLite snapshot**.
The same authenticated operation is available at:

```text
POST /api/v1/database-snapshots
```

The application uses SQLite's online backup API and writes an immutable file to:

```text
/var/lib/ai4sbench/cache/sqlite-snapshots/
```

The Dashboard lists and downloads these files through:

```text
GET /api/v1/database-snapshots
GET /api/v1/database-snapshots/{name}/download
```

Record the generated snapshot name in the deployment notes. Do not copy the
live database and its WAL directly, and do not place a snapshot in Git.

## Deployment procedure

The currently deployed control plane is a direct systemd/Uvicorn deployment,
not a Docker deployment. Every application update is delivered through Git;
do not use `scp`, `install`, or ad-hoc file replacement for source or static
assets.

The long-lived deployment target is `main`. Always record the exact `main`
commit being deployed; do not deploy an unmerged feature branch.

### Backend-hosted Website preview

The API serves the committed `backend/src/website_dist/` files at
`https://dashboard.ai4sbench.org/website-preview/`. The Website is plain HTML, CSS,
JavaScript, and data files; there is no build step. The API does not serve files
from the `ai4s-bench-website/` submodule checkout at runtime.

To prepare a Website preview release:

1. Push the intended Website source commit to the configured fork
   (`hycarbon-b/ai4s-bench-website`). The source PR can remain open; neither a
   merge into the official Website repository nor an update to its GitHub Pages
   site is required for a backend preview release.
2. In the parent checkout, fetch and check out that pushed commit in the
   `ai4s-bench-website/` submodule, then stage the submodule pointer:

   ```powershell
   git -C ai4s-bench-website fetch origin
   git -C ai4s-bench-website checkout <website-commit>
   git add ai4s-bench-website
   python scripts/sync_website_preview.py
   python scripts/sync_website_preview.py --check
   git add backend/src/website_dist
   ```

   The sync script mirrors Website pages and assets into
   `backend/src/website_dist/`, excludes repository-only files such as tests
   and `.github/`, and removes stale snapshot files. Review both the staged
   submodule pointer and snapshot in the parent PR.
3. After parent CI succeeds, merge the parent PR into `main` and follow the
   deployment steps below. Production deploys the committed preview snapshot
   from parent `main` at `/website-preview/`.

The separate `https://ai4sbench.org/` GitHub Pages site follows the Website
repository's own `main` and publishing process. Publishing `/website-preview/`
does not merge the Website PR or update GitHub Pages. A later Website merge also
does not refresh the backend snapshot automatically.

### Run the deployment script on EC2

The fixed routine deployment lives in `deploy/deploy.sh`, with read-only HTTP
and asset checks in `deploy/verify.py`. Both run on EC2. CI is a release
prerequisite: merge the reviewed parent PR to `main` after
`python scripts/ci.py` succeeds, then deploy that main commit over SSH:

```powershell
ssh ec2-user@3.237.65.103
```

On the instance:

```bash
sudo bash /opt/ai4sbench/deploy/deploy.sh <main-commit-sha>
```

Omitting the SHA selects the latest fetched `origin/main`. The script freezes
that SHA at the start, so a later main update does not change the release in
progress. The server needs Git, curl, Python 3, sudo/runuser, systemd, flock,
the existing service virtualenv, and uv when locked dependencies change.

For the first release introducing this script, it is not yet in the old server
checkout. Fetch the reviewed main commit and read the script from Git so the
snapshot still precedes the checkout update:

```bash
sudo -u ai4sbench git -C /opt/ai4sbench fetch origin main
sudo -u ai4sbench git -C /opt/ai4sbench show <main-commit-sha>:deploy/deploy.sh | sudo bash -s -- <main-commit-sha>
```

The script runs these steps in order:

1. Acquire a deployment lock. Require server branch `main`, a clean checkout
   and clean initialized submodules. Fetch main, verify that the target belongs
   to main and fast-forwards the current commit, and preserve the runtime
   `repository/` directory. Reject target files that would collide with it.
2. Load the protected production environment as the service account. Verify
   the production database path, create an SQLite online backup using the same
   helper as the Dashboard snapshot action, check its integrity, and print the
   snapshot path and current Alembic revision.
3. Fast-forward to the exact SHA, sync submodule URLs, and update submodules
   recursively to the parent gitlinks. The Website fork may contain an open
   source PR; the backend still serves the parent's committed
   `backend/src/website_dist/` snapshot at `/website-preview/`.
4. If `backend/uv.lock` or `backend/pyproject.toml` changed, sync frozen
   dependencies with `dev` and `aws` extras, retaining `harbor-ec2` when the v2
   runner is enabled or active.
5. Run Alembic `upgrade head` and `current` with the production environment.
6. Restart API and jobs, including the Harbor v2 runner when enabled or active.
7. Wait for readiness. Verify readiness, OpenAPI review/proposal routes and
   public proposals on loopback and the public HTTPS URL. Compare all Dashboard
   and Website file hashes on loopback. On the public URL, compare asset hashes
   and HTML titles/resource references because Cloudflare rewrites HTML for
   analytics and email protection. Print recent warning-level service logs and
   the final parent/Website SHAs.

A log including the target SHA and snapshot path is saved under
`/var/lib/ai4sbench/deployments/`. Failure stops the sequence and reports the
failed step, snapshot and log. Inspect these before recovering. Review migrations
before release; the script does not automatically reset Git, downgrade Alembic
or restore the database. Read-only Alembic checks also require the production
environment, otherwise they may inspect a different database.

Initial host provisioning, protected environment changes, systemd-unit
installation and worker AMI rollout remain separate operations. When the routine
release sequence changes, update `deploy/` and this runbook together.

### Deploying Harbor EC2 v2

The API, v1 jobs, and v2 jobs systemd units are committed under
`backend/systemd/`. Before enabling v2:

1. Add `harbor[ec2]==0.20.0` to the service virtual environment with
   `uv sync --frozen --extra aws --extra harbor-ec2 --extra dev`.
2. Set `TBCP_HARBOR_V2_ENABLED=true`, `TBCP_HARBOR_V2_EC2_KEY_NAME`, and
   `TBCP_HARBOR_V2_SSH_KEY_PATH` in the protected environment file. Reuse the
   v1 AMI, subnet, security group, and region settings. The SSH private key
   must be readable by `ai4sbench` and match the EC2 key pair.
3. Install the unit, reload systemd, and enable it:

   ```bash
   sudo install -o root -g root -m 0644 backend/systemd/ai4sbench-api.service /etc/systemd/system/
   sudo install -o root -g root -m 0644 backend/systemd/ai4sbench-jobs.service /etc/systemd/system/
   sudo install -o root -g root -m 0644 backend/systemd/ai4sbench-harbor-v2-jobs.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl restart ai4sbench-api.service ai4sbench-jobs.service
   sudo systemctl enable --now ai4sbench-harbor-v2-jobs.service
   ```

4. Check the v2 runner journal and `/docs/ec2/v2`. Harbor tags each worker
   instance as `ai4sbench:v2-run=<run_id>` and terminates it after the job.

The example operator configuration uses a Docker-ready v1 AMI and a public
subnet. The security group must allow TCP 22 from the runner host to workers.
For a private runner, use VPC routing and private addresses instead.

### Proposal deletion and synchronization

`DELETE /api/v1/proposals/{proposal_id}` is administrator-only logical
deletion. It sets `proposals.deleted_at`, hides the record from Dashboard and
Website list APIs, and preserves the GitHub Discussion and task-revision link.
Full Sync matches deleted Discussions by node ID or URL and skips them, so a
deleted Proposal does not reappear. Do not delete the GitHub Discussion as part
of this operation.

The protected `/etc/ai4sbench/control-panel.env` is not replaced during a code
deployment. Environment changes are a separate reviewed operation followed by
a service restart and CORS/OAuth verification.

## Capacity snapshot

Verified 2026-09-13:

| Resource | Observed state |
| --- | --- |
| Root filesystem | 20 GB total; approximately 2.8 GB used; 18 GB available |
| Memory | approximately 913 MiB total; approximately 335 MiB available |
| Swap | none |
| Database file | approximately 680 KiB |

The host is a `t3.micro` with no swap. Monitor memory before adding workers,
large synchronizations, or resource-intensive background jobs.

## AI proposal reviews

See [AI review setup, API and recovery](backend/docs/ai-proposal-reviews.md) for the service key, bot token,
Discord destination, benchmark Actions settings, migration and smoke-test sequence.
Deploy the backend before enabling the benchmark workflow.
