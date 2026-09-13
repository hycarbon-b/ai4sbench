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
| Deployed branch | `codex/switch-benchmark-repository` |
| Deployed commit | `2bd0827` |
| Alembic head | `20260913_0014` |

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

Two systemd units run the control plane:

| Unit | Command | Purpose |
| --- | --- | --- |
| `ai4sbench-api.service` | `python -m uvicorn control_panel.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips *` | HTTP API, Dashboard static assets, GitHub OAuth, proposal submission |
| `ai4sbench-jobs.service` | `python -m control_panel.job_runner` | Database-backed background job runner |

Both units currently run as `ai4sbench:ai4sbench` with working directory
`/opt/ai4sbench/backend`.

### How the instance and services start

1. AWS starts the `t3.micro` instance.
2. Amazon Linux boots systemd and reaches `network-online.target`.
3. Because both units are `enabled` for `multi-user.target`, systemd loads
   `/etc/ai4sbench/control-panel.env` and starts `ai4sbench-api.service`.
4. The API launches Uvicorn on port `8080`.
5. `ai4sbench-jobs.service` starts after the API unit and runs the SQLite job
   runner.
6. If either process exits unexpectedly, systemd restarts it after three
   seconds (`Restart=on-failure`).

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
| CORS origins | `https://ai4s-bench.github.io`, `https://ai4sbench.org`, `http://198.18.0.1:3000` |
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

The instance currently runs `codex/switch-benchmark-repository`. After the
release is reviewed and merged, the normal long-lived deployment target should
return to `main`. Always record the exact branch and commit being deployed.

### 1. Verify and build locally

From the parent repository:

```powershell
cd backend
python -m pytest -q
python -m ruff check control_panel tests

cd ../dashboard-frontend/frontend
npm run check
npm run build

cd ../..
git status --short --branch
git diff --check
```

When Dashboard source changes, `npm run build` must update and commit
`backend/control_panel/static/`. When Website source changes, push the Website
submodule branch first, update the parent submodule pointer, refresh
`backend/control_panel/website_dist/`, and commit both. EC2 never builds or
receives these assets separately.

### 2. Commit and push the reviewed deployment branch

```powershell
$DeployBranch = 'codex/switch-benchmark-repository'
git push origin $DeployBranch
git rev-parse --short HEAD
```

Do not create or merge a pull request as an implicit deployment step. Branch
review, merge, and deployment are separate actions.

### 3. Create the pre-deployment database snapshot

Use the Dashboard snapshot action described above and record the returned file
name before changing the checkout or running Alembic.

### 4. Inspect and fast-forward the EC2 checkout

```powershell
ssh ec2-user@3.237.65.103
```

On the instance:

```bash
sudo -u ai4sbench git -C /opt/ai4sbench status --short --branch
sudo -u ai4sbench git -C /opt/ai4sbench fetch origin codex/switch-benchmark-repository
sudo -u ai4sbench git -C /opt/ai4sbench switch codex/switch-benchmark-repository
sudo -u ai4sbench git -C /opt/ai4sbench merge --ff-only origin/codex/switch-benchmark-repository
sudo -u ai4sbench git -C /opt/ai4sbench rev-parse --short HEAD
```

The existing untracked `/opt/ai4sbench/repository/` directory is runtime state.
Leave it in place. Stop if tracked files are modified or if an unexpected
untracked path would collide with the deployment; never clean or reset the
checkout automatically.

### 5. Apply migrations

The production environment file is root-readable and must not be printed.
Load it only for the migration process:

```bash
sudo bash -c '
  set -a
  . /etc/ai4sbench/control-panel.env
  set +a
  cd /opt/ai4sbench/backend
  sudo -E -u ai4sbench .venv/bin/python -m alembic upgrade head
  sudo -E -u ai4sbench .venv/bin/python -m alembic current
'
```

Review every new migration before running it. Do not use `alembic downgrade`
as a generic rollback; restore decisions depend on whether the release changed
data and must use the recorded snapshot when necessary.

### 6. Restart services

```bash
sudo systemctl restart ai4sbench-api.service ai4sbench-jobs.service
sudo systemctl is-active ai4sbench-api.service ai4sbench-jobs.service
```

Both commands must report `active`.

### 7. Verify locally and publicly

```bash
curl -fsS http://127.0.0.1:8080/health/ready
sudo -u ai4sbench git -C /opt/ai4sbench rev-parse --short HEAD
sudo journalctl \
  -u ai4sbench-api.service \
  -u ai4sbench-jobs.service \
  --since '10 minutes ago' \
  --no-pager -p warning
```

From the operator machine, verify:

```powershell
Invoke-RestMethod https://dashboard.ai4sbench.org/health/ready
Invoke-RestMethod https://dashboard.ai4sbench.org/openapi.json
Invoke-RestMethod https://dashboard.ai4sbench.org/api/v1/public/proposals
```

Also confirm that the Dashboard HTML references the newly committed hashed
asset, expected Swagger routes are present, and Proposal counts match the
pre-deployment snapshot unless the release intentionally changed data.

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
