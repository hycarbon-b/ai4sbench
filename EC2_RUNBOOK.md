# AI4S-Bench Dashboard EC2 runbook

Last verified: 2026-08-30 (Asia/Shanghai)

This document records the current production control-plane deployment. It is
safe to commit because it intentionally excludes private keys, GitHub OAuth
credentials, JWT secrets, job-token secrets, and the full environment file.

## Service at a glance

| Item | Current value |
| --- | --- |
| Public Dashboard | `https://dashboard.ai4sbench.org` |
| Health endpoint | `https://dashboard.ai4sbench.org/health/live` |
| AWS region | `us-east-1` |
| EC2 instance ID | `i-0ad6edfa737560f25` |
| Instance type | `t3.micro` |
| Public IPv4 / SSH host | `3.237.65.103` |
| Private hostname | `ip-172-31-4-109.ec2.internal` |
| OS | Amazon Linux 2023, Linux `6.18.41-94.142.amzn2023.x86_64` |
| Application listener | `0.0.0.0:8080` (Uvicorn) |
| Service account | `ai4sbench` |
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

After an API restart, verify both the local listener and public route:

```bash
sudo ss -ltnp | grep ':8080'
curl -fsS http://127.0.0.1:8080/health/live
```

```powershell
Invoke-WebRequest -UseBasicParsing https://dashboard.ai4sbench.org/health/live
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

Run this on the EC2 instance. The destination directory should be access-limited
and backed up according to the project's retention policy.

```bash
sudo -u ai4sbench /opt/ai4sbench/backend/.venv/bin/python - <<'PY'
import sqlite3

source = sqlite3.connect('/var/lib/ai4sbench/control-panel.sqlite3')
destination = sqlite3.connect('/tmp/control-panel-snapshot.sqlite3')
source.backup(destination)
destination.close()
source.close()
PY
sudo chown ec2-user:ec2-user /tmp/control-panel-snapshot.sqlite3
```

Download it from the local machine:

```powershell
scp ec2-user@3.237.65.103:/tmp/control-panel-snapshot.sqlite3 .
```

After confirming the download and any required off-site backup, remove the
temporary server-side copy:

```bash
rm -f /tmp/control-panel-snapshot.sqlite3
```

## Deployment procedure

The currently deployed control plane is a direct systemd/Uvicorn deployment,
not a Docker deployment. For a small, code-only update:

```powershell
scp backend/control_panel/main.py ec2-user@3.237.65.103:/tmp/main.py
ssh ec2-user@3.237.65.103 "sudo install -o ai4sbench -g ai4sbench -m 664 /tmp/main.py /opt/ai4sbench/backend/control_panel/main.py; sudo rm -f /tmp/main.py; sudo systemctl restart ai4sbench-api.service; sudo systemctl is-active ai4sbench-api.service"
```

For a broader release, upload only the reviewed files, preserve the existing
secret environment file, restart the affected unit, and run the health check.
Do not overwrite the production environment from a repository example file.

## Capacity snapshot

Verified 2026-08-30:

| Resource | Observed state |
| --- | --- |
| Root filesystem | 20 GB total; approximately 2.5 GB used; 18 GB available |
| Memory | approximately 913 MiB total; approximately 375 MiB available |
| Swap | none |
| Database file | approximately 220 KB |

The host is a `t3.micro` with no swap. Monitor memory before adding workers,
large synchronizations, or resource-intensive background jobs.
