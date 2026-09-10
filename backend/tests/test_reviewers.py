from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from control_panel.config import Settings
from control_panel.identity import User, current_active_user, current_optional_user
from control_panel.main import create_app


def make_user(role: str, github_login: str) -> User:
    return User(
        id=uuid4(),
        email=f"{github_login}@example.test",
        hashed_password="not-used-in-this-test",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        role=role,
        github_login=github_login,
    )


def reviewer_payload() -> dict[str, object]:
    return {
        "schema_version": "tb-reviewer-application/v1",
        "name": "Ada Reviewer",
        "affiliation": "Example Materials Institute",
        "email": "Ada.Reviewer@example.test",
        "github": None,
        "role": "Principal research scientist",
        "domains": ["materials-science", "ai-ml"],
        "domains_display": ["Materials Science", "AI / ML"],
        "field": "machine-learning-potentials",
        "subfield": "Machine-learning potentials",
        "research_background": (
            "I develop and validate machine-learning interatomic potentials, with experience "
            "reviewing reproducibility, evaluation protocols, and scientific software artifacts."
        ),
    }


def test_reviewer_application_submission_admin_decision_and_access() -> None:
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
        admin = make_user("admin", "test-admin")
        applicant = make_user("member", "approved-reviewer")
        active = {"user": admin}
        app.dependency_overrides[current_active_user] = lambda: active["user"]
        app.dependency_overrides[current_optional_user] = lambda: applicant

        with TestClient(app) as client:
            created = client.post("/api/v1/reviewers", json=reviewer_payload())
            assert created.status_code == 201, created.text
            assert created.json()["status"] == "pending"
            application_id = created.json()["id"]

            listed = client.get("/api/v1/reviewer-applications")
            assert listed.status_code == 200, listed.text
            application = listed.json()["items"][0]
            assert application["id"] == application_id
            assert application["email"] == "ada.reviewer@example.test"
            assert application["submitted_by_login"] == "approved-reviewer"
            assert application["domains_display"] == ["Materials Science", "AI / ML"]

            approved = client.patch(
                f"/api/v1/reviewer-applications/{application_id}",
                json={
                    "status": "approved",
                    "github": "@Approved-Reviewer",
                    "admin_notes": "Expertise verified against public research profile.",
                },
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["github"] == "approved-reviewer"
            assert approved.json()["reviewed_by"] == "github:test-admin"
            assert approved.json()["reviewed_at"] is not None

            active["user"] = applicant
            signed_in = client.get("/api/v1/auth/me")
            assert signed_in.status_code == 200, signed_in.text
            assert signed_in.json()["can_review"] is True

            active["user"] = make_user("member", "unapproved-reviewer")
            assert client.get("/api/v1/auth/me").json()["can_review"] is False


def test_reviewer_application_validation_and_admin_errors() -> None:
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
        app.dependency_overrides[current_active_user] = lambda: make_user("admin", "test-admin")
        app.dependency_overrides[current_optional_user] = lambda: None

        with TestClient(app) as client:
            invalid = reviewer_payload()
            invalid["domains"] = ["chemistry"]
            rejected = client.post("/api/v1/reviewers", json=invalid)
            assert rejected.status_code == 422

            empty_patch = client.patch(
                f"/api/v1/reviewer-applications/{uuid4()}",
                json={},
            )
            assert empty_patch.status_code == 422

            missing = client.get(f"/api/v1/reviewer-applications/{uuid4()}")
            assert missing.status_code == 404

            schema = client.get("/openapi.json").json()
            create_operation = schema["paths"]["/api/v1/reviewers"]["post"]
            assert create_operation["requestBody"]["content"]["application/json"]["schema"]["$ref"] == (
                "#/components/schemas/ReviewerApplicationSubmission"
            )
            admin_list = schema["paths"]["/api/v1/reviewer-applications"]["get"]
            assert admin_list["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == (
                "#/components/schemas/ReviewerApplicationListResponse"
            )
