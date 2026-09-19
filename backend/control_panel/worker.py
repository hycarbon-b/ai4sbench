from __future__ import annotations

import json
import os
import queue
import shlex
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

HARBOR_PREFIX = "[harbor]"
EVENT_MESSAGE_LIMIT = 4000


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


def split_event_message(message: str, limit: int = EVENT_MESSAGE_LIMIT) -> list[str]:
    """Split one log line into event-sized parts without discarding any of it.

    The events API caps a single message at ``EVENT_MESSAGE_LIMIT`` characters,
    so a long Harbor line is delivered as consecutive parts rather than being
    truncated.  Every part keeps the ``[harbor]`` prefix, so each row the
    Dashboard's live event feed shows is still identifiable as Harbor output;
    nothing on the server stitches parts back into the original line, since
    the byte-exact text is already carried by the completion payload's
    ``harbor_output`` field, built from the unsplit line.
    """
    if len(message) <= limit:
        return [message]
    prefix = f"{HARBOR_PREFIX} " if message.startswith(f"{HARBOR_PREFIX} ") else ""
    body = message[len(prefix) :]
    budget = limit - len(prefix)
    return [prefix + body[index : index + budget] for index in range(0, len(body), budget)]


class EventStream:
    """Deliver every worker log line to the control plane, in order.

    Harbor writes output faster than the events API accepts it.  Lines are
    queued rather than dropped while a request is in flight, a single sender
    thread preserves their order, and :meth:`close` blocks until the queue has
    drained so a finished run never loses its tail.
    """

    def __init__(
        self,
        base_url: str,
        run_id: str,
        session_token: str,
        *,
        debug_state: DebugState | None = None,
        attempts: int = 3,
        timeout_seconds: int = 10,
    ) -> None:
        self._base_url = base_url
        self._path = f"/api/v1/worker/runs/{run_id}/events"
        self._session_token = session_token
        self._debug_state = debug_state
        self._attempts = attempts
        self._timeout_seconds = timeout_seconds
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._closed = threading.Event()
        self._sender = threading.Thread(target=self._drain, name="tbcp-events", daemon=True)
        self._sender.start()

    def emit(self, message: str) -> None:
        if self._debug_state is not None:
            self._debug_state.update(message)
        if self._closed.is_set():
            return
        for part in split_event_message(message):
            self._queue.put(part)

    def _drain(self) -> None:
        while (message := self._queue.get()) is not None:
            # A failed line must not stop the sender; ``post`` already retries
            # transient failures before giving up on this one message.
            with suppress(OSError):
                post(
                    self._base_url,
                    self._path,
                    {
                        "session_token": self._session_token,
                        "event_type": "runner_log",
                        "message": message,
                        "payload": {},
                    },
                    attempts=self._attempts,
                    timeout_seconds=self._timeout_seconds,
                )

    def close(self, timeout_seconds: float = 120.0) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._queue.put(None)
        self._sender.join(timeout_seconds)


class HarborRecorder:
    """Prefix, persist, and stream every line Harbor writes.

    Each line is tagged with ``[harbor]``, appended to a local log file, and
    handed to the event stream.  The accumulated text is returned with the
    completion callback so a run still reports its full output even when
    individual real-time events failed to reach the control plane.
    """

    def __init__(self, log_path: Path, emit: Callable[[str], None]) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = log_path.open("w", encoding="utf-8", errors="replace")
        self._emit = emit
        self._lines: list[str] = []

    def write(self, line: str) -> None:
        entry = f"{HARBOR_PREFIX} {line}" if line else HARBOR_PREFIX
        self._lines.append(entry)
        with suppress(OSError):
            self._handle.write(f"{entry}\n")
            self._handle.flush()
        self._emit(entry)

    def text(self) -> str:
        return "\n".join(self._lines)

    def attach(self, result: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {} if result is None else result
        output = self.text()
        result["harbor_output"] = output
        result["harbor_output_bytes"] = len(output.encode("utf-8"))
        return result

    def close(self) -> None:
        with suppress(OSError):
            self._handle.close()


def run_command(command: list[str], cwd: Path, emit: Callable[[str], None]) -> int:
    """Run ``command`` and stream its merged stdout/stderr through ``emit``.

    ``stderr`` is folded into ``stdout`` so the caller observes the original
    interleaved ordering, and ``stdin`` is closed because none of these
    commands is interactive.
    """
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        emit(line.rstrip("\r\n"))
    return process.wait()


def harbor_command(task: Path, jobs_dir: Path, job_name: str, config: dict[str, Any]) -> list[str]:
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
    return command


def harbor_invocation(command: list[str], config: dict[str, Any]) -> dict[str, Any]:
    """Describe the Harbor inputs for the completion callback.

    Only run configuration is reported.  Tokens, credentials, and the session
    token are never part of the Harbor command line and must never be added
    here.
    """
    return {
        "command": list(command),
        "agent": config["agent"],
        "model": config.get("model"),
        "environment": config.get("environment", "docker"),
        "n_attempts": config.get("n_attempts", 1),
        "n_concurrent": config["n_concurrent"],
        "stdin": "disabled",
    }


def main() -> None:
    base_url = os.environ["TBCP_API_BASE_URL"]
    run_id = os.environ["TBCP_RUN_ID"]
    bootstrap_token = os.environ["TBCP_JOB_TOKEN"]
    claim = post(base_url, f"/api/v1/worker/runs/{run_id}/claim", {"token": bootstrap_token})
    session_token = claim["session_token"]
    revision = claim["task_revision"]
    config = claim["run"]["config"]["plan_config"]
    debug_state = DebugState(run_id)
    events = EventStream(base_url, run_id, session_token, debug_state=debug_state)
    emit = events.emit

    workdir = Path(tempfile.mkdtemp(prefix="tbcp-"))
    jobs_dir = workdir / "jobs"
    job_name = f"run-{run_id}"
    recorder = HarborRecorder(jobs_dir / job_name / "harbor.log", emit)
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
        if run_command(["git", "sparse-checkout", "init", "--cone"], repo, emit) != 0:
            raise RuntimeError("git sparse-checkout initialization failed")
        if (
            run_command(["git", "sparse-checkout", "set", "--cone", revision["task_path"]], repo, emit)
            != 0
        ):
            raise RuntimeError("git sparse-checkout configuration failed")
        if run_command(["git", "checkout", "--detach", "FETCH_HEAD"], repo, emit) != 0:
            raise RuntimeError("git checkout failed")
        task = repo / revision["task_path"]
        if not task.is_dir():
            raise RuntimeError("Pinned task path does not exist")
        command = harbor_command(task, jobs_dir, job_name, config)
        recorder.write(f"$ {shlex.join(command)}")
        recorder.write("stdin=disabled")
        recorder.write(f"cwd={repo}")
        debug_state.status = "running"
        exit_code = run_command(command, repo, recorder.write)
        recorder.write(f"exit_code={exit_code}")
        result: dict[str, Any] = {
            "exit_code": exit_code,
            "runner": "harbor",
            "job_name": job_name,
            "harbor_invocation": harbor_invocation(command, config),
        }
        harbor_result = jobs_dir / job_name / "result.json"
        if harbor_result.is_file():
            try:
                result["harbor"] = json.loads(harbor_result.read_text(encoding="utf-8"))
                recorder.write("result.json was read")
            except (OSError, json.JSONDecodeError) as exc:
                result["result_parse_error"] = str(exc)[:500]
                recorder.write(f"result.json could not be read: {exc}")
        else:
            recorder.write("result.json is missing")
        trial_exceptions = {
            path.parent.name: path.read_text(encoding="utf-8", errors="replace")[-12000:]
            for path in sorted((jobs_dir / job_name).glob("*/exception.txt"))
        }
        if trial_exceptions:
            result["trial_exceptions"] = trial_exceptions
            for trial, text in trial_exceptions.items():
                recorder.write(f"ERROR trial {trial} recorded an exception")
                for line in text.splitlines()[-40:]:
                    recorder.write(f"ERROR {trial}: {line}")
        state = terminal_state(exit_code, result.get("harbor"))
        debug_state.status = state
        recorder.attach(result)
        events.close()
        post(
            base_url,
            f"/api/v1/worker/runs/{run_id}/complete",
            {"session_token": session_token, "state": state, "result": result},
        )
    except Exception as exc:
        recorder.write(f"ERROR {type(exc).__name__}: {exc}")
        debug_state.status = "failed"
        failure = recorder.attach({"error": type(exc).__name__, "message": str(exc)[:500]})
        with suppress(Exception):
            events.close(timeout_seconds=30.0)
        with suppress(Exception):
            post(
                base_url,
                f"/api/v1/worker/runs/{run_id}/complete",
                {"session_token": session_token, "state": "failed", "result": failure},
            )
        raise
    finally:
        recorder.close()
        events.close(timeout_seconds=30.0)
        if tunnel_process is not None and tunnel_process.poll() is None:
            tunnel_process.terminate()
        if debug_server is not None:
            debug_server.shutdown()


if __name__ == "__main__":
    main()
