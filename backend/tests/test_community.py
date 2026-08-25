from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from control_panel.community import document_from_discussion
from control_panel.config import Settings
from control_panel.identity import User, current_active_user
from control_panel.main import create_app
from control_panel.schemas import ProposalDocument


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
        "subfield": "Coastal oceanography",
        "task_slug": "sparse-coastal-assimilation",
        "scientific_problem": (
            "Reconstruct a gridded coastal state estimate from sparse observations while preserving "
            "the physical constraints used by the operational workflow."
        ),
        "workflow_details": (
            "Assimilate sparse observations into a gridded state estimate and publish the required "
            "output artifacts."
        ),
        "dependencies_and_system_requirements": "Python 3.12, two CPU cores, 4 GB RAM, and 10 GB storage.",
        "dataset": (
            "A small, versioned coastal-observation dataset with clearly documented provenance and a "
            "separate verifier split."
        ),
        "evaluation_strategy": (
            "Verify the output artifacts programmatically against a deterministic reference workflow "
            "in a separate verifier environment."
        ),
        "complexity": (
            "The task requires physical-oceanography judgement, sparse-data assimilation, and validation "
            "of a constrained reconstruction."
        ),
        "references_and_resources": (
            "The workflow follows a maintained coastal-observation protocol and its published "
            "reproducibility archive."
        ),
        "additional_information": "None provided",
    }


def test_canonical_document_renders_dashboard_compatible_markdown() -> None:
    document = ProposalDocument.model_validate(proposal_payload()).with_author(
        github_login="scientist", email="scientist@example.test"
    )
    rendered = document.render_discussion()
    assert "## Scientific Domain\n\nEarth Sciences > Ocean Sciences > Coastal oceanography" in rendered
    assert "## Author Information" in rendered
    assert "GitHub: https://github.com/scientist" in rendered


def test_dashboard_style_discussion_round_trips_to_canonical_document() -> None:
    document = document_from_discussion(
        {
            "title": "[Task Proposal #41] Assimilate a sparse coastal observation network",
            "author": {"login": "scientist"},
            "body": ProposalDocument.model_validate(proposal_payload())
            .with_author(github_login="scientist", email="scientist@example.test")
            .render_discussion(),
        }
    )
    assert document.domain == "earth-sciences"
    assert document.field == "ocean-sciences"
    assert document.subfield == "Coastal oceanography"
    assert document.author_information.github == "scientist"


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
                AsyncMock(
                    return_value={
                        "id": "D_kwDOExample",
                        "number": 1,
                        "url": "https://github.com/example/repo/discussions/1",
                    }
                ),
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


def test_admin_full_sync_upserts_discussions_without_deleting_records() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        app = create_app(
            Settings(
                environment="test",
                database_url=f"sqlite:///{database.as_posix()}",
                auto_create_schema=True,
                execution_mode="fake",
                github_repository="example/repo",
            )
        )
        app.dependency_overrides[current_active_user] = lambda: user("admin")
        document = ProposalDocument.model_validate(proposal_payload()).with_author(
            github_login="scientist", email="scientist@example.test"
        )
        discussion = {
            "id": "D_kwDOExample",
            "number": 41,
            "url": "https://github.com/example/repo/discussions/41",
            "title": "[Task Proposal #41] Assimilate a sparse coastal observation network",
            "body": document.render_discussion(),
            "category": {"name": "Task Proposals"},
            "author": {"login": "scientist"},
            "labels": {"nodes": []},
            "closed": False,
            "createdAt": "2026-08-25T00:00:00Z",
            "updatedAt": "2026-08-25T00:00:00Z",
        }
        with (
            TestClient(app) as client,
            patch("control_panel.community.github_access_token", return_value="test-token"),
            patch(
                "control_panel.community.fetch_all_discussions",
                AsyncMock(return_value=[discussion]),
            ) as fetch,
        ):
            first = client.post("/api/v1/proposals/sync-discussions")
            assert first.status_code == 200, first.text
            assert first.json() == {"scanned_count": 1, "created_count": 1, "updated_count": 0}

            discussion["labels"] = {"nodes": [{"name": "proposal-approved ✅"}]}
            discussion["updatedAt"] = "2026-08-25T01:00:00Z"
            second = client.post("/api/v1/proposals/sync-discussions")
            assert second.status_code == 200, second.text
            assert second.json() == {"scanned_count": 1, "created_count": 0, "updated_count": 1}

            fetch.return_value = []
            third = client.post("/api/v1/proposals/sync-discussions")
            assert third.status_code == 200, third.text
            assert third.json() == {"scanned_count": 0, "created_count": 0, "updated_count": 0}
            records = client.get("/api/v1/proposals")
            assert records.status_code == 200
            assert len(records.json()["items"]) == 1
            assert records.json()["items"][0]["status"] == "approved"


def test_anonymous_user_cannot_submit_a_proposal() -> None:
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
        with TestClient(app) as client:
            response = client.post("/api/v1/proposals", json=proposal_payload())
            assert response.status_code == 401


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


def test_logout_clears_only_the_control_panel_cookie() -> None:
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
        with TestClient(app) as client:
            response = client.post("/api/v1/auth/logout")
            assert response.status_code == 204
            assert "ai4sbench_session=\"\"" in response.headers["set-cookie"]


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
