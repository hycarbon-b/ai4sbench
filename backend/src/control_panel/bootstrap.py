from __future__ import annotations

import shlex
import textwrap


def render_worker_bootstrap(
    api_base_url: str,
    run_id: str,
    worker_token: str,
) -> str:
    """Return cloud-init user data for the disposable Free-plan worker.

    This path deliberately starts from AWS's public Amazon Linux 2023 AMI.  It
    installs the published Harbor wheel with source builds and download caching
    disabled, then writes the small, stdlib-only callback runner below.  The
    production baked-AMI path remains available in the EC2 provider.
    """

    exports = "\n".join(
        (
            f"export TBCP_API_BASE_URL={shlex.quote(api_base_url)}",
            f"export TBCP_RUN_ID={shlex.quote(run_id)}",
            f"export TBCP_JOB_TOKEN={shlex.quote(worker_token)}",
        )
    )
    worker = textwrap.dedent(
        """\
        import json
        import os
        import queue
        import shlex
        import subprocess
        import sys
        import tempfile
        import time
        import traceback
        import threading
        from pathlib import Path
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen


        def post(path, value, attempts=6, timeout_seconds=30):
            request = Request(
                f"{os.environ['TBCP_API_BASE_URL'].rstrip('/')}{path}",
                data=json.dumps(value).encode(),
                headers={"Content-Type": "application/json", "User-Agent": "ai4sbench-bootstrap"},
                method="POST",
            )
            last_error = None
            for attempt in range(attempts):
                try:
                    with urlopen(request, timeout=timeout_seconds) as response:
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


        def run(command, cwd, emit):
            process = subprocess.Popen(
                command,
                cwd=cwd,
                text=True,
                errors="replace",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            assert process.stdout is not None
            for line in process.stdout:
                emit(line.rstrip("\\r\\n"))
            return process.wait()


        def log_console(line):
            # Before claim succeeds there is no session token, so nothing can
            # be posted to /events yet; stderr reaches the unit's journal on a
            # real instance, so a claim failure is not silent.
            print(f"[worker] {line}", file=sys.stderr, flush=True)


        def main():
            run_id = os.environ["TBCP_RUN_ID"]
            log_console(f"Claiming run {run_id}")
            try:
                claim = post(
                    f"/api/v1/worker/runs/{run_id}/claim", {"token": os.environ["TBCP_JOB_TOKEN"]}
                )
            except Exception:
                log_console(f"ERROR claim failed: {traceback.format_exc()}")
                raise
            session_token = claim["session_token"]
            revision = claim["task_revision"]
            config = claim["run"]["config"]["plan_config"]
            pending = queue.Queue()

            def send_events():
                while (message := pending.get()) is not None:
                    try:
                        post(
                            f"/api/v1/worker/runs/{run_id}/events",
                            {
                                "session_token": session_token,
                                "event_type": "runner_log",
                                "message": message,
                                "payload": {},
                            },
                            attempts=3,
                            timeout_seconds=10,
                        )
                    except OSError:
                        pass

            sender = threading.Thread(target=send_events, daemon=True)
            sender.start()

            def emit(message):
                for index in range(0, max(len(message), 1), 4000):
                    pending.put(message[index : index + 4000])

            harbor_lines = []

            def record(line, prefix="[harbor]"):
                entry = f"{prefix} {line}" if line else prefix
                harbor_lines.append(entry)
                emit(entry)

            def record_worker(line):
                record(line, prefix="[worker]")

            def drain():
                pending.put(None)
                sender.join(120)

            def harbor_output():
                output = "\\n".join(harbor_lines)
                return output, len(output.encode("utf-8"))

            try:
                record_worker(f"Claimed run {run_id}")
                with tempfile.TemporaryDirectory(prefix="ai4sbench-") as temporary:
                    workdir = Path(temporary)
                    record_worker(f"Cloning pinned revision {revision['commit_sha'][:12]}")
                    clone_command = [
                        "git", "clone", "--filter=blob:none", "--no-checkout", revision["repo_url"], "repo"
                    ]
                    if run(clone_command, workdir, record_worker):
                        raise RuntimeError("git clone failed")
                    repo = workdir / "repo"
                    fetch_command = ["git", "fetch", "origin", revision["commit_sha"], "--depth", "1"]
                    if run(fetch_command, repo, record_worker):
                        raise RuntimeError("git fetch failed")
                    if run(["git", "sparse-checkout", "init", "--cone"], repo, record_worker):
                        raise RuntimeError("git sparse-checkout initialization failed")
                    sparse_set = ["git", "sparse-checkout", "set", "--cone", revision["task_path"]]
                    if run(sparse_set, repo, record_worker):
                        raise RuntimeError("git sparse-checkout configuration failed")
                    if run(["git", "checkout", "--detach", "FETCH_HEAD"], repo, record_worker):
                        raise RuntimeError("git checkout failed")
                    task = repo / revision["task_path"]
                    if not task.is_dir():
                        raise RuntimeError("pinned task path does not exist")
                    jobs_dir = workdir / "jobs"
                    job_name = f"run-{run_id}"
                    command = [
                        "/root/.local/bin/harbor", "run", "-p", str(task), "-a", config["agent"],
                        "-k", str(config.get("n_attempts", 1)), "--n-concurrent",
                        str(config["n_concurrent"]), "-e", config.get("environment", "docker"),
                        "--job-name", job_name, "--jobs-dir", str(jobs_dir),
                    ]
                    if config.get("model"):
                        command.extend(["-m", config["model"]])
                    record(f"$ {shlex.join(command)}")
                    record("stdin=disabled")
                    exit_code = run(command, repo, record)
                    record(f"exit_code={exit_code}")
                    result = {"exit_code": exit_code, "runner": "harbor", "job_name": job_name}
                    result_file = jobs_dir / job_name / "result.json"
                    if result_file.is_file():
                        result["harbor"] = json.loads(result_file.read_text(encoding="utf-8"))
                        record("result.json was read")
                    else:
                        record("result.json is missing")
                    trial_exceptions = {
                        path.parent.name: path.read_text(encoding="utf-8", errors="replace")[-12000:]
                        for path in sorted((jobs_dir / job_name).glob("*/exception.txt"))
                    }
                    if trial_exceptions:
                        result["trial_exceptions"] = trial_exceptions
                        for trial in trial_exceptions:
                            record(f"ERROR trial {trial} recorded an exception")
                        print(
                            "AI4SBENCH_TRIAL_EXCEPTIONS="
                            + json.dumps(
                                {name: value[-6000:] for name, value in trial_exceptions.items()}
                            )
                        )
                    harbor = result.get("harbor")
                    stats = harbor.get("stats") if isinstance(harbor, dict) else None
                    total = harbor.get("n_total_trials") if isinstance(harbor, dict) else None
                    succeeded = (
                        exit_code == 0
                        and isinstance(stats, dict)
                        and isinstance(total, int)
                        and total > 0
                        and stats.get("n_completed_trials") == total
                        and stats.get("n_errored_trials") == 0
                        and stats.get("n_running_trials") == 0
                        and stats.get("n_pending_trials") == 0
                        and stats.get("n_cancelled_trials") == 0
                    )
                    result["harbor_output"], result["harbor_output_bytes"] = harbor_output()
                    drain()
                    post(
                        f"/api/v1/worker/runs/{run_id}/complete",
                        {
                            "session_token": session_token,
                            "state": "succeeded" if succeeded else "failed",
                            "result": result,
                        },
                    )
            except Exception as exc:
                # A failure before Harbor ever starts must be as fully reported
                # as a Harbor failure: record the whole traceback, not just
                # str(exc), through the same [worker]-tagged log.
                full_traceback = traceback.format_exc()
                for line in full_traceback.splitlines():
                    record_worker(line)
                output, output_bytes = harbor_output()
                drain()
                post(
                    f"/api/v1/worker/runs/{run_id}/complete",
                    {
                        "session_token": session_token,
                        "state": "failed",
                        "result": {
                            "error": type(exc).__name__,
                            "message": str(exc)[:2000],
                            "traceback": full_traceback[-20000:],
                            "harbor_output": output,
                            "harbor_output_bytes": output_bytes,
                        },
                    },
                )
                raise


        if __name__ == "__main__":
            main()
        """
    )
    return "\n".join(
        (
            "#!/bin/bash",
            "set -euo pipefail",
            "dnf install -y docker git python3.12",
            "systemctl enable --now docker",
            "install -d -m 0755 /usr/local/lib/docker/cli-plugins",
            "case $(uname -m) in",
            (
                "  x86_64) compose_arch=x86_64; "
                "compose_sha=837fd1d35bf6a494f41b5b5988269a7be79de337cf1a1a6ff0e45ab51bb4e9be; "
                "buildx_arch=amd64; "
                "buildx_sha=48af8a397ebd60178778bf63611dbcebe5f5e7a9be90eb9147b24b9587455778 ;;"
            ),
            (
                "  aarch64) compose_arch=aarch64; "
                "compose_sha=fc5d1371f1ec7987e703da94ede49af3fbfb240b83f22991a98511de7bc4b93b; "
                "buildx_arch=arm64; "
                "buildx_sha=5d0cafd9d16afe1a0f0d9529885344ace2cc99efdd531b6c783c5455a6001569 ;;"
            ),
            "  *) echo 'Unsupported Compose architecture' >&2; exit 1 ;;",
            "esac",
            "curl --fail --location --retry 3 \\",
            (
                "  https://github.com/docker/compose/releases/download/v5.4.0/"
                "docker-compose-linux-${compose_arch} \\"
            ),
            "  -o /usr/local/lib/docker/cli-plugins/docker-compose",
            'echo "${compose_sha}  /usr/local/lib/docker/cli-plugins/docker-compose" | sha256sum --check',
            "chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose",
            "docker compose version",
            "curl --fail --location --retry 3 \\",
            (
                "  https://github.com/docker/buildx/releases/download/v0.36.1/"
                "buildx-v0.36.1.linux-${buildx_arch} \\"
            ),
            "  -o /usr/local/lib/docker/cli-plugins/docker-buildx",
            'echo "${buildx_sha}  /usr/local/lib/docker/cli-plugins/docker-buildx" | sha256sum --check',
            "chmod 0755 /usr/local/lib/docker/cli-plugins/docker-buildx",
            "docker buildx version",
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "export PATH=/root/.local/bin:$PATH",
            'uv tool install --no-build --no-cache --python /usr/bin/python3.12 "harbor==0.20.0"',
            exports,
            "cat >/opt/ai4sbench-worker.py <<'PYTHON'",
            worker.rstrip(),
            "PYTHON",
            "exec /usr/bin/python3.12 /opt/ai4sbench-worker.py",
        )
    )
