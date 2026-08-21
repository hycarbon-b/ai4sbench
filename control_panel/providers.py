from __future__ import annotations

import shlex
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from .bootstrap import render_worker_bootstrap
from .config import Settings


class EC2Provider(Protocol):
    def launch(self, run_id: str, worker_token: str, config: dict[str, Any]) -> str: ...

    def terminate(self, instance_id: str) -> None: ...

    def describe(self, instance_id: str) -> str: ...


@dataclass
class FakeEC2Provider:
    instances: dict[str, str] = field(default_factory=dict)
    run_instances: dict[str, str] = field(default_factory=dict)
    launch_configs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def launch(self, run_id: str, worker_token: str, config: dict[str, Any]) -> str:
        if run_id in self.run_instances:
            return self.run_instances[run_id]
        instance_id = f"i-fake{uuid.uuid4().hex[:12]}"
        self.instances[instance_id] = "running"
        self.run_instances[run_id] = instance_id
        self.launch_configs[run_id] = dict(config)
        return instance_id

    def terminate(self, instance_id: str) -> None:
        self.instances[instance_id] = "terminated"

    def describe(self, instance_id: str) -> str:
        return self.instances.get(instance_id, "not_found")


class Boto3EC2Provider:
    def __init__(self, settings: Settings) -> None:
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Install the aws dependency group before using EC2 mode") from exc
        self.settings = settings
        credentials: dict[str, str] = {}
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            credentials = {
                "aws_access_key_id": settings.aws_access_key_id.get_secret_value(),
                "aws_secret_access_key": settings.aws_secret_access_key.get_secret_value(),
            }
        self.client = boto3.client("ec2", region_name=settings.aws_region, **credentials)

    def _user_data(self, run_id: str, worker_token: str) -> str:
        if self.settings.ec2_bootstrap_mode == "amazon_linux_2023":
            return render_worker_bootstrap(self.settings.worker_api_base_url, run_id, worker_token)
        return "\n".join(
            (
                "#!/bin/bash",
                "set -euo pipefail",
                f"export TBCP_API_BASE_URL={shlex.quote(self.settings.worker_api_base_url)}",
                f"export TBCP_RUN_ID={shlex.quote(run_id)}",
                f"export TBCP_JOB_TOKEN={shlex.quote(worker_token)}",
                f"export TBCP_ENABLE_QUICK_TUNNEL={'1' if self.settings.enable_quick_tunnel else '0'}",
                "exec /opt/ai4sbench/.venv/bin/ai4sbench-worker",
            )
        )

    def launch(self, run_id: str, worker_token: str, config: dict[str, Any]) -> str:
        instance_type = str(config.get("instance_type") or self.settings.ec2_instance_type)
        if instance_type not in self.settings.ec2_allowed_instance_types:
            raise ValueError(f"Instance type is not allowlisted: {instance_type}")
        root_volume_gb = int(config.get("root_volume_gb") or self.settings.ec2_root_volume_gb)
        if not 20 <= root_volume_gb <= 500:
            raise ValueError("root_volume_gb must be between 20 and 500")

        tags = [
            {"Key": "Name", "Value": f"ai4sbench-{run_id[:8]}"},
            {"Key": "ai4sbench:managed", "Value": "true"},
            {"Key": "ai4sbench:run-id", "Value": run_id},
        ]
        request: dict[str, Any] = {
            "ClientToken": run_id,
            "ImageId": self.settings.ec2_ami_id,
            "InstanceType": instance_type,
            "MinCount": 1,
            "MaxCount": 1,
            "UserData": self._user_data(run_id, worker_token),
            "MetadataOptions": {
                "HttpTokens": "required",
                "HttpEndpoint": "enabled",
                "HttpPutResponseHopLimit": 1,
            },
            "InstanceInitiatedShutdownBehavior": "terminate",
            "BlockDeviceMappings": [
                {
                    "DeviceName": "/dev/xvda",
                    "Ebs": {
                        "DeleteOnTermination": True,
                        "Encrypted": True,
                        "VolumeSize": root_volume_gb,
                        "VolumeType": "gp3",
                    },
                }
            ],
            "TagSpecifications": [
                {"ResourceType": "instance", "Tags": tags},
                {"ResourceType": "volume", "Tags": tags[1:]},
            ],
        }
        if self.settings.ec2_subnet_id:
            request["NetworkInterfaces"] = [
                {
                    "DeviceIndex": 0,
                    "SubnetId": self.settings.ec2_subnet_id,
                    "Groups": list(self.settings.ec2_security_group_ids),
                    "AssociatePublicIpAddress": self.settings.ec2_associate_public_ip,
                    "DeleteOnTermination": True,
                }
            ]
        else:
            request["SecurityGroupIds"] = list(self.settings.ec2_security_group_ids)
        if self.settings.ec2_instance_profile_arn:
            request["IamInstanceProfile"] = {"Arn": self.settings.ec2_instance_profile_arn}
        response = self.client.run_instances(**request)
        return str(response["Instances"][0]["InstanceId"])

    def terminate(self, instance_id: str) -> None:
        self.client.terminate_instances(InstanceIds=[instance_id])

    def describe(self, instance_id: str) -> str:
        response = self.client.describe_instances(InstanceIds=[instance_id])
        return str(response["Reservations"][0]["Instances"][0]["State"]["Name"])


def provider_from_settings(settings: Settings) -> EC2Provider:
    return FakeEC2Provider() if settings.execution_mode == "fake" else Boto3EC2Provider(settings)
