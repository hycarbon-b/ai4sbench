from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TBCP_",
        enable_decoding=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = 8080
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")
    cors_origins: tuple[str, ...] = ()
    database_url: str = "sqlite:///./data/control-panel.sqlite3"
    auto_create_schema: bool = False
    auth_jwt_secret: SecretStr = SecretStr("development-auth-secret-change-me-32chars")
    github_oauth_client_id: str = ""
    github_oauth_client_secret: SecretStr | None = None
    github_repository: str = ""
    github_repository_node_id: str = ""
    github_discussion_category_id: str = ""
    admin_github_logins: tuple[str, ...] = ()
    admin_github_emails: tuple[str, ...] = ()
    contributor_github_logins: tuple[str, ...] = ()
    contributor_github_emails: tuple[str, ...] = ()
    reviewer_github_logins: tuple[str, ...] = ()
    job_token_secret: SecretStr = SecretStr("development-job-secret-change-me")
    execution_mode: Literal["fake", "ec2"] = "fake"
    max_active_runs: int = Field(default=1, ge=1, le=32)
    queue_poll_seconds: float = Field(default=1.0, ge=0.1, le=60)
    job_lease_seconds: int = Field(default=120, ge=30, le=3600)
    worker_api_base_url: str = "http://127.0.0.1:8080"
    enable_quick_tunnel: bool = True

    # Free-plan E2E defaults.  boto3 reads the account credentials from the
    # standard AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY environment variables;
    # an instance profile is intentionally optional because workers only call
    # the control-plane callback API.
    aws_region: str = Field(
        default="us-east-1",
        validation_alias=AliasChoices("TBCP_AWS_REGION", "AWS_REGION"),
    )
    aws_access_key_id: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AWS_ACCESS_KEY_ID", "TBCP_AWS_ACCESS_KEY_ID"),
    )
    aws_secret_access_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AWS_SECRET_ACCESS_KEY", "TBCP_AWS_SECRET_ACCESS_KEY"),
    )
    ec2_ami_id: str = ""
    ec2_bootstrap_mode: Literal["amazon_linux_2023", "baked_ami"] = "amazon_linux_2023"
    ec2_instance_type: str = "m7i-flex.xlarge"
    ec2_allowed_instance_types: tuple[str, ...] = (
        "t3.micro",
        "t3.small",
        "t4g.micro",
        "t4g.small",
        "c7i-flex.large",
        "c7i-flex.xlarge",
        "m7i-flex.large",
        "m7i-flex.xlarge",
        "m7i.2xlarge",
    )
    ec2_instance_resources: dict[str, dict[str, int]] = Field(
        default_factory=lambda: {
            "t3.micro": {"cpus": 2, "memory_mb": 1024},
            "t3.small": {"cpus": 2, "memory_mb": 2048},
            "t4g.micro": {"cpus": 2, "memory_mb": 1024},
            "t4g.small": {"cpus": 2, "memory_mb": 2048},
            "c7i-flex.large": {"cpus": 2, "memory_mb": 4096},
            "c7i-flex.xlarge": {"cpus": 4, "memory_mb": 8192},
            "m7i-flex.large": {"cpus": 2, "memory_mb": 8192},
            "m7i-flex.xlarge": {"cpus": 4, "memory_mb": 16384},
            "m7i.2xlarge": {"cpus": 8, "memory_mb": 32768},
        }
    )
    ec2_root_volume_gb: int = Field(default=40, ge=20, le=500)
    ec2_subnet_id: str | None = None
    ec2_security_group_ids: tuple[str, ...] = ()
    ec2_instance_profile_arn: str | None = None
    ec2_associate_public_ip: bool = True
    github_token: SecretStr | None = None
    git_https_proxy: str | None = None

    @field_validator(
        "allowed_hosts",
        "cors_origins",
        "ec2_allowed_instance_types",
        "ec2_security_group_ids",
        "admin_github_logins",
        "admin_github_emails",
        "contributor_github_logins",
        "contributor_github_emails",
        "reviewer_github_logins",
        mode="before",
    )
    @classmethod
    def parse_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @model_validator(mode="after")
    def validate_production(self) -> Settings:
        if self.environment == "production":
            if self.auto_create_schema:
                raise ValueError("TBCP_AUTO_CREATE_SCHEMA must be false in production")
            if self.execution_mode != "ec2":
                raise ValueError("TBCP_EXECUTION_MODE must be ec2 in production")
            if len(self.job_token_secret.get_secret_value()) < 32:
                raise ValueError("TBCP_JOB_TOKEN_SECRET must contain at least 32 characters")
            if len(self.auth_jwt_secret.get_secret_value()) < 32:
                raise ValueError("TBCP_AUTH_JWT_SECRET must contain at least 32 characters")
            if not self.github_oauth_client_id or not self.github_oauth_client_secret:
                raise ValueError("Production requires GitHub OAuth client credentials")
        if self.execution_mode == "ec2" and (not self.ec2_ami_id or not self.ec2_security_group_ids):
            raise ValueError("EC2 mode requires TBCP_EC2_AMI_ID and TBCP_EC2_SECURITY_GROUP_IDS")
        if bool(self.aws_access_key_id) != bool(self.aws_secret_access_key):
            raise ValueError("AWS access key ID and secret access key must be configured together")
        return self

    def public(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "execution_mode": self.execution_mode,
            "aws_region": self.aws_region,
            "ec2_ami_id": self.ec2_ami_id,
            "ec2_instance_type": self.ec2_instance_type,
            "ec2_allowed_instance_types": list(self.ec2_allowed_instance_types),
            "ec2_instance_resources": self.ec2_instance_resources,
            "ec2_root_volume_gb": self.ec2_root_volume_gb,
            "ec2_subnet_id": self.ec2_subnet_id,
            "ec2_associate_public_ip": self.ec2_associate_public_ip,
            "quick_tunnel_enabled": self.enable_quick_tunnel,
            "max_active_runs": self.max_active_runs,
            "github_login_enabled": bool(self.github_oauth_client_id and self.github_oauth_client_secret),
            "github_repository": self.github_repository,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
