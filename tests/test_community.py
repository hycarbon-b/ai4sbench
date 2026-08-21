from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from control_panel.config import Settings
from control_panel.identity import User, current_active_user
from control_panel.main import create_app


def user(role: str) -> User:
    return User(
        id=uuid4(),
        email=f"{role}@example.test",
        hashed_password="not-used-in-this-test",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        role=role,
        github_login=f"{role}-github",
    )


def proposal_payload() -> dict[str, str]:
    return {
        "title": "Assimilate a sparse coastal observation network",
        "domain": "earth-sciences",
        "field": "ocean-sciences",
        "task_slug": "sparse-coastal-assimilation",
        "abstract": (
            "Reconstruct a gridded coastal state estimate from sparse observations while preserving "
            "the physical constraints used by the operational workflow."
        ),
        "evidence": (
            "The workflow follows a maintained coastal-observation protocol and its published "
            "reproducibility archive."
        ),
    }


def test_member_can_open_discussion_but_cannot_manage_cloud_profiles() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        app = create_app(
            Settings(
                environment="test",
                database_url=f"sqlite:///{database.as_posix()}",
                auto_create_schema=True,
                execution_mode="fake",
            )
        )
        member = user("member")
        app.dependency_overrides[current_active_user] = lambda: member
        with (
            TestClient(app) as client,
            patch(
                "control_panel.community.create_github_discussion",
                AsyncMock(return_value="https://github.com/example/repo/discussions/1"),
            ),
        ):
            response = client.post("/api/v1/proposals", json=proposal_payload())
            assert response.status_code == 201, response.text
            assert response.json()["discussion_url"].endswith("/1")
            denied = client.post(
                "/api/v1/cloud-profiles",
                json={"name": "paid-us-east-1", "provider": "aws", "allocation": {"max_active_runs": 3}},
            )
            assert denied.status_code == 403


def test_public_auth_configuration_does_not_expose_oauth_secrets() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        app = create_app(
            Settings(
                environment="test",
                database_url=f"sqlite:///{database.as_posix()}",
                auto_create_schema=True,
                execution_mode="fake",
                github_oauth_client_id="public-client-id",
                github_oauth_client_secret="private-client-secret",
            )
        )
        with TestClient(app) as client:
            response = client.get("/api/v1/auth/config")
            assert response.status_code == 200
            assert response.json() == {"github_login_enabled": True}


def test_configured_application_exposes_github_authorize_endpoint() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        app = create_app(
            Settings(
                environment="test",
                database_url=f"sqlite:///{database.as_posix()}",
                auto_create_schema=True,
                execution_mode="fake",
                github_oauth_client_id="public-client-id",
                github_oauth_client_secret="private-client-secret",
            )
        )
        with TestClient(app) as client:
            response = client.get("/auth/github/authorize")
            assert response.status_code == 200, response.text
            authorization_url = response.json()["authorization_url"]
            assert authorization_url.startswith("https://github.com/login/oauth/authorize")
            assert "scope=read%3Auser+user%3Aemail+public_repo" in authorization_url


def test_admin_can_create_cloud_profile() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        app = create_app(
            Settings(
                environment="test",
                database_url=f"sqlite:///{database.as_posix()}",
                auto_create_schema=True,
                execution_mode="fake",
            )
        )
        app.dependency_overrides[current_active_user] = lambda: user("admin")
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/cloud-profiles",
                json={"name": "paid-us-east-1", "provider": "aws", "allocation": {"max_active_runs": 3}},
            )
            assert response.status_code == 201, response.text
            assert response.json()["name"] == "paid-us-east-1"
