# AWS execution contract

This directory separates control-plane permissions from worker permissions.
Replace every `${...}` placeholder before applying a policy and validate the
result with IAM Access Analyzer.

## What an AWS sandbox account means

An AWS sandbox account is a real, separately billed AWS account intended for
experiments. EC2, EBS, public IPv4, NAT, and data transfer are real resources;
they are not simulated. The value is isolation: the account can be detached
from production networks, placed in a Sandbox OU, restricted with SCPs, assigned
small service quotas, and protected by budgets and automated cleanup.

It is different from LocalStack, which emulates API behavior locally but does
not boot an EC2 kernel or validate AMIs, cloud-init, instance profiles, Docker,
or EC2 networking. It is also different from Cloudflare Sandbox, which is a
managed container sandbox with tunnel/port APIs rather than an AWS account.

Recommended account controls:

- no peering, Transit Gateway, or trust path to production;
- only the selected Region and the four allowlisted instance families;
- budget alerts at 50%, 80%, and 100%, plus an independent stale-instance
  cleanup rule for `ai4sbench:managed=true`;
- low On-Demand vCPU and GPU quotas;
- CloudTrail enabled and root user protected with MFA;
- an SSO role for operators, never long-lived access keys.

## Network

The initial profile uses a public subnet and public IPv4 only for outbound
package, GitHub, model API, and Cloudflare connectivity. The worker security
group has zero inbound rules. A Cloudflare Quick Tunnel is started from the
worker over an outbound connection and exposes only a random-token debug path.

For production, switch `EC2_ASSOCIATE_PUBLIC_IP=false` and use a private subnet
with NAT or carefully selected VPC endpoints. NAT has a much larger fixed hourly
cost than a public IPv4, so it is intentionally not part of the first test.

## Temporary Free-plan E2E mode

The first complete E2E test intentionally uses account-root access keys loaded
from the control panel process environment.  It creates no IAM Identity Center,
IAM user, worker role, or instance profile.  boto3 uses the standard
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` variables and the worker needs
no AWS credentials because it only calls the control-plane HTTPS API.

Keep this mode limited to one short-lived test worker and automatic termination.
It is compatible with an AWS Free plan because it does not create an AWS
Organization; EC2, gp3, public IPv4, and data transfer still consume available
Free-plan credits.  The default eligible family allowlist is maintained in
`control_panel/config.py`.  It validates lifecycle wiring, not task-performance
capacity: the reference task needs four CPUs, while Free-plan small instances
may provide fewer.

## Required APIs

The implemented control plane calls:

- `ec2:RunInstances`
- `ec2:TerminateInstances`
- `ec2:DescribeInstances`
- `ec2:CreateTags` as part of tagged launch authorization
- `iam:PassRole` for exactly the worker instance role (only once the optional
  instance profile is enabled)

Provisioning outside the application additionally needs IAM/EC2 APIs to create
the role, instance profile, security group, subnet, budget, and AMI. Keep those
permissions in an infrastructure role, not the runtime control-plane role.

The worker needs no AWS API permissions for the current callback-based design.
Add S3, CloudWatch Logs, or SSM permissions only when those features are enabled.

## Resource estimate for the pinned task

`amr-poisson-optimize` declares 4 CPU, 4 GiB memory, 10 GiB storage, no GPU,
a 7,200 second agent timeout, and a 1,200 second verifier timeout. The default
worker is `m7i.xlarge` or `c7i.xlarge` with 40 GiB encrypted gp3.

The accepted matrix is three sequential jobs: oracle x5, nop x1, Codex x3.
At the hard timeout it can occupy one instance for roughly 7-8 hours including
image builds. Budget for 3-8 instance-hours, 40 GiB gp3 for less than one day,
one public IPv4 for the instance lifetime, and roughly 2-10 GiB of downloads.
The normal infrastructure cost should be low single-digit USD; model-token cost
is separate and may dominate. Exact EC2 pricing must be read from the selected
Region's current AWS price list before approval.
