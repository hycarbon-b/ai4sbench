from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from control_panel.community import form_payload_from_discussion
from control_panel.config import Settings
from control_panel.identity import User, current_active_user, current_optional_user
from control_panel.main import create_app
from control_panel.schemas import ProposalSubmission


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
        "domain": "Earth Sciences",
        "field_name": "Coastal oceanography",
        "problem": (
            "Reconstruct a gridded coastal state estimate from sparse observations while preserving "
            "the physical constraints used by the operational workflow."
        ),
        "solvability": "The scientific constraints are known and the evaluation target is well-defined.",
        "references": (
            "The workflow follows a maintained coastal-observation protocol and its published "
            "reproducibility archive."
        ),
        "software": "Python 3.12, xarray, two CPU cores, and a deterministic solver.",
        "dataset": (
            "A small, versioned coastal-observation dataset with clearly documented provenance and a "
            "separate verifier split."
        ),
        "compute": "Two CPU cores, 4 GB RAM, and 10 GB storage.",
        "workflow": (
            "Assimilate sparse observations into a gridded state estimate and publish the required "
            "output artifacts."
        ),
        "evaluation": (
            "Verify the output artifacts programmatically against a deterministic reference workflow "
            "in a separate verifier environment."
        ),
        "leakage": "The held-out verifier split is never exposed to the task agent.",
        "name": "Coastal Scientist",
        "affiliation": "Example University",
        "github": "scientist",
    }


def test_form_contract_renders_and_round_trips_through_a_discussion() -> None:
    submission = ProposalSubmission.model_validate(proposal_payload())
    rendered = submission.render_discussion()
    payload = form_payload_from_discussion(
        {
            "title": "[Task Proposal #41] Assimilate a sparse coastal observation network",
            "author": {"login": "scientist"},
            "body": rendered,
        }
    )
    imported = ProposalSubmission.model_validate(payload)
    assert imported.domain == "Earth Sciences"
    assert imported.field_name == "Coastal oceanography"
    assert imported.github == "scientist"
    assert imported.task_slug == "assimilate-a-sparse-coastal-observation-network"


def test_domains_are_normalized_as_one_readable_comma_separated_string() -> None:
    payload = proposal_payload()
    payload["domain"] = "Materials Science,Chemistry, Materials Science"
    submission = ProposalSubmission.model_validate(payload)
    assert submission.domain == "Materials Science, Chemistry"


def test_old_canonical_payload_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ProposalSubmission.model_validate(
            {
                "title": "Assimilate a sparse coastal observation network",
                "domain": "earth-sciences",
                "field": "ocean-sciences",
                "subfield": "Coastal oceanography",
            }
        )


def test_extra_legacy_fields_are_rejected() -> None:
    payload = proposal_payload()
    payload["task_slug"] = "legacy-client-supplied-slug"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProposalSubmission.model_validate(payload)


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
            assert response.json()["input"]["github"] == "member-github"
            assert (
                response.json()["derived"]["task_slug"]
                == "assimilate-a-sparse-coastal-observation-network"
            )
            assert response.json()["discussion"]["body"].startswith("## Scientific Domain")
            denied = client.post(
                "/api/v1/cloud-profiles",
                json={"name": "paid-us-east-1", "provider": "aws", "allocation": {"max_active_runs": 3}},
            )
            assert denied.status_code == 403


def test_member_can_preview_exact_submission_without_creating_a_discussion() -> None:
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
        app.dependency_overrides[current_optional_user] = lambda: user("member")
        with TestClient(app) as client:
            preview = client.post("/api/v1/proposals/preview", json=proposal_payload())
            assert preview.status_code == 200, preview.text
            payload = preview.json()
            assert payload["input"]["github"] == "member-github"
            assert payload["derived"] == {
                "domain": "Earth Sciences",
                "field": "coastal-oceanography",
                "task_slug": "assimilate-a-sparse-coastal-observation-network",
            }
            assert payload["github_identity_source"] == "authenticated"
            assert payload["discussion"]["title"] == proposal_payload()["title"]
            assert "GitHub: https://github.com/member-github" in payload["discussion"]["body"]
            assert client.get("/api/v1/proposals").json()["items"] == []


def test_proposal_domains_are_public_and_documented() -> None:
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
            response = client.get("/api/v1/proposal-domains")
            assert response.status_code == 200, response.text
            assert response.json()["items"] == [
                "Materials Science",
                "Physics",
                "Chemistry",
                "Biology",
                "AI / ML",
                "Applied Mathematics",
                "Interdisciplinary",
            ]
            schema = client.get("/openapi.json").json()
            response_schema = schema["paths"]["/api/v1/proposal-domains"]["get"]["responses"]["200"]
            assert response_schema["content"]["application/json"]["schema"]["$ref"] == (
                "#/components/schemas/ProposalDomainListResponse"
            )


def test_empty_proposal_preview_lists_required_input_fields() -> None:
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
            response = client.post("/api/v1/proposals/preview")
            assert response.status_code == 200, response.text
            assert response.json() == {
                "input": None,
                "derived": None,
                "discussion": None,
                "github_identity_source": None,
                "missing_fields": [
                    "title",
                    "domain",
                    "field_name",
                    "problem",
                    "solvability",
                    "references",
                    "software",
                    "dataset",
                    "compute",
                    "workflow",
                    "evaluation",
                    "leakage",
                    "name",
                    "github",
                ],
            }


def test_proposals_from_different_repositories_can_share_a_discussion_number() -> None:
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
        app.dependency_overrides[current_active_user] = lambda: user("member")
        discussions = [
            {"id": "D_kwDOFirst", "number": 9, "url": "https://github.com/one/repo/discussions/9"},
            {"id": "D_kwDOSecond", "number": 9, "url": "https://github.com/two/repo/discussions/9"},
        ]
        with (
            TestClient(app) as client,
            patch("control_panel.community.create_github_discussion", AsyncMock(side_effect=discussions)),
        ):
            assert client.post("/api/v1/proposals", json=proposal_payload()).status_code == 201
            assert client.post("/api/v1/proposals", json=proposal_payload()).status_code == 201


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
        document = ProposalSubmission.model_validate(proposal_payload())
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
            patch("control_panel.community.github_access_token", AsyncMock(return_value="test-token")),
            patch(
                "control_panel.community.fetch_all_discussions",
                AsyncMock(return_value=[discussion]),
            ) as fetch,
        ):
            first = client.post("/api/v1/proposals/sync-discussions")
            assert first.status_code == 200, first.text
            assert first.json() == {
                "scanned_count": 1,
                "created_count": 1,
                "updated_count": 0,
                "invalid_count": 0,
            }

            discussion["labels"] = {"nodes": [{"name": "proposal-approved ✅"}]}
            discussion["updatedAt"] = "2026-08-25T01:00:00Z"
            second = client.post("/api/v1/proposals/sync-discussions")
            assert second.status_code == 200, second.text
            assert second.json() == {
                "scanned_count": 1,
                "created_count": 0,
                "updated_count": 1,
                "invalid_count": 0,
            }

            fetch.return_value = []
            third = client.post("/api/v1/proposals/sync-discussions")
            assert third.status_code == 200, third.text
            assert third.json() == {
                "scanned_count": 0,
                "created_count": 0,
                "updated_count": 0,
                "invalid_count": 0,
            }
            records = client.get("/api/v1/proposals")
            assert records.status_code == 200
            assert len(records.json()["items"]) == 1
            assert records.json()["items"][0]["status"] == "approved"
            assert records.json()["items"][0]["input_valid"] is True


def test_legacy_discussion_is_retained_but_marked_invalid() -> None:
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
        discussion = {
            "id": "D_kwDOLegacy",
            "number": 99,
            "url": "https://github.com/example/repo/discussions/99",
            "title": "[Task Proposal #99] Legacy coastal reconstruction",
            "body": """## Scientific Domain

Earth Sciences > Ocean Sciences > Coastal oceanography

## Scientific Problem

This older Discussion has a scientific problem but does not use the current form sections.

## Complexity

This legacy field is not the current solvability input.

GitHub: @scientist
""",
            "category": {"name": "Task Proposals"},
            "author": {"login": "scientist"},
            "labels": {"nodes": []},
            "closed": False,
            "createdAt": "2026-08-25T00:00:00Z",
            "updatedAt": "2026-08-25T00:00:00Z",
        }
        with (
            TestClient(app) as client,
            patch("control_panel.community.github_access_token", AsyncMock(return_value="test-token")),
            patch(
                "control_panel.community.fetch_all_discussions",
                AsyncMock(return_value=[discussion]),
            ),
        ):
            response = client.post("/api/v1/proposals/sync-discussions")
            assert response.status_code == 200, response.text
            assert response.json()["invalid_count"] == 1
            records = client.get("/api/v1/proposals")
            assert records.json()["items"][0]["input_valid"] is False


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


def test_configured_application_exposes_same_origin_github_start_page() -> None:
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
            response = client.get("/auth/github/start")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/html")
            assert "fetch('/auth/github/authorize'" in response.text


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
