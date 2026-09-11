from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from control_panel.community import (
    form_payload_from_discussion,
    review_payload_from_comment,
    validate_proposal_submission,
)
from control_panel.config import Settings
from control_panel.identity import User, current_active_user, current_optional_user
from control_panel.main import create_app
from control_panel.schemas import ProposalReview, ProposalSubmission


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


def review_payload() -> dict[str, object]:
    return {
        "review_decision": "approved",
        "review_short_description": (
            "A reproducible coastal-state reconstruction proposal with a clear verifier boundary."
        ),
        "review_tags": ["coastal oceanography", "data assimilation"],
        "review_difficulty": "Hard",
        "review_scientific_value": (
            "The task tests a consequential scientific workflow with measurable physical constraints."
        ),
        "review_primary_metric": "Held-out state-estimation error",
        "review_primary_metric_short": "Held-out error",
        "review_secondary_metrics": ["Runtime", "Constraint violations"],
        "review_verification_method": (
            "A deterministic held-out verifier checks artifacts, numerical error and constraints."
        ),
        "review_estimated_runtime": "45 minutes",
        "review_compute_budget": "4 vCPU, 8 GiB RAM",
        "review_token_budget": "200k tokens",
        "review_baseline_results": ["Interpolation baseline: 0.42 error"],
        "review_failure_modes": ["Constraint violations near the coastline"],
        "review_notes": "Approved for task implementation.",
    }


def test_form_contract_renders_and_round_trips_through_a_discussion() -> None:
    source = proposal_payload()
    source["workflow"] = (
        "Execute the deterministic workflow, preserve every intermediate artifact, and publish "
        "the complete verifier-ready output bundle. "
    ) * 15
    submission = validate_proposal_submission(source)
    rendered = submission.render_discussion()
    payload = form_payload_from_discussion(
        {
            "title": "[Task Proposal #41] Assimilate a sparse coastal observation network",
            "author": {"login": "scientist"},
            "body": rendered,
        }
    )
    imported = validate_proposal_submission(payload)
    assert imported == submission


def test_review_contract_renders_and_round_trips_through_a_discussion_reply() -> None:
    review = ProposalReview.model_validate(review_payload())
    body = review.render_comment()
    imported = ProposalReview.model_validate(review_payload_from_comment({"body": body}))
    assert imported == review
    assert body.startswith("<!-- ai4sbench-proposal-review:v1 -->")


def test_configured_reviewer_can_publish_review_reply_and_update_public_board() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        app = create_app(
            Settings(
                environment="test",
                database_url=f"sqlite:///{database.as_posix()}",
                auto_create_schema=True,
                execution_mode="fake",
                reviewer_github_logins=("member-github",),
            )
        )
        app.dependency_overrides[current_active_user] = lambda: user("member")
        with (
            TestClient(app) as client,
            patch(
                "control_panel.community.create_github_discussion",
                AsyncMock(
                    return_value={
                        "id": "D_kwDOPublishReview",
                        "number": 54,
                        "url": "https://github.com/example/repo/discussions/54",
                    }
                ),
            ),
            patch(
                "control_panel.community.create_github_discussion_comment",
                AsyncMock(
                    return_value={
                        "id": "DC_kwDOPublishedReview",
                        "url": "https://github.com/example/repo/discussions/54#discussioncomment-3",
                        "body": ProposalReview.model_validate(review_payload()).render_comment(),
                        "author": {"login": "member-github"},
                        "createdAt": "2026-09-01T02:00:00Z",
                        "updatedAt": "2026-09-01T02:00:00Z",
                    }
                ),
            ),
        ):
            created = client.post("/api/v1/proposals", json=proposal_payload())
            assert created.status_code == 201, created.text
            proposal_id = created.json()["id"]

            published = client.post(f"/api/v1/proposals/{proposal_id}/reviews", json=review_payload())
            assert published.status_code == 201, published.text
            assert published.json()["review_comment_node_id"] == "DC_kwDOPublishedReview"
            assert published.json()["input"]["review_decision"] == "approved"

            board = client.get("/api/v1/public/proposals").json()["items"][0]
            assert board["status"] == "approved"
            assert board["review_input_valid"] is True
            assert board["review_reviewer_login"] == "member-github"
            assert board["review_short_description"] == review_payload()["review_short_description"]


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
                response.json()["derived"]["task_slug"] == "assimilate-a-sparse-coastal-observation-network"
            )
            assert response.json()["discussion"]["body"].startswith("## Scientific Domain")
            review_denied = client.post(
                f"/api/v1/proposals/{response.json()['id']}/reviews", json=review_payload()
            )
            assert review_denied.status_code == 403
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
                "reviewed_count": 0,
                "invalid_review_count": 0,
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
                "reviewed_count": 0,
                "invalid_review_count": 0,
            }

            fetch.return_value = []
            third = client.post("/api/v1/proposals/sync-discussions")
            assert third.status_code == 200, third.text
            assert third.json() == {
                "scanned_count": 0,
                "created_count": 0,
                "updated_count": 0,
                "invalid_count": 0,
                "reviewed_count": 0,
                "invalid_review_count": 0,
            }
            records = client.get("/api/v1/proposals")
            assert records.status_code == 200
            assert len(records.json()["items"]) == 1
            assert records.json()["items"][0]["status"] == "approved"
            assert records.json()["items"][0]["input_valid"] is True


def test_review_sync_updates_proposal_and_public_board_joins_latest_revision() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        app = create_app(
            Settings(
                environment="test",
                database_url=f"sqlite:///{database.as_posix()}",
                auto_create_schema=True,
                execution_mode="fake",
                github_repository="example/repo",
                reviewer_github_logins=("science-reviewer",),
            )
        )
        app.dependency_overrides[current_active_user] = lambda: user("admin")
        submission = ProposalSubmission.model_validate(proposal_payload())
        review = ProposalReview.model_validate(review_payload())
        discussion = {
            "id": "D_kwDOReviewed",
            "number": 52,
            "url": "https://github.com/example/repo/discussions/52",
            "title": "[Task Proposal #52] Assimilate a sparse coastal observation network",
            "body": submission.render_discussion(),
            "category": {"name": "Task Proposals"},
            "author": {"login": "scientist"},
            "labels": {"nodes": []},
            "createdAt": "2026-09-01T00:00:00Z",
            "updatedAt": "2026-09-01T01:00:00Z",
            "comments": {
                "nodes": [
                    {
                        "id": "DC_kwDOReview",
                        "url": "https://github.com/example/repo/discussions/52#discussioncomment-1",
                        "body": review.render_comment(),
                        "author": {"login": "science-reviewer"},
                        "createdAt": "2026-09-01T00:30:00Z",
                        "updatedAt": "2026-09-01T00:30:00Z",
                    }
                ]
            },
        }
        with (
            TestClient(app) as client,
            patch("control_panel.community.github_access_token", AsyncMock(return_value="token")),
            patch("control_panel.community.fetch_all_discussions", AsyncMock(return_value=[discussion])),
        ):
            preview = client.post("/api/v1/proposals/reviews/preview", json=review_payload())
            assert preview.status_code == 200, preview.text
            assert preview.json()["comment"]["body"] == review.render_comment()

            synced = client.post("/api/v1/proposals/sync-discussions")
            assert synced.status_code == 200, synced.text
            assert synced.json()["reviewed_count"] == 1
            assert synced.json()["invalid_review_count"] == 0

            proposal = client.get("/api/v1/proposals").json()["items"][0]
            revision = client.post(
                "/api/v1/task-revisions",
                json={
                    "repo_url": "https://github.com/example/repo",
                    "commit_sha": "a" * 40,
                    "task_path": "tasks/earth-sciences/coastal-observation",
                    "resource_requirements": {"cpus": 4, "memory_mb": 8192},
                    "proposal_id": proposal["id"],
                    "pull_request_url": "https://github.com/example/repo/pull/7",
                    "release": "2026.1",
                },
            )
            assert revision.status_code == 201, revision.text

            board = client.get("/api/v1/public/proposals")
            assert board.status_code == 200, board.text
            item = board.json()["items"][0]
            assert item["field_name"] == "Coastal oceanography"
            assert item["status"] == "approved"
            assert item["review_short_description"] == review.review_short_description
            assert item["review_reviewer_login"] == "science-reviewer"
            assert item["revision_commit_sha"] == "a" * 40
            assert item["revision_task_path"] == "tasks/earth-sciences/coastal-observation"
            assert "disciplines" not in item
            assert "short_description" not in item
            assert "interdisciplinary" not in item
            assert "candidate" not in item


def test_invalid_authorized_review_is_recorded_but_does_not_approve_proposal() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "control.sqlite3"
        app = create_app(
            Settings(
                environment="test",
                database_url=f"sqlite:///{database.as_posix()}",
                auto_create_schema=True,
                execution_mode="fake",
                github_repository="example/repo",
                reviewer_github_logins=("science-reviewer",),
            )
        )
        app.dependency_overrides[current_active_user] = lambda: user("admin")
        submission = ProposalSubmission.model_validate(proposal_payload())
        discussion = {
            "id": "D_kwDOInvalidReview",
            "number": 53,
            "url": "https://github.com/example/repo/discussions/53",
            "title": "[Task Proposal #53] Assimilate a sparse coastal observation network",
            "body": submission.render_discussion(),
            "category": {"name": "Task Proposals"},
            "author": {"login": "scientist"},
            "labels": {"nodes": []},
            "createdAt": "2026-09-01T00:00:00Z",
            "updatedAt": "2026-09-01T01:00:00Z",
            "comments": {
                "nodes": [
                    {
                        "id": "DC_kwDOInvalid",
                        "url": "https://github.com/example/repo/discussions/53#discussioncomment-2",
                        "body": "<!-- ai4sbench-proposal-review:v1 -->\n\nDecision: approved",
                        "author": {"login": "science-reviewer"},
                        "createdAt": "2026-09-01T00:30:00Z",
                        "updatedAt": "2026-09-01T00:30:00Z",
                    }
                ]
            },
        }
        with (
            TestClient(app) as client,
            patch("control_panel.community.github_access_token", AsyncMock(return_value="token")),
            patch("control_panel.community.fetch_all_discussions", AsyncMock(return_value=[discussion])),
        ):
            synced = client.post("/api/v1/proposals/sync-discussions")
            assert synced.status_code == 200, synced.text
            assert synced.json()["reviewed_count"] == 0
            assert synced.json()["invalid_review_count"] == 1
            item = client.get("/api/v1/public/proposals").json()["items"][0]
            assert item["status"] == "pending"
            assert item["review_input_valid"] is False
            assert item["review_short_description"] is None


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
            assert 'ai4sbench_session=""' in response.headers["set-cookie"]


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
