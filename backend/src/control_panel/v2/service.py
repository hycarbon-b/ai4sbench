"""Run Harbor's supported EC2 environment through its CLI.

Harbor owns EC2 launch, SSH, Docker Compose, and termination. This module only
stages an immutable task, invokes the CLI, and records its result.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import socket
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.control_panel.github_source import GITHUB_REPO
from src.control_panel.harbor_result import terminal_state
from src.control_panel.v2.models import HarborRunV2
from src.control_panel.v2.schemas import HarborRunCreate
from src.core.config import Settings
from src.db.database import begin_immediate
from src.db.models import TaskRevision

COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$", re.IGNORECASE)
LOGGER = logging.getLogger(__name__)


class V2ConfigurationError(ValueError):
    pass


def validate_ready(settings: Settings, *, require_local_runtime: bool = False) -> None:
    if not settings.harbor_v2_enabled:
        raise V2ConfigurationError("Harbor EC2 v2 is disabled")
    if not settings.ec2_ami_id or not settings.ec2_security_group_ids:
        raise V2ConfigurationError("Harbor EC2 v2 requires an AMI and security group")
    if not settings.harbor_v2_ec2_key_name:
        raise V2ConfigurationError("Harbor EC2 v2 requires an EC2 key pair name")
    key_path = settings.harbor_v2_ssh_key_path
    if key_path is None:
        raise V2ConfigurationError("Harbor EC2 v2 requires an SSH private key path")
    if require_local_runtime:
        if not key_path.expanduser().is_file():
            raise V2ConfigurationError("Harbor EC2 v2 requires an existing SSH private key")
        if not shutil.which(settings.harbor_v2_executable):
            raise V2ConfigurationError("Harbor CLI executable was not found")
        if not shutil.which("ssh"):
            raise V2ConfigurationError("OpenSSH client was not found")


def validate_revision(revision: TaskRevision) -> None:
    if not GITHUB_REPO.fullmatch(revision.repo_url):
        raise ValueError("Task revision must use a canonical GitHub HTTPS repository URL")
    if not COMMIT_RE.fullmatch(revision.commit_sha):
        raise ValueError("Task revision must have an immutable commit SHA")
    raw_path = revision.task_path
    parts = raw_path.split("/")
    if raw_path != "." and (
        not raw_path or raw_path.startswith("/") or "\\" in raw_path
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("Task revision path must be safe and relative")


def build_command(
    settings: Settings,
    run: HarborRunV2,
    task_dir: Path,
    jobs_dir: Path,
    *,
    executable: tuple[str, ...] | None = None,
) -> list[str]:
    """Build only flags supported by Harbor 0.20.0's `harbor run` command."""
    config = run.config
    command = [
        *(executable or (settings.harbor_v2_executable,)),
        "run", "-p", str(task_dir), "-a", str(config["agent"]),
        "-k", str(config["n_attempts"]),
        "--n-concurrent", str(config["n_concurrent"]),
        "-e", "ec2", "--job-name", run.id, "--jobs-dir", str(jobs_dir),
        "--yes", "--delete",
    ]
    if config.get("model"):
        command += ["-m", str(config["model"])]
    kwargs: dict[str, object] = {
        "region": settings.aws_region,
        "ami_id": settings.ec2_ami_id,
        "instance_type": config["instance_type"],
        "security_group_ids": list(settings.ec2_security_group_ids),
        "key_name": settings.harbor_v2_ec2_key_name,
        "ssh_key_path": str(settings.harbor_v2_ssh_key_path.expanduser().resolve()),
        "ssh_user": settings.harbor_v2_ssh_user,
        "use_public_ip": settings.ec2_associate_public_ip,
        "root_volume_size_gb": settings.ec2_root_volume_gb,
        "bootstrap_docker": settings.harbor_v2_bootstrap_docker,
        "tags": {"ai4sbench:managed": "true", "ai4sbench:v2-run": run.id},
    }
    if settings.ec2_subnet_id:
        kwargs["subnet_id"] = settings.ec2_subnet_id
    if settings.ec2_instance_profile_arn:
        kwargs["iam_instance_profile"] = settings.ec2_instance_profile_arn
    for key, value in kwargs.items():
        command += ["--ek", f"{key}={json.dumps(value, separators=(',', ':'))}"]
    return command


async def enqueue_run(
    session: AsyncSession,
    settings: Settings,
    body: HarborRunCreate,
    idempotency_key: str | None,
) -> tuple[HarborRunV2, bool]:
    validate_ready(settings)
    if idempotency_key and len(idempotency_key) > 200:
        raise ValueError("Idempotency-Key is too long")
    await begin_immediate(session)
    config = body.model_dump()
    config.pop("task_revision_id")
    config["instance_type"] = body.instance_type or settings.ec2_instance_type
    if config["instance_type"] not in settings.ec2_allowed_instance_types:
        raise ValueError("Instance type is not allowed")
    if idempotency_key:
        existing = await session.scalar(
            select(HarborRunV2).where(HarborRunV2.idempotency_key == idempotency_key)
        )
        if existing:
            if existing.task_revision_id != body.task_revision_id or existing.config != config:
                raise ValueError("Idempotency-Key was already used for different inputs")
            await session.commit()
            return existing, False
    revision = await session.get(TaskRevision, body.task_revision_id)
    if revision is None:
        raise LookupError("Task revision not found")
    validate_revision(revision)
    run = HarborRunV2(task_revision_id=revision.id, idempotency_key=idempotency_key, config=config)
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run, True


def run_dict(run: HarborRunV2) -> dict[str, object]:
    return {
        "id": run.id,
        "task_revision_id": run.task_revision_id,
        "state": run.state,
        "config": run.config,
        "job_path": run.job_path,
        "exit_code": run.exit_code,
        "result": run.result,
        "error": run.error,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


async def stage_task(revision: TaskRevision, source_dir: Path) -> Path:
    """Check out the recorded Git commit before giving Harbor a local task path."""
    validate_revision(revision)
    source_dir.parent.mkdir(parents=True, exist_ok=True)

    async def git(*args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError("Git checkout timed out after 600 seconds") from None
        if process.returncode:
            raise RuntimeError(f"Git checkout failed: {stderr.decode(errors='replace')[-1000:]}")
        return stdout.decode(errors="replace").strip()

    await git("clone", "--filter=blob:none", "--no-checkout", revision.repo_url, str(source_dir))
    await git("-C", str(source_dir), "fetch", "--depth", "1", "origin", revision.commit_sha)
    await git("-C", str(source_dir), "checkout", "--detach", "FETCH_HEAD")
    if (await git("-C", str(source_dir), "rev-parse", "HEAD")).lower() != revision.commit_sha.lower():
        raise RuntimeError("Checked-out commit differs from the registered task revision")
    task_dir = (source_dir / revision.task_path).resolve()
    if not task_dir.is_relative_to(source_dir.resolve()) or not (task_dir / "task.toml").is_file():
        raise RuntimeError("Registered task path is missing from the checked-out commit")
    from harbor.models.task.task import Task

    if not Task.is_valid_dir(task_dir):
        raise RuntimeError("Registered task is not valid for the pinned Harbor CLI version")
    return task_dir


class HarborV2Runner:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        *,
        executable: tuple[str, ...] | None = None,
        checkout: Callable[[TaskRevision, Path], Awaitable[Path]] = stage_task,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.executable = executable
        self.checkout = checkout
        self.owner = f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"

    async def run_once(self) -> bool:
        now = datetime.now(UTC)
        async with self.sessions() as session:
            await begin_immediate(session)
            stale = list(
                await session.scalars(
                    select(HarborRunV2).where(
                        HarborRunV2.state == "running", HarborRunV2.lease_until < now
                    )
                )
            )
            for item in stale:
                item.state = "interrupted"
                item.interrupted = True
                item.error = "Harbor runner lease expired; inspect tagged EC2 instances before retrying"
                item.finished_at = now
                item.owner = None
                item.lease_until = None
            run = await session.scalar(
                select(HarborRunV2).where(HarborRunV2.state == "queued")
                .order_by(HarborRunV2.created_at).limit(1)
            )
            if run is None:
                await session.commit()
                return bool(stale)
            run.state = "running"
            run.started_at = now
            run.owner = self.owner
            run.lease_until = now + timedelta(seconds=self.settings.harbor_v2_lease_seconds)
            run_id = run.id
            await session.commit()
        await self._execute(run_id)
        return True

    async def _renew(self, run_id: str) -> None:
        async with self.sessions() as session:
            run = await session.get(HarborRunV2, run_id)
            if run and run.state == "running" and run.owner == self.owner:
                run.lease_until = datetime.now(UTC) + timedelta(
                    seconds=self.settings.harbor_v2_lease_seconds
                )
                await session.commit()

    async def _heartbeat(self, run_id: str) -> None:
        while True:
            await asyncio.sleep(self.settings.harbor_v2_lease_seconds / 3)
            try:
                await self._renew(run_id)
            except Exception:
                LOGGER.exception("Could not renew Harbor v2 lease for %s", run_id)

    async def _execute(self, run_id: str) -> None:
        root = self.settings.harbor_v2_jobs_dir.expanduser().resolve() / run_id
        root.mkdir(parents=True, exist_ok=True)
        jobs_dir = root / "jobs"
        jobs_dir.mkdir(exist_ok=True)
        log_path = root / "harbor.log"
        exit_code: int | None = None
        result: dict | None = None
        error: str | None = None
        heartbeat = asyncio.create_task(self._heartbeat(run_id))
        try:
            async with self.sessions() as session:
                run = await session.get(HarborRunV2, run_id)
                assert run is not None
                revision = await session.get(TaskRevision, run.task_revision_id)
                assert revision is not None
                run.job_path = str(jobs_dir / run.id)
                await session.commit()
            task_dir = await self.checkout(revision, root / "source")
            command = build_command(
                self.settings, run, task_dir, jobs_dir, executable=self.executable
            )
            with log_path.open("wb") as log_file:
                process = await asyncio.create_subprocess_exec(
                    *command, stdout=log_file, stderr=asyncio.subprocess.STDOUT,
                    stdin=asyncio.subprocess.DEVNULL,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                )
                exit_code = await process.wait()
            result_path = jobs_dir / run_id / "result.json"
            if result_path.is_file():
                result = json.loads(result_path.read_text(encoding="utf-8"))
            else:
                error = "Harbor did not write result.json"
            state = terminal_state(exit_code, result)
            if state == "failed" and error is None:
                error = f"Harbor exited {exit_code}; job result has incomplete or errored trials"
        except Exception as exc:
            state = "failed"
            error = f"{type(exc).__name__}: {exc}"[:2000]
            with log_path.open("ab") as log_file:
                log_file.write((error + "\n").encode())
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        async with self.sessions() as session:
            run = await session.get(HarborRunV2, run_id)
            assert run is not None
            if run.state != "running" or run.owner != self.owner:
                return
            run.state = state
            run.exit_code = exit_code
            run.result = result
            run.error = error
            run.finished_at = datetime.now(UTC)
            run.owner = None
            run.lease_until = None
            await session.commit()


def read_log(settings: Settings, run_id: str, *, max_bytes: int = 64_000) -> tuple[str, bool]:
    path = settings.harbor_v2_jobs_dir.expanduser().resolve() / run_id / "harbor.log"
    if not path.is_file():
        return "", False
    size = path.stat().st_size
    with path.open("rb") as stream:
        if size > max_bytes:
            stream.seek(-max_bytes, 2)
        data = stream.read()
    return data.decode("utf-8", errors="replace"), size > max_bytes
