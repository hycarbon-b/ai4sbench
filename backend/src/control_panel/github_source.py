from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

import httpx

GITHUB_REPO = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


class GitHubTaskSource:
    def __init__(
        self,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
        git_https_proxy: str | None = None,
    ) -> None:
        self.token = token
        self.client = client
        self.git_https_proxy = git_https_proxy

    async def _request(self, path: str) -> dict:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "ai4sbench-control-panel"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.client:
            response = await self.client.get(f"https://api.github.com{path}", headers=headers)
        else:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(f"https://api.github.com{path}", headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"GitHub sync failed with HTTP {response.status_code}") from exc
        return response.json()

    async def sync_revision(self, repo_url: str, ref: str, task_path: str) -> dict[str, object]:
        match = GITHUB_REPO.match(repo_url)
        if not match:
            raise ValueError("repo_url must be a canonical https://github.com/owner/repo URL")
        if not ref or not task_path or task_path.startswith("/") or ".." in task_path.split("/"):
            raise ValueError("ref and a safe relative task_path are required")
        owner, repo = match.group("owner"), match.group("repo")
        commit = await self._request(f"/repos/{owner}/{repo}/commits/{ref}")
        sha = str(commit["sha"])
        task_file = await self._request(f"/repos/{owner}/{repo}/contents/{task_path}/task.toml?ref={sha}")
        try:
            task_toml = base64.b64decode(str(task_file["content"])).decode("utf-8")
            environment = tomllib.loads(task_toml).get("environment", {})
            requirements = {
                key: int(environment[key])
                for key in ("cpus", "memory_mb", "storage_mb", "gpus")
                if key in environment
            }
        except (KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
            raise ValueError("task.toml could not be read for resource requirements") from exc
        return {
            "repo_url": repo_url.removesuffix(".git").rstrip("/"),
            "commit_sha": sha,
            "task_path": task_path,
            "resource_requirements": requirements,
        }

    async def sync_repository(
        self, repo_url: str, ref: str
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        """Discover every task definition at the latest branch commit.

        Git fetches the complete tree, then the importer reads every
        ``tasks/**/task.toml`` blob.  Large datasets and build contexts are not
        downloaded merely to populate a task picker; the worker checks out the
        complete immutable repository snapshot before it runs a selected task.
        """
        match = GITHUB_REPO.match(repo_url)
        if not match:
            raise ValueError("repo_url must be a canonical https://github.com/owner/repo URL")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", ref):
            raise ValueError("ref must be a safe branch or tag name")

        checkout = await asyncio.to_thread(self._checkout_branch, repo_url, ref, self.git_https_proxy)
        try:
            commit_sha, task_specs = await asyncio.to_thread(self._scan_task_files, checkout)
            tasks = [
                {
                    "repo_url": repo_url.removesuffix(".git").rstrip("/"),
                    "commit_sha": commit_sha,
                    "task_path": task_path,
                    "resource_requirements": requirements,
                }
                for task_path, requirements in task_specs
            ]
            return {"commit_sha": commit_sha, "task_count": len(tasks)}, tasks
        finally:
            await asyncio.to_thread(self._remove_checkout, checkout)

    @staticmethod
    def _resource_requirements(task_toml: str) -> dict[str, int]:
        try:
            environment = tomllib.loads(task_toml).get("environment", {})
            return {
                key: int(environment[key])
                for key in ("cpus", "memory_mb", "storage_mb", "gpus")
                if key in environment
            }
        except (TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
            raise ValueError("A task.toml file could not be read") from exc

    @classmethod
    def _scan_task_files(cls, checkout: Path) -> tuple[str, list[tuple[str, dict[str, int]]]]:
        """Read task TOMLs away from FastAPI's event loop."""
        commit_sha = cls._git_output(checkout, "rev-parse", "HEAD")
        tree_paths = cls._git_output(checkout, "ls-tree", "-r", "--name-only", "HEAD", "--", "tasks")
        task_paths = [
            path
            for path in tree_paths.splitlines()
            if path.startswith("tasks/") and path.endswith("/task.toml")
        ]
        if not task_paths:
            raise ValueError("No tasks/**/task.toml files were found at the requested ref")
        return (
            commit_sha,
            [
                (
                    task_path.removesuffix("/task.toml"),
                    cls._resource_requirements(cls._git_output(checkout, "show", f"HEAD:{task_path}")),
                )
                for task_path in task_paths
            ],
        )

    @staticmethod
    def _checkout_branch(repo_url: str, ref: str, git_https_proxy: str | None = None) -> Path:
        root = Path(tempfile.mkdtemp(prefix="ai4sbench-task-import-"))
        checkout = root / "repo"
        command = [
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            "--depth=1",
            "--single-branch",
            f"--branch={ref}",
            repo_url,
            str(checkout),
        ]
        environment = os.environ.copy()
        if git_https_proxy:
            environment["HTTPS_PROXY"] = git_https_proxy
            environment["HTTP_PROXY"] = git_https_proxy
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                env=environment,
            )
            if git_https_proxy:
                for key in ("http.proxy", "https.proxy"):
                    subprocess.run(
                        ["git", "config", key, git_https_proxy],
                        cwd=checkout,
                        check=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=30,
                    )
        except (OSError, subprocess.SubprocessError) as exc:
            GitHubTaskSource._remove_checkout(checkout)
            raise ValueError("Git checkout of the requested branch failed") from exc
        return checkout

    @staticmethod
    def _git_output(checkout: Path, *args: str) -> str:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=checkout,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("Could not resolve the checked-out commit") from exc
        return completed.stdout.strip()

    @staticmethod
    def _remove_checkout(checkout: Path) -> None:
        import shutil

        shutil.rmtree(checkout.parent, ignore_errors=True)
