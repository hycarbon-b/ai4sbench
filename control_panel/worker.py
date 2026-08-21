from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .harbor_result import terminal_state
from .quick_tunnel import DebugState, start_debug_server, start_quick_tunnel


def post(
    base_url: str,
    path: str,
    value: dict[str, Any],
    *,
    attempts: int = 6,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(value).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "tbcp-worker"},
        method="POST",
    )
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - caller configures control-plane URL
                return json.loads(response.read().decode())
        except HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise
            last_error = exc
        except URLError as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(min(2**attempt, 20))
    assert last_error is not None
    raise last_error


def run_command(command: list[str], cwd: Path, emit: Callable[[str], None]) -> int:
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert process.stdout is not None
    for line in process.stdout:
        emit(line.rstrip())
    return process.wait()


def main() -> None:
    base_url = os.environ["TBCP_API_BASE_URL"]
    run_id = os.environ["TBCP_RUN_ID"]
    bootstrap_token = os.environ["TBCP_JOB_TOKEN"]
    claim = post(base_url, f"/api/v1/worker/runs/{run_id}/claim", {"token": bootstrap_token})
    session_token = claim["session_token"]
    revision = claim["task_revision"]
    config = claim["run"]["config"]["plan_config"]
    debug_state = DebugState(run_id)
    event_lock = threading.Lock()
    event_in_flight = False

    def emit(message: str) -> None:
        nonlocal event_in_flight
        debug_state.update(message)
        with event_lock:
            if event_in_flight:
                return
            event_in_flight = True

        def send_event() -> None:
            nonlocal event_in_flight
            with suppress(OSError):
                post(
                    base_url,
                    f"/api/v1/worker/runs/{run_id}/events",
                    {
                        "session_token": session_token,
                        "event_type": "runner_log",
                        "message": message[:4000],
                        "payload": {},
                    },
                    attempts=1,
                    timeout_seconds=5,
                )
            with event_lock:
                event_in_flight = False

        threading.Thread(target=send_event, daemon=True).start()

    workdir = Path(tempfile.mkdtemp(prefix="tbcp-"))
    debug_server = None
    tunnel_process = None
    try:
        if os.getenv("TBCP_ENABLE_QUICK_TUNNEL", "0").lower() in {"1", "true", "yes", "on"}:
            debug_server, debug_target = start_debug_server(debug_state)

            def publish_tunnel(url: str) -> None:
                post(
                    base_url,
                    f"/api/v1/worker/runs/{run_id}/events",
                    {
                        "session_token": session_token,
                        "event_type": "debug_tunnel_ready",
                        "message": "One-time Cloudflare debug endpoint is ready",
                        "payload": {"url": url},
                    },
                )

            tunnel_process = start_quick_tunnel(debug_target, debug_state, publish_tunnel)
            if tunnel_process is None:
                emit("cloudflared is not installed; continuing without a public debug endpoint")

        emit(f"Cloning pinned revision {revision['commit_sha'][:12]}")
        if (
            run_command(
                ["git", "clone", "--filter=blob:none", "--no-checkout", revision["repo_url"], "repo"],
                workdir,
                emit,
            )
            != 0
        ):
            raise RuntimeError("git clone failed")
        repo = workdir / "repo"
        if run_command(["git", "fetch", "origin", revision["commit_sha"], "--depth", "1"], repo, emit) != 0:
            raise RuntimeError("git fetch of pinned revision failed")
        if run_command(["git", "sparse-checkout", "init", "--no-cone"], repo, emit) != 0:
            raise RuntimeError("git sparse-checkout initialization failed")
        if (
            run_command(["git", "sparse-checkout", "set", "--no-cone", revision["task_path"]], repo, emit)
            != 0
        ):
            raise RuntimeError("git sparse-checkout configuration failed")
        if run_command(["git", "checkout", "--detach", "FETCH_HEAD"], repo, emit) != 0:
            raise RuntimeError("git checkout failed")
        task = repo / revision["task_path"]
        if not task.is_dir():
            raise RuntimeError("Pinned task path does not exist")
        jobs_dir = workdir / "jobs"
        job_name = f"run-{run_id}"
        command = [
            "harbor",
            "run",
            "-p",
            str(task),
            "-a",
            config["agent"],
            "-k",
            str(config.get("n_attempts", 1)),
            "--n-concurrent",
            str(config["n_concurrent"]),
            "-e",
            config.get("environment", "docker"),
            "--job-name",
            job_name,
            "--jobs-dir",
            str(jobs_dir),
        ]
        if config.get("model"):
            command.extend(["-m", config["model"]])
        emit("Launching Harbor task")
        debug_state.status = "running"
        exit_code = run_command(command, repo, emit)
        result: dict[str, Any] = {"exit_code": exit_code, "runner": "harbor", "job_name": job_name}
        harbor_result = jobs_dir / job_name / "result.json"
        if harbor_result.is_file():
            try:
                result["harbor"] = json.loads(harbor_result.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                result["result_parse_error"] = str(exc)[:500]
        trial_exceptions = {
            path.parent.name: path.read_text(encoding="utf-8", errors="replace")[-12000:]
            for path in sorted((jobs_dir / job_name).glob("*/exception.txt"))
        }
        if trial_exceptions:
            result["trial_exceptions"] = trial_exceptions
        state = terminal_state(exit_code, result.get("harbor"))
        debug_state.status = state
        post(
            base_url,
            f"/api/v1/worker/runs/{run_id}/complete",
            {"session_token": session_token, "state": state, "result": result},
        )
    except Exception as exc:
        with suppress(Exception):
            post(
                base_url,
                f"/api/v1/worker/runs/{run_id}/complete",
                {
                    "session_token": session_token,
                    "state": "failed",
                    "result": {"error": type(exc).__name__, "message": str(exc)[:500]},
                },
            )
        raise
    finally:
        if tunnel_process is not None and tunnel_process.poll() is None:
            tunnel_process.terminate()
        if debug_server is not None:
            debug_server.shutdown()


if __name__ == "__main__":
    main()
