#!/usr/bin/env python3
"""Run the repository's portable continuous-integration checks.

This script is the single source of truth for CI.  It is intentionally usable
from a Windows checkout as well as from GitHub Actions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "dashboard-frontend" / "frontend"
STATIC_ASSETS = ROOT / "backend" / "src" / "static"
DOCUMENT_ASSETS = ROOT / "backend" / "src" / "document"
BENCHMARK = ROOT / "benchmark-repository"
NPM = "npm.cmd" if sys.platform == "win32" else "npm"


def require(command: str) -> None:
    if shutil.which(command) is None:
        raise SystemExit(f"Required command not found on PATH: {command}")


def run(
    *command: str,
    cwd: Path = ROOT,
    capture_output: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    print(f"\n$ {' '.join(command)}  (cwd: {cwd})", flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture_output,
        env=env,
    )


def assert_static_assets_are_current() -> None:
    result = run(
        "git",
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        STATIC_ASSETS.relative_to(ROOT).as_posix(),
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout, end="")
        raise SystemExit(
            "Dashboard static assets are out of date. Run "
            "`npm run build` from dashboard-frontend/frontend and commit the "
            "resulting backend/src/static changes."
        )


def assert_document_assets_are_current() -> None:
    result = run(
        "git",
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        DOCUMENT_ASSETS.relative_to(ROOT).as_posix(),
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout, end="")
        raise SystemExit(
            "Documentation assets are out of date. Build MkDocs from the pinned "
            "benchmark submodule and commit backend/src/document."
        )


def build_document_assets() -> None:
    run("git", "submodule", "update", "--init", "benchmark-repository")
    source_date_epoch = run(
        "git", "show", "-s", "--format=%ct", "HEAD", cwd=BENCHMARK, capture_output=True
    ).stdout.strip()
    run(
        "uv", "run", "mkdocs", "build", "--strict",
        "--config-file", str(BENCHMARK / "mkdocs.yml"),
        "--site-dir", str(DOCUMENT_ASSETS),
        cwd=BACKEND,
        env={**os.environ, "SOURCE_DATE_EPOCH": source_date_epoch},
    )


def main() -> None:
    for command in ("git", "uv", NPM):
        require(command)

    run("uv", "sync", "--locked", "--extra", "dev", "--extra", "aws", cwd=BACKEND)
    build_document_assets()
    assert_document_assets_are_current()
    run("uv", "run", "ruff", "check", "src", "tests", cwd=BACKEND)
    run("uv", "run", "pytest", cwd=BACKEND)

    run(NPM, "ci", cwd=FRONTEND)
    run(NPM, "run", "check", cwd=FRONTEND)
    run(NPM, "run", "build", cwd=FRONTEND)
    assert_static_assets_are_current()

    print("\nCI checks passed.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from error
