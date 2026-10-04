from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.control_panel.providers import FakeEC2Provider
from src.control_panel.v2.models import HarborRunV2
from src.control_panel.v2.service import (
    HarborV2Runner,
    V2ConfigurationError,
    build_command,
    validate_ready,
    validate_revision,
)
from src.core.config import Settings
from src.core.identity import User, current_active_user
from src.db.models import TaskRevision
from src.main import create_app


def _admin() -> User:
    return User(
        id=uuid.uuid4(), email="admin@example.test", hashed_password="unused",
        is_active=True, is_superuser=False, is_verified=True, role="admin",
        github_login="v2-admin",
    )


def _result(completed: int = 1, errored: int = 0) -> dict:
    return {
        "n_total_trials": 1,
        "stats": {
            "n_completed_trials": completed, "n_errored_trials": errored,
            "n_running_trials": 0, "n_pending_trials": 0, "n_cancelled_trials": 0,
        },
    }


def _fake_cli(path: Path) -> None:
    path.write_text(
        """import json, os, pathlib, sys
args = sys.argv[1:]
assert os.environ.get('PYTHONIOENCODING') == 'utf-8'
assert args[0] == 'run' and args[args.index('-e') + 1] == 'ec2'
assert '--delete' in args and '--yes' in args
name = args[args.index('--job-name') + 1]
jobs = pathlib.Path(args[args.index('--jobs-dir') + 1])
kwargs = dict(arg.split('=', 1) for pos, arg in enumerate(args) if pos and args[pos - 1] == '--ek')
assert json.loads(kwargs['ami_id']) == 'ami-test'
assert json.loads(kwargs['instance_type']) == 't3.micro'
assert json.loads(kwargs['security_group_ids']) == ['sg-test']
job = jobs / name
job.mkdir(parents=True)
result = {'n_total_trials': 1, 'stats': {'n_completed_trials': 1, 'n_errored_trials': 0,
          'n_running_trials': 0, 'n_pending_trials': 0, 'n_cancelled_trials': 0}}
if '--fail-trial' in (pathlib.Path(args[args.index('-p') + 1]) / 'task.toml').read_text():
    result['stats']['n_completed_trials'] = 0
    result['stats']['n_errored_trials'] = 1
(job / 'result.json').write_text(json.dumps(result), encoding='utf-8')
print('fake harbor CLI completed •', flush=True)
""", encoding="utf-8"
    )


def test_v2_api_runner_e2e_and_scoped_docs() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        key = root / "ec2-key.pem"
        key.write_text("test only", encoding="utf-8")
        cli = root / "fake_harbor.py"
        _fake_cli(cli)
        settings = Settings(
            environment="test", allowed_hosts=("testserver",),
            database_url=f"sqlite:///{(root / 'db.sqlite').as_posix()}",
            auto_create_schema=True, execution_mode="fake", harbor_v2_enabled=True,
            harbor_v2_executable=sys.executable, harbor_v2_jobs_dir=root / "jobs",
            harbor_v2_ssh_key_path=key, harbor_v2_ec2_key_name="test-key",
            ec2_ami_id="ami-test", ec2_security_group_ids=("sg-test",),
            ec2_instance_type="t3.micro", ec2_allowed_instance_types=("t3.micro",),
        )
        app = create_app(settings, FakeEC2Provider())
        app.dependency_overrides[current_active_user] = _admin
        task = root / "task"
        task.mkdir()
        (task / "task.toml").write_text("test", encoding="utf-8")

        async def checkout(_revision: TaskRevision, _destination: Path) -> Path:
            return task

        with TestClient(app) as client:
            revision = client.post("/api/v1/task-revisions", json={
                "repo_url": "https://github.com/harbor-framework/harbor",
                "commit_sha": "a" * 40, "task_path": "tasks/example",
            })
            assert revision.status_code == 201, revision.text
            body = {"task_revision_id": revision.json()["id"], "agent": "oracle",
                    "instance_type": "t3.micro"}
            headers = {"Idempotency-Key": "v2-e2e"}
            created = client.post("/api/v2/control-panel/runs", json=body, headers=headers)
            assert created.status_code == 201, created.text
            run_id = created.json()["id"]
            repeated = client.post("/api/v2/control-panel/runs", json=body, headers=headers)
            assert repeated.status_code == 200
            assert repeated.json()["id"] == run_id
            conflict = client.post("/api/v2/control-panel/runs", json={**body, "agent": "other"},
                                   headers=headers)
            assert conflict.status_code == 409

            runner = HarborV2Runner(settings, app.state.session_factory,
                                    executable=(sys.executable, str(cli)), checkout=checkout)
            assert client.portal.call(runner.run_once)
            finished = client.get(f"/api/v2/control-panel/runs/{run_id}")
            assert finished.status_code == 200
            assert finished.json()["state"] == "succeeded", finished.text
            assert finished.json()["result"] == _result()
            assert "fake harbor CLI completed" in client.get(
                f"/api/v2/control-panel/runs/{run_id}/log"
            ).json()["text"]
            assert len(client.get("/api/v2/control-panel/runs").json()["items"]) == 1
            schema = client.get("/openapi/ec2/v2.json").json()
            assert set(schema["paths"]) == {
                "/api/v2/control-panel/runs", "/api/v2/control-panel/runs/{run_id}",
                "/api/v2/control-panel/runs/{run_id}/log",
            }
            assert client.get("/docs/ec2/v2").status_code == 200
            assert "/api/v2/control-panel/runs" not in client.get("/openapi/ec2/v1.json").json()["paths"]


def test_v2_fails_closed_on_errored_trial_and_recovers_stale_lease() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        key = root / "ec2-key.pem"
        key.write_text("test only", encoding="utf-8")
        cli = root / "fake_harbor.py"
        _fake_cli(cli)
        settings = Settings(
            environment="test", allowed_hosts=("testserver",),
            database_url=f"sqlite:///{(root / 'db.sqlite').as_posix()}",
            auto_create_schema=True, execution_mode="fake", harbor_v2_enabled=True,
            harbor_v2_executable=sys.executable, harbor_v2_jobs_dir=root / "jobs",
            harbor_v2_ssh_key_path=key, harbor_v2_ec2_key_name="test-key",
            ec2_ami_id="ami-test", ec2_security_group_ids=("sg-test",),
            ec2_instance_type="t3.micro", ec2_allowed_instance_types=("t3.micro",),
        )
        app = create_app(settings, FakeEC2Provider())
        app.dependency_overrides[current_active_user] = _admin
        task = root / "task"
        task.mkdir()
        (task / "task.toml").write_text("--fail-trial", encoding="utf-8")

        async def checkout(_revision: TaskRevision, _destination: Path) -> Path:
            return task

        async def make_stale(revision_id: str) -> str:
            async with app.state.session_factory() as session:
                stale = HarborRunV2(
                    task_revision_id=revision_id, config={"agent": "oracle", "model": None,
                        "n_attempts": 1, "n_concurrent": 1, "instance_type": "t3.micro"},
                    state="running", owner="dead-worker",
                    lease_until=datetime.now(UTC) - timedelta(minutes=1),
                )
                session.add(stale)
                await session.commit()
                return stale.id

        with TestClient(app) as client:
            revision = client.post("/api/v1/task-revisions", json={
                "repo_url": "https://github.com/harbor-framework/harbor",
                "commit_sha": "a" * 40, "task_path": "tasks/example",
            }).json()
            stale_id = client.portal.call(make_stale, revision["id"])
            queued = client.post("/api/v2/control-panel/runs", json={
                "task_revision_id": revision["id"], "agent": "oracle"})
            assert queued.status_code == 201, queued.text
            runner = HarborV2Runner(settings, app.state.session_factory,
                                    executable=(sys.executable, str(cli)), checkout=checkout)
            assert client.portal.call(runner.run_once)
            assert client.get(f"/api/v2/control-panel/runs/{stale_id}").json()["state"] == "interrupted"
            failed = client.get(f"/api/v2/control-panel/runs/{queued.json()['id']}").json()
            assert failed["state"] == "failed" and failed["exit_code"] == 0


def test_v2_rejects_unsafe_task_path() -> None:
    revision = TaskRevision(repo_url="https://github.com/harbor-framework/harbor",
                            commit_sha="a" * 40, task_path="tasks/../private")
    try:
        validate_revision(revision)
    except ValueError:
        pass
    else:
        raise AssertionError("Traversal task path was accepted")


def test_v2_api_checks_configuration_without_reading_runner_private_key() -> None:
    settings = Settings(
        environment="test", harbor_v2_enabled=True, harbor_v2_executable="missing-harbor",
        harbor_v2_ssh_key_path=Path("/run/secrets/harbor-ec2-key"),
        harbor_v2_ec2_key_name="test-key", ec2_ami_id="ami-test",
        ec2_security_group_ids=("sg-test",),
    )
    validate_ready(settings)
    try:
        validate_ready(settings, require_local_runtime=True)
    except V2ConfigurationError:
        pass
    else:
        raise AssertionError("Runner accepted a missing local private key")


def test_v2_command_parses_with_official_harbor_cli() -> None:
    harbor = shutil.which("harbor")
    if harbor is None:
        pytest.skip("Install the harbor-ec2 optional dependency to run the CLI smoke test")
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        scaffold = subprocess.run(
            [harbor, "init", "--task", "ai4sbench/v2-smoke", "--description", "CLI smoke task",
             "--author", "AI4SBench", "--output-dir", str(root)],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=60,
        )
        assert scaffold.returncode == 0, scaffold.stderr
        task = root / "v2-smoke"
        settings = Settings(
            environment="test", ec2_ami_id="ami-test", ec2_security_group_ids=("sg-test",),
            harbor_v2_ssh_key_path=root / "private.pem", harbor_v2_ec2_key_name="test-key",
            ec2_instance_type="t3.micro", ec2_allowed_instance_types=("t3.micro",),
            ec2_subnet_id="subnet-test", aws_region="us-east-1",
        )
        run = HarborRunV2(
            id="v2-cli-smoke", task_revision_id="unused",
            config={"agent": "oracle", "model": None, "n_attempts": 1,
                    "n_concurrent": 1, "instance_type": "t3.micro"},
        )
        command = build_command(settings, run, task, root / "jobs", executable=(harbor,))
        parsed = subprocess.run(
            [*command, "--print-config"], capture_output=True, text=True,
            encoding="utf-8", env=env, timeout=60,
        )
        assert parsed.returncode == 0, parsed.stderr
        config = json.loads(parsed.stdout)
        assert config["environment"]["type"] == "ec2"
        assert config["environment"]["kwargs"]["instance_type"] == "t3.micro"
        assert config["environment"]["kwargs"]["security_group_ids"] == ["sg-test"]
        assert config["tasks"] == [{"path": str(task)}]
