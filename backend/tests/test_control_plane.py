from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from control_panel.config import Settings
from control_panel.identity import User, current_active_user
from control_panel.job_runner import JobRunner
from control_panel.main import create_app
from control_panel.models import AuditEvent, DatabaseJob, Run
from control_panel.providers import FakeEC2Provider
from control_panel.quick_tunnel import extract_quick_tunnel_url
from control_panel.services import deterministic_bootstrap_token

JOB_SECRET = "test-job-secret-that-is-long-enough-456"


class FailingLaunchProvider(FakeEC2Provider):
    """A provider whose launch() always raises, to exercise retry exhaustion."""

    def launch(self, run_id: str, worker_token: str, config: dict) -> str:
        raise RuntimeError(
            "InvalidBlockDeviceMapping: Volume of size 30GB is smaller than snapshot "
            "'snap-example', expect size >= 40GB"
        )


async def control_state(factory, run_id: str) -> tuple[str, str, list[str], int]:
    async with factory() as session:
        stored = await session.get(Run, run_id)
        assert stored is not None
        jobs = list(await session.scalars(select(DatabaseJob)))
        audits = list(await session.scalars(select(AuditEvent)))
        return stored.state, stored.instance_state, [job.state for job in jobs], len(audits)


async def make_jobs_claimable_now(factory) -> None:
    async with factory() as session:
        for job in await session.scalars(select(DatabaseJob).where(DatabaseJob.state == "pending")):
            job.available_at = datetime.now(UTC)
        await session.commit()


async def release_active_runs(factory) -> int:
    async with factory() as session:
        active = list(await session.scalars(select(Run).where(Run.state == "provisioning")))
        for run in active:
            run.state = "succeeded"
            run.instance_state = "terminated"
        for job in await session.scalars(select(DatabaseJob).where(DatabaseJob.state == "pending")):
            job.available_at = datetime.now(UTC)
        await session.commit()
        return len(active)


async def expire_run(factory, run_id: str) -> None:
    async with factory() as session:
        stored = await session.get(Run, run_id)
        assert stored is not None
        stored.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()


async def run_state(factory, run_id: str) -> tuple[str, str]:
    async with factory() as session:
        stored = await session.get(Run, run_id)
        assert stored is not None
        return stored.state, stored.instance_state


async def sqlite_pragmas(factory) -> tuple[int, int, str]:
    async with factory() as session:
        assert isinstance(session, AsyncSession)
        foreign_keys = int((await session.execute(text("PRAGMA foreign_keys"))).scalar_one())
        busy_timeout = int((await session.execute(text("PRAGMA busy_timeout"))).scalar_one())
        journal_mode = str((await session.execute(text("PRAGMA journal_mode"))).scalar_one())
        return foreign_keys, busy_timeout, journal_mode


class ControlPanelIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database = Path(self.temp_dir.name) / "control.sqlite3"
        self.settings = Settings(
            environment="test",
            database_url=f"sqlite:///{database.as_posix()}",
            job_token_secret=JOB_SECRET,
            auto_create_schema=True,
            execution_mode="fake",
        )
        self.provider = FakeEC2Provider()
        self.app = create_app(self.settings, self.provider)
        self.app.dependency_overrides[current_active_user] = lambda: User(
            id=uuid.uuid4(),
            email="admin@example.test",
            hashed_password="not-used-in-test",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            role="admin",
            github_login="test-admin",
        )
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()
        self.headers: dict[str, str] = {}

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def create_approved_plan(self, agent: str = "oracle", model: str | None = None) -> dict:
        revision_response = self.client.post(
            "/api/v1/task-revisions",
            headers=self.headers,
            json={
                "repo_url": "https://github.com/harbor-framework/terminal-bench-science",
                "commit_sha": "1" * 40,
                "task_path": "tasks/mathematical-sciences/applied-mathematics/amr-poisson-optimize",
                "resource_requirements": {"cpus": 4, "memory_mb": 4096, "storage_mb": 10240},
            },
        )
        self.assertEqual(revision_response.status_code, 201, revision_response.text)
        revision = revision_response.json()
        plan_response = self.client.post(
            "/api/v1/plans",
            headers=self.headers,
            json={
                "task_revision_id": revision["id"],
                "config": {
                    "agent": agent,
                    "model": model,
                    "n_concurrent": 1,
                    "n_attempts": 1,
                    "environment": "docker",
                },
            },
        )
        self.assertEqual(plan_response.status_code, 201, plan_response.text)
        plan = plan_response.json()
        approved_response = self.client.post(
            f"/api/v1/plans/{plan['id']}/approve",
            headers=self.headers,
            json={"lock_version": plan["lock_version"]},
        )
        self.assertEqual(approved_response.status_code, 200, approved_response.text)
        return approved_response.json()

    def test_admin_endpoints_require_github_administrator(self) -> None:
        self.app.dependency_overrides.pop(current_active_user)
        self.assertEqual(self.client.get("/api/v1/dashboard").status_code, 401)
        self.assertEqual(self.client.get("/health/live").status_code, 200)
        self.app.dependency_overrides[current_active_user] = lambda: User(
            id=uuid.uuid4(),
            email="admin@example.test",
            hashed_password="not-used-in-test",
            is_active=True,
            is_superuser=False,
            is_verified=True,
            role="admin",
            github_login="test-admin",
        )

    def test_runtime_uses_one_configured_async_sqlite_engine(self) -> None:
        self.assertIsInstance(self.app.state.engine, AsyncEngine)
        self.assertFalse(hasattr(self.app.state, "auth_engine"))
        self.assertFalse(hasattr(self.app.state, "auth_session_factory"))
        self.assertEqual(
            self.client.portal.call(sqlite_pragmas, self.app.state.session_factory),
            (1, 30_000, "wal"),
        )

    def test_swagger_and_openapi_are_available_in_production(self) -> None:
        production = create_app(
            Settings(
                environment="production",
                allowed_hosts=("testserver",),
                database_url="sqlite:///:memory:",
                auto_create_schema=False,
                auth_jwt_secret="production-auth-jwt-secret-that-is-long-enough",
                job_token_secret="production-job-token-secret-that-is-long-enough",
                github_oauth_client_id="test-client-id",
                github_oauth_client_secret="test-client-secret",
                execution_mode="ec2",
                ec2_ami_id="ami-0123456789abcdef0",
                ec2_security_group_ids=("sg-0123456789abcdef0",),
            ),
            FakeEC2Provider(),
        )
        with TestClient(production) as client:
            self.assertEqual(client.get("/docs").status_code, 200)
            schema = client.get("/openapi.json")
            self.assertEqual(schema.status_code, 200)
            payload = schema.json()
            self.assertEqual(payload["info"]["title"], "AI4S-Bench Control Plane API")
            self.assertIn("community", {tag["name"] for tag in payload["tags"]})

    def test_website_cors_preflight_allows_proposal_updates(self) -> None:
        cors_app = create_app(
            Settings(
                environment="test",
                database_url="sqlite:///:memory:",
                auto_create_schema=True,
                execution_mode="fake",
                cors_origins=("https://ai4sbench.org",),
            )
        )
        with TestClient(cors_app) as client:
            response = client.options(
                "/api/v1/proposals/example-proposal-id",
                headers={
                    "Origin": "https://ai4sbench.org",
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["access-control-allow-origin"], "https://ai4sbench.org")
        self.assertIn("PUT", response.headers["access-control-allow-methods"])
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")

    def test_website_dist_is_served_beneath_dashboard(self) -> None:
        redirect = self.client.get("/website", follow_redirects=False)
        self.assertEqual(redirect.status_code, 307)
        self.assertEqual(redirect.headers["location"], "/website/")

        index = self.client.get("/website/")
        self.assertEqual(index.status_code, 200)
        self.assertIn("AI4S-Benchmark", index.text)

        site_config = self.client.get("/website/data/site.json")
        self.assertEqual(site_config.status_code, 200)
        self.assertEqual(site_config.json()["control_plane_url"], "https://dashboard.ai4sbench.org")

        reviewer_form = self.client.get("/website/reviewers/")
        self.assertEqual(reviewer_form.status_code, 200)
        self.assertIn("20260910-reviewer-intake", reviewer_form.text)

    def test_every_json_api_route_declares_a_response_schema(self) -> None:
        excluded_binary_route = "/api/v1/database-snapshots/{name}/download"
        undocumented = [
            route.path
            for route in self.app.routes
            if isinstance(route, APIRoute)
            and route.path.startswith("/api/")
            and route.path != excluded_binary_route
            and route.response_model is None
        ]
        self.assertEqual(undocumented, [])

        openapi = self.client.get("/openapi.json").json()
        proposal_preview = openapi["paths"]["/api/v1/proposals/preview"]["post"]
        self.assertEqual(
            proposal_preview["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ProposalPreviewResponse",
        )
        proposal_board = openapi["paths"]["/api/v1/public/proposals"]["get"]
        self.assertEqual(
            proposal_board["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ProposalBoardListResponse",
        )
        proposal_detail = openapi["paths"]["/api/v1/proposals/{proposal_id}"]["get"]
        self.assertEqual(
            proposal_detail["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ProposalEditDetailResponse",
        )
        proposal_update = openapi["paths"]["/api/v1/proposals/{proposal_id}"]["put"]
        self.assertEqual(
            proposal_update["requestBody"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ProposalSubmission",
        )
        self.assertEqual(
            proposal_update["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ProposalUpdatedResponse",
        )
        review_preview = openapi["paths"]["/api/v1/proposals/reviews/preview"]["post"]
        self.assertEqual(
            review_preview["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ProposalReviewPreviewResponse",
        )
        review_publish = openapi["paths"]["/api/v1/proposals/{proposal_id}/reviews"]["post"]
        self.assertEqual(
            review_publish["responses"]["201"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ProposalReviewPublishedResponse",
        )
        task_revisions = openapi["paths"]["/api/v1/task-revisions"]["get"]
        self.assertEqual(
            task_revisions["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/TaskRevisionListResponse",
        )

    def test_admin_can_create_list_and_download_a_sqlite_snapshot(self) -> None:
        created = self.client.post("/api/v1/database-snapshots")
        self.assertEqual(created.status_code, 201, created.text)
        snapshot = created.json()
        self.assertRegex(snapshot["name"], r"^ai4sbench-control-panel-.*\.sqlite3$")
        self.assertGreater(snapshot["size_bytes"], 0)

        listed = self.client.get("/api/v1/database-snapshots")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual([item["name"] for item in listed.json()["items"]], [snapshot["name"]])

        downloaded = self.client.get(f"/api/v1/database-snapshots/{snapshot['name']}/download")
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.headers["content-type"], "application/vnd.sqlite3")
        self.assertTrue(downloaded.content.startswith(b"SQLite format 3\x00"))
        self.assertEqual(
            self.client.get("/api/v1/database-snapshots/../../control.sqlite3/download").status_code,
            404,
        )

    def test_full_database_queue_worker_and_termination_flow(self) -> None:
        plan = self.create_approved_plan()
        idempotency_key = str(uuid.uuid4())
        response = self.client.post(
            "/api/v1/runs",
            headers={**self.headers, "Idempotency-Key": idempotency_key},
            json={"plan_id": plan["id"], "timeout_minutes": 180},
        )
        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()
        self.assertEqual(run["state"], "queued")
        self.assertTrue(run["created"])

        repeated = self.client.post(
            "/api/v1/runs",
            headers={**self.headers, "Idempotency-Key": idempotency_key},
            json={"plan_id": plan["id"], "timeout_minutes": 180},
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["id"], run["id"])
        self.assertFalse(repeated.json()["created"])

        runner = JobRunner(self.settings, self.provider)
        self.assertTrue(self.client.portal.call(runner.process_one))
        token = deterministic_bootstrap_token(run["id"], self.settings)
        claim = self.client.post(
            f"/api/v1/worker/runs/{run['id']}/claim",
            json={"token": token},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        session_token = claim.json()["session_token"]
        self.assertEqual(
            self.client.post(f"/api/v1/worker/runs/{run['id']}/claim", json={"token": token}).status_code,
            403,
        )

        event = self.client.post(
            f"/api/v1/worker/runs/{run['id']}/events",
            json={
                "session_token": session_token,
                "event_type": "runner_log",
                "message": "real harbor output",
                "payload": {},
            },
        )
        self.assertEqual(event.status_code, 201, event.text)
        completed = self.client.post(
            f"/api/v1/worker/runs/{run['id']}/complete",
            json={
                "session_token": session_token,
                "state": "succeeded",
                "result": {"exit_code": 0, "reward": 1.0},
            },
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["instance_state"], "terminating")
        self.assertTrue(self.client.portal.call(runner.process_one))

        state, instance_state, jobs, audit_count = self.client.portal.call(
            control_state, self.app.state.session_factory, run["id"]
        )
        self.assertEqual(state, "succeeded")
        self.assertEqual(instance_state, "terminated")
        self.assertEqual(jobs, ["completed", "completed"])
        self.assertGreaterEqual(audit_count, 3)
        self.client.portal.call(runner.engine.dispose)

    def test_worker_lifecycle_records_the_image_version_and_full_harbor_output(self) -> None:
        """Cover the baked-image run path end to end against the fake provider.

        Enqueue, launch, claim, stream `[harbor]` output, complete, terminate.
        The claim must record which worker image ran, and the completion payload
        must carry the whole Harbor log so the Dashboard can show it even if
        individual real-time events were lost.
        """
        plan = self.create_approved_plan()
        response = self.client.post(
            "/api/v1/runs",
            headers={**self.headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"plan_id": plan["id"], "timeout_minutes": 180},
        )
        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()

        runner = JobRunner(self.settings, self.provider)
        self.assertTrue(self.client.portal.call(runner.process_one))
        claim = self.client.post(
            f"/api/v1/worker/runs/{run['id']}/claim",
            json={"token": deterministic_bootstrap_token(run["id"], self.settings)},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        session_token = claim.json()["session_token"]

        harbor_lines = [
            "[harbor] $ harbor run -p tasks/example -a oracle",
            "[harbor] stdin=disabled",
            "[harbor] trial 1 started",
            "[harbor] trial 1 completed",
            "[harbor] exit_code=0",
        ]
        for line in harbor_lines:
            event = self.client.post(
                f"/api/v1/worker/runs/{run['id']}/events",
                json={
                    "session_token": session_token,
                    "event_type": "runner_log",
                    "message": line,
                    "payload": {},
                },
            )
            self.assertEqual(event.status_code, 201, event.text)

        output = "\n".join(harbor_lines)
        completed = self.client.post(
            f"/api/v1/worker/runs/{run['id']}/complete",
            json={
                "session_token": session_token,
                "state": "succeeded",
                "result": {
                    "exit_code": 0,
                    "runner": "harbor",
                    "job_name": f"run-{run['id']}",
                    "harbor_output": output,
                    "harbor_output_bytes": len(output.encode("utf-8")),
                    "harbor": {
                        "n_total_trials": 1,
                        "stats": {
                            "n_completed_trials": 1,
                            "n_errored_trials": 0,
                            "n_running_trials": 0,
                            "n_pending_trials": 0,
                            "n_cancelled_trials": 0,
                        },
                    },
                },
            },
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["result"]["harbor_output"], output)
        self.assertTrue(self.client.portal.call(runner.process_one))

        events = self.client.get(f"/api/v1/runs/{run['id']}/events", headers=self.headers)
        self.assertEqual(events.status_code, 200, events.text)
        items = events.json()["items"]
        logs = [item["message"] for item in items if item["event_type"] == "runner_log"]
        self.assertEqual(logs, harbor_lines)
        self.assertTrue(all(line.startswith("[harbor]") for line in logs))

        claimed = next(item for item in items if item["event_type"] == "worker_claimed")
        self.assertEqual(claimed["payload"]["bootstrap_mode"], self.settings.ec2_bootstrap_mode)

        state, instance_state, _, _ = self.client.portal.call(
            control_state, self.app.state.session_factory, run["id"]
        )
        self.assertEqual((state, instance_state), ("succeeded", "terminated"))
        self.assertEqual(self.provider.describe(self.provider.run_instances[run["id"]]), "terminated")
        self.client.portal.call(runner.engine.dispose)

    def test_queue_accepts_runs_beyond_active_worker_limit(self) -> None:
        plan = self.create_approved_plan()
        first = self.client.post(
            "/api/v1/runs",
            headers={**self.headers, "Idempotency-Key": "first"},
            json={"plan_id": plan["id"]},
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            "/api/v1/runs",
            headers={**self.headers, "Idempotency-Key": "second"},
            json={"plan_id": plan["id"]},
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["state"], "queued")

    def test_manual_run_creates_an_approved_snapshot_and_uses_selected_instance(self) -> None:
        revision_response = self.client.post(
            "/api/v1/task-revisions",
            headers=self.headers,
            json={
                "repo_url": "https://github.com/harbor-framework/terminal-bench-science",
                "commit_sha": "3" * 40,
                "task_path": "tasks/mathematical-sciences/applied-mathematics/amr-poisson-optimize",
            },
        )
        revision = revision_response.json()
        response = self.client.post(
            "/api/v1/runs/manual",
            headers={**self.headers, "Idempotency-Key": "manual-console-test"},
            json={
                "task_revision_id": revision["id"],
                "timeout_minutes": 240,
                "config": {
                    "agent": "oracle",
                    "n_concurrent": 1,
                    "n_attempts": 1,
                    "environment": "docker",
                    "instance_type": "t3.micro",
                    "root_volume_gb": 30,
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        run = response.json()
        self.assertTrue(run["created"])
        self.assertEqual(run["state"], "queued")
        self.assertEqual(run["config"]["launch_source"], "manual_console")
        self.assertEqual(run["config"]["plan_config"]["instance_type"], "t3.micro")

        runner = JobRunner(self.settings, self.provider)
        self.assertTrue(self.client.portal.call(runner.process_one))
        self.assertEqual(self.provider.launch_configs[run["id"]]["instance_type"], "t3.micro")
        self.client.portal.call(runner.engine.dispose)

    def test_manual_run_rejects_an_instance_that_cannot_run_the_task(self) -> None:
        revision = self.client.post(
            "/api/v1/task-revisions",
            headers=self.headers,
            json={
                "repo_url": "https://github.com/harbor-framework/terminal-bench-science",
                "commit_sha": "4" * 40,
                "task_path": "tasks/mathematical-sciences/applied-mathematics/amr-poisson-optimize",
                "resource_requirements": {"cpus": 4, "memory_mb": 4096},
            },
        ).json()
        response = self.client.post(
            "/api/v1/runs/manual",
            headers={**self.headers, "Idempotency-Key": "reject-small-instance"},
            json={
                "task_revision_id": revision["id"],
                "config": {"agent": "oracle", "instance_type": "t3.micro"},
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("requires 4 vCPU", response.json()["detail"])

    def test_oracle_batch_queues_every_task_and_respects_concurrent_worker_limit(self) -> None:
        self.settings.max_active_runs = 3
        revision_ids: list[str] = []
        for index in range(5):
            revision = self.client.post(
                "/api/v1/task-revisions",
                headers=self.headers,
                json={
                    "repo_url": "https://github.com/harbor-framework/terminal-bench-science",
                    "commit_sha": f"{index + 7:x}" * 40,
                    "task_path": f"tasks/batch/task-{index}",
                    "resource_requirements": {
                        "cpus": 1,
                        "memory_mb": 1024,
                        "storage_mb": 51200 if index == 0 else 1024,
                    },
                },
            ).json()
            revision_ids.append(revision["id"])

        response = self.client.post(
            "/api/v1/runs/manual-batch",
            headers={**self.headers, "Idempotency-Key": "all-oracles-once"},
            json={
                "task_revision_ids": revision_ids,
                "timeout_minutes": 60,
                "config": {"agent": "oracle", "n_attempts": 1, "environment": "docker"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["created_count"], 5)
        self.assertEqual(response.json()["items"][0]["config"]["plan_config"]["root_volume_gb"], 50)

        runner = JobRunner(self.settings, self.provider)
        for _ in range(5):
            self.assertTrue(self.client.portal.call(runner.process_one))
        self.assertEqual(len(self.provider.launch_configs), 3)
        active_count = self.client.portal.call(release_active_runs, self.app.state.session_factory)
        self.assertEqual(active_count, 3)

        for _ in range(2):
            self.assertTrue(self.client.portal.call(runner.process_one))
        self.assertEqual(len(self.provider.launch_configs), 5)
        self.assertTrue(all(config["agent"] == "oracle" for config in self.provider.launch_configs.values()))
        self.client.portal.call(runner.engine.dispose)

    def test_repository_sync_imports_every_task_at_one_pinned_commit(self) -> None:
        source_result = (
            {"commit_sha": "5" * 40, "task_count": 2},
            [
                {
                    "repo_url": "https://github.com/harbor-framework/terminal-bench-science",
                    "commit_sha": "5" * 40,
                    "task_path": "tasks/earth-sciences/example-one",
                    "resource_requirements": {"cpus": 1, "memory_mb": 2048},
                },
                {
                    "repo_url": "https://github.com/harbor-framework/terminal-bench-science",
                    "commit_sha": "5" * 40,
                    "task_path": "tasks/life-sciences/example-two",
                    "resource_requirements": {"cpus": 2, "memory_mb": 4096},
                },
            ],
        )
        with patch("control_panel.api.GitHubTaskSource") as source_class:
            source_class.return_value.sync_repository = AsyncMock(return_value=source_result)
            response = self.client.post(
                "/api/v1/task-revisions/sync-repository",
                headers=self.headers,
                json={
                    "repo_url": "https://github.com/harbor-framework/terminal-bench-science",
                    "ref": "main",
                },
            )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["commit_sha"], "5" * 40)
        self.assertEqual(body["task_count"], 2)
        self.assertEqual(body["created_count"], 2)
        self.assertEqual(
            {item["task_path"] for item in body["items"]},
            {
                "tasks/earth-sciences/example-one",
                "tasks/life-sciences/example-two",
            },
        )

    def test_codex_plan_requires_model(self) -> None:
        revision = self.client.post(
            "/api/v1/task-revisions",
            headers=self.headers,
            json={
                "repo_url": "https://github.com/example/repo",
                "commit_sha": "2" * 40,
                "task_path": "tasks/demo",
            },
        ).json()
        response = self.client.post(
            "/api/v1/plans",
            headers=self.headers,
            json={"task_revision_id": revision["id"], "config": {"agent": "codex"}},
        )
        self.assertEqual(response.status_code, 422)

    def test_public_settings_do_not_leak_secrets(self) -> None:
        response = self.client.get("/api/v1/settings", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("job_token_secret", body)
        self.assertNotIn("github_token", body)
        self.assertNotIn("git_https_proxy", body)

    def test_quick_tunnel_url_parser(self) -> None:
        self.assertIsNone(extract_quick_tunnel_url("connector starting"))
        self.assertEqual(
            extract_quick_tunnel_url("https://quiet-river.trycloudflare.com ready"),
            "https://quiet-river.trycloudflare.com",
        )

    def test_a_launch_failure_records_the_real_ec2_error_on_every_attempt(self) -> None:
        """A launch failure happens before any worker exists, so the run's
        event history is the only record of what went wrong. Each retry's
        real error must be visible, not just a generic message after the
        final one is exhausted.
        """
        plan = self.create_approved_plan()
        response = self.client.post(
            "/api/v1/runs",
            headers={**self.headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"plan_id": plan["id"], "timeout_minutes": 180},
        )
        run_id = response.json()["id"]

        failing_provider = FailingLaunchProvider()
        runner = JobRunner(self.settings, failing_provider)
        for _ in range(5):
            self.assertTrue(self.client.portal.call(runner.process_one))
            # Force the next attempt to be immediately claimable instead of
            # waiting out the retry backoff.
            self.client.portal.call(make_jobs_claimable_now, self.app.state.session_factory)

        events = self.client.get(f"/api/v1/runs/{run_id}/events", headers=self.headers).json()["items"]
        attempt_events = [item for item in events if item["event_type"] == "launch_attempt_failed"]
        final_event = next(item for item in events if item["event_type"] == "launch_failed")

        self.assertGreaterEqual(len(attempt_events), 4)
        for item in attempt_events:
            self.assertIn("InvalidBlockDeviceMapping", item["payload"]["error"])
            self.assertIn("Volume of size 30GB", item["message"])
        self.assertIn("InvalidBlockDeviceMapping", final_event["payload"]["error"])

        state, instance_state, _jobs, _audits = self.client.portal.call(
            control_state, self.app.state.session_factory, run_id
        )
        self.assertEqual(state, "failed")
        self.assertEqual(instance_state, "launch_failed")
        self.client.portal.call(runner.engine.dispose)

    def test_reconciler_times_out_committed_run(self) -> None:
        plan = self.create_approved_plan()
        response = self.client.post(
            "/api/v1/runs",
            headers={**self.headers, "Idempotency-Key": "timeout-run"},
            json={"plan_id": plan["id"], "timeout_minutes": 1},
        )
        run_id = response.json()["id"]
        self.client.portal.call(expire_run, self.app.state.session_factory, run_id)
        runner = JobRunner(self.settings, self.provider)
        self.client.portal.call(runner.run_once)
        state, instance_state = self.client.portal.call(run_state, self.app.state.session_factory, run_id)
        self.assertEqual(state, "timed_out")
        self.assertEqual(instance_state, "terminated")
        self.client.portal.call(runner.engine.dispose)

    def test_production_settings_fail_closed(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                environment="production",
                auto_create_schema=False,
                execution_mode="ec2",
                job_token_secret="long-enough-job-token-secret-for-test",
                auth_jwt_secret="short",
                github_oauth_client_id="client-id",
                github_oauth_client_secret="long-enough-github-client-secret-for-test",
                ec2_ami_id="ami-0123456789abcdef0",
                ec2_security_group_ids=("sg-0123456789abcdef0",),
            )


if __name__ == "__main__":
    unittest.main()
