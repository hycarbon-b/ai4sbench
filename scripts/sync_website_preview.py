#!/usr/bin/env python3
"""Mirror the checked-out Website source into the backend preview snapshot."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ai4s-bench-website"
DESTINATION = ROOT / "backend" / "src" / "website_dist"
EXCLUDED_ROOTS = {".github", "tests"}
EXCLUDED_FILES = {".gitignore", "README.md"}


def source_files() -> dict[Path, Path]:
    if not SOURCE.is_dir() or not (SOURCE / "index.html").is_file():
        raise RuntimeError(
            f"Website submodule is missing or has no index.html: {SOURCE}"
        )

    files: dict[Path, Path] = {}
    for path in SOURCE.rglob("*"):
        relative = path.relative_to(SOURCE)
        if ".git" in relative.parts or relative.parts[0] in EXCLUDED_ROOTS:
            continue
        if len(relative.parts) == 1 and relative.name in EXCLUDED_FILES:
            continue
        if path.is_symlink():
            raise RuntimeError(f"Website preview source contains a symlink: {path}")
        if path.is_file():
            files[relative] = path
    return files


def source_revision() -> str:
    result = subprocess.run(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    revision = result.stdout.strip()
    staged_gitlink = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-files",
            "--stage",
            "--",
            "ai4s-bench-website",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    staged_revision = staged_gitlink.split()[1] if staged_gitlink else None
    if staged_revision != revision:
        raise RuntimeError(
            "Stage the Website submodule pointer first; it must match the checked-out "
            f"source commit {revision} (staged: {staged_revision or 'none'})."
        )
    return revision


def sync(files: dict[Path, Path], check_only: bool) -> bool:
    expected = set(files)
    if DESTINATION.is_symlink():
        raise RuntimeError(f"Preview destination must not be a symlink: {DESTINATION}")
    has_symlink = DESTINATION.exists() and any(
        path.is_symlink() for path in DESTINATION.rglob("*")
    )
    if has_symlink:
        raise RuntimeError(f"Preview destination contains a symlink: {DESTINATION}")
    existing = (
        {
            path.relative_to(DESTINATION): path
            for path in DESTINATION.rglob("*")
            if path.is_file()
        }
        if DESTINATION.exists()
        else {}
    )

    differences = expected != set(existing)
    if not differences:
        differences = any(
            files[relative].read_bytes() != existing[relative].read_bytes()
            for relative in expected
        )
    if check_only or not differences:
        return not differences

    DESTINATION.mkdir(parents=True, exist_ok=True)
    for relative, source in files.items():
        target = DESTINATION / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    for relative, old_path in existing.items():
        if relative not in expected:
            old_path.unlink()
    for directory in sorted(
        (path for path in DESTINATION.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the committed preview snapshot matches the submodule",
    )
    args = parser.parse_args()

    try:
        files = source_files()
        revision = source_revision()
        matches = sync(files, check_only=args.check)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Website preview sync failed: {exc}", file=sys.stderr)
        return 1

    if args.check:
        state = "matches" if matches else "is out of date"
        print(f"Website source {revision}: snapshot {state}")
        return 0 if matches else 1
    relative_destination = DESTINATION.relative_to(ROOT)
    print(f"Synced {len(files)} Website files from {revision} to {relative_destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
