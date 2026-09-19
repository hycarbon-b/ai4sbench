#!/usr/bin/env python3
"""Build a baked AI4S-Bench worker AMI from a pinned repository commit.

The resulting image carries Docker, the Compose and Buildx CLI plugins, Git,
Python 3.12, Harbor, and the worker package itself.  Launching an instance from
it needs only the three run-scoped environment variables the control plane
injects through user data, so the run path no longer installs software.

This is a deployment-operator tool.  It needs EC2 create permissions and is
deliberately not reachable from the control-plane runtime.

    python scripts/build_worker_ami.py \
      --base-ami-id ami-0123456789abcdef0 \
      --subnet-id subnet-abc123 \
      --security-group-id sg-abc123 \
      --repo-url https://github.com/hycarbon-b/ai4sbench.git

On success it prints the new AMI ID and the commit it was built from.  Record
both in the deployment notes together with the control-plane commit.
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from control_panel.ami_build import (  # noqa: E402 - path setup must precede the import
    ImageSpec,
    assert_image_is_runtime_free,
    render_provisioning_script,
)

BUILD_TIMEOUT_SECONDS = 3600
POLL_SECONDS = 20


def git_head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ami-id", required=True, help="Amazon Linux 2023 base image")
    parser.add_argument("--security-group-id", required=True, action="append", dest="security_group_ids")
    parser.add_argument("--repo-url", required=True, help="Clone URL the image installs the worker from")
    parser.add_argument("--subnet-id", default=None)
    parser.add_argument("--commit-sha", default=None, help="Defaults to the local checkout's HEAD")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--instance-type", default="m7i-flex.large")
    parser.add_argument("--root-volume-gb", type=int, default=40)
    parser.add_argument("--name-prefix", default="ai4sbench-worker")
    parser.add_argument("--keep-builder", action="store_true", help="Leave the builder instance for triage")
    return parser.parse_args()


def wait_for(description: str, predicate, timeout_seconds: int = BUILD_TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if (value := predicate()) is not None:
            return value
        print(f"  waiting for {description}...", flush=True)
        time.sleep(POLL_SECONDS)
    raise SystemExit(f"Timed out waiting for {description}")


def main() -> None:
    arguments = parse_arguments()
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - operator tooling
        raise SystemExit("Install the aws dependency group: uv sync --extra aws") from exc

    commit_sha = arguments.commit_sha or git_head(ROOT)
    spec = ImageSpec(repo_url=arguments.repo_url, commit_sha=commit_sha)
    script = render_provisioning_script(spec)
    assert_image_is_runtime_free(script)

    client = boto3.client("ec2", region_name=arguments.region)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    builder_tags = [
        {"Key": "Name", "Value": f"{arguments.name_prefix}-builder-{stamp}"},
        {"Key": "ai4sbench:managed", "Value": "true"},
        {"Key": "ai4sbench:image-role", "Value": "builder"},
    ]
    request = {
        "ImageId": arguments.base_ami_id,
        "InstanceType": arguments.instance_type,
        "MinCount": 1,
        "MaxCount": 1,
        "UserData": script,
        "InstanceInitiatedShutdownBehavior": "stop",
        "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
        "BlockDeviceMappings": [
            {
                "DeviceName": "/dev/xvda",
                "Ebs": {
                    "DeleteOnTermination": True,
                    "Encrypted": True,
                    "VolumeSize": arguments.root_volume_gb,
                    "VolumeType": "gp3",
                },
            }
        ],
        "TagSpecifications": [{"ResourceType": "instance", "Tags": builder_tags}],
    }
    if arguments.subnet_id:
        request["NetworkInterfaces"] = [
            {
                "DeviceIndex": 0,
                "SubnetId": arguments.subnet_id,
                "Groups": list(arguments.security_group_ids),
                "AssociatePublicIpAddress": True,
                "DeleteOnTermination": True,
            }
        ]
    else:
        request["SecurityGroupIds"] = list(arguments.security_group_ids)

    instance_id = client.run_instances(**request)["Instances"][0]["InstanceId"]
    print(f"Builder instance: {instance_id}")
    image_id = ""
    try:
        # The provisioning script powers the instance off only after its smoke
        # check passes, so `stopped` is the build's success signal.
        def stopped() -> str | None:
            state = client.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0][
                "State"
            ]["Name"]
            if state == "stopped":
                return state
            if state in {"shutting-down", "terminated"}:
                raise SystemExit(f"Builder instance entered {state}; inspect the console output")
            return None

        wait_for("the provisioning script to finish and stop the instance", stopped)
        console = client.get_console_output(InstanceId=instance_id, Latest=True).get("Output", "")
        if console:
            decoded = base64.b64decode(console).decode("utf-8", "replace") if _is_base64(console) else console
            if "/var/lib/ai4sbench-image-ready" not in decoded and "harbor" not in decoded.lower():
                print("Console output did not confirm the smoke check; review it before use", flush=True)

        image_id = client.create_image(
            InstanceId=instance_id,
            Name=f"{arguments.name_prefix}-{commit_sha[:12]}-{stamp}",
            Description=f"AI4S-Bench worker at {commit_sha} with Harbor {spec.harbor_version}",
            TagSpecifications=[
                {
                    "ResourceType": "image",
                    "Tags": [{"Key": key, "Value": value} for key, value in spec.tags().items()],
                }
            ],
        )["ImageId"]
        print(f"Registered image: {image_id}")
        client.get_waiter("image_available").wait(
            ImageIds=[image_id],
            WaiterConfig={"Delay": POLL_SECONDS, "MaxAttempts": BUILD_TIMEOUT_SECONDS // POLL_SECONDS},
        )
    finally:
        if arguments.keep_builder:
            print(f"Builder instance {instance_id} was kept for triage; terminate it manually")
        else:
            client.terminate_instances(InstanceIds=[instance_id])
            print(f"Terminated builder instance {instance_id}")

    print("\nBuild complete. Record these values in the deployment notes:")
    print(f"  TBCP_EC2_AMI_ID={image_id}")
    print(f"  TBCP_EC2_BOOTSTRAP_MODE=baked_ami")
    print(f"  TBCP_EC2_WORKER_AMI_COMMIT={commit_sha}")
    print(f"  harbor version: {spec.harbor_version}")


def _is_base64(value: str) -> bool:
    try:
        base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return False
    return True


if __name__ == "__main__":
    main()
