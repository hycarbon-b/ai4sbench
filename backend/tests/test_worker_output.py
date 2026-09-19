"""Tests for the worker's Harbor output pipeline and the baked-image contract.

The control plane's only view of a disposable worker is the log it streams back,
so these tests pin the guarantees the Dashboard depends on: every line is
delivered, in order, prefixed with ``[harbor]``, and repeated in the completion
payload even when real-time delivery failed.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from control_panel.ami_build import (
    ImageSpec,
    assert_image_is_runtime_free,
    render_provisioning_script,
)
from control_panel.bootstrap import render_worker_bootstrap
from control_panel.config import Settings
from control_panel.providers import Boto3EC2Provider
from control_panel.worker import (
    WORKER_PREFIX,
    EventStream,
    HarborRecorder,
    harbor_command,
    harbor_invocation,
    run_command,
    split_event_message,
)

SPEC = ImageSpec(repo_url="https://github.com/example/ai4sbench.git", commit_sha="a" * 40)


class RecordingStream(EventStream):
    """An event stream that records posted messages instead of sending them."""

    def __init__(self, *, failures: int = 0) -> None:
        self.sent: list[str] = []
        self.attempts = 0
        self._failures = failures
        self._lock = threading.Lock()
        super().__init__("https://control.example", "run-1", "session-token")

    def _post_one(self, message: str) -> None:
        with self._lock:
            self.attempts += 1
            if self.attempts <= self._failures:
                raise OSError("control plane is unreachable")
            self.sent.append(message)


def _patched_post(stream: RecordingStream):
    def post(base_url, path, value, *, attempts=1, timeout_seconds=1):
        stream._post_one(value["message"])
        return {}

    return post


class EventStreamTests(unittest.TestCase):
    def test_message_longer_than_the_event_limit_is_split_not_truncated(self) -> None:
        parts = split_event_message("x" * 9000)
        self.assertEqual([len(part) for part in parts], [4000, 4000, 1000])
        self.assertEqual("".join(parts), "x" * 9000)

    def test_a_split_harbor_line_keeps_its_prefix_on_every_part(self) -> None:
        parts = split_event_message(f"[harbor] {'x' * 9000}")
        self.assertTrue(all(part.startswith("[harbor] ") for part in parts))
        self.assertTrue(all(len(part) <= 4000 for part in parts))
        # The parts are for the live event feed only; the byte-exact line lives
        # in the completion payload's harbor_output, not in their concatenation.
        self.assertEqual(len(parts), 3)

    def test_every_line_is_delivered_in_order_under_fast_output(self) -> None:
        with patch("control_panel.worker.post") as post:
            stream = RecordingStream()
            post.side_effect = _patched_post(stream)
            expected = [f"line-{index}" for index in range(500)]
            for line in expected:
                stream.emit(line)
            stream.close()
        self.assertEqual(stream.sent, expected)

    def test_a_failed_send_does_not_stop_later_lines(self) -> None:
        with patch("control_panel.worker.post") as post:
            stream = RecordingStream(failures=1)
            post.side_effect = _patched_post(stream)
            stream.emit("first")
            stream.emit("second")
            stream.close()
        # The first line's send failed and was reported as lost, but the sender
        # thread stayed alive and delivered the rest of the run's output.
        self.assertEqual(stream.sent, ["second"])

    def test_close_waits_for_the_queue_to_drain(self) -> None:
        with patch("control_panel.worker.post") as post:
            stream = RecordingStream()
            post.side_effect = _patched_post(stream)
            for index in range(200):
                stream.emit(f"line-{index}")
            stream.close()
            self.assertEqual(len(stream.sent), 200)

    def test_emitting_after_close_is_ignored(self) -> None:
        with patch("control_panel.worker.post") as post:
            stream = RecordingStream()
            post.side_effect = _patched_post(stream)
            stream.close()
            stream.emit("too late")
        self.assertEqual(stream.sent, [])


class HarborRecorderTests(unittest.TestCase):
    def test_every_line_is_prefixed_persisted_and_streamed(self) -> None:
        streamed: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "jobs" / "run-1" / "harbor.log"
            recorder = HarborRecorder(log, streamed.append)
            recorder.write("Starting task")
            recorder.write("")
            recorder.write("ERROR trial 1 failed")
            result = recorder.attach({"exit_code": 1})
            recorder.close()
            persisted = log.read_text(encoding="utf-8")

        self.assertEqual(
            streamed,
            ["[harbor] Starting task", "[harbor]", "[harbor] ERROR trial 1 failed"],
        )
        self.assertTrue(all(line.startswith("[harbor]") for line in persisted.splitlines()))
        self.assertEqual(result["harbor_output"], "\n".join(streamed))
        self.assertEqual(result["harbor_output_bytes"], len(result["harbor_output"].encode("utf-8")))

    def test_the_full_output_is_not_truncated(self) -> None:
        streamed: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            recorder = HarborRecorder(Path(temporary) / "harbor.log", streamed.append)
            for index in range(2000):
                recorder.write(f"trial {index} completed")
            result = recorder.attach()
            recorder.close()
        self.assertEqual(len(result["harbor_output"].splitlines()), 2000)
        self.assertGreater(result["harbor_output_bytes"], 4000)

    def test_write_worker_tags_a_line_with_the_worker_prefix_not_harbor(self) -> None:
        streamed: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            recorder = HarborRecorder(Path(temporary) / "harbor.log", streamed.append)
            recorder.write_worker("Cloning pinned revision abc123")
            recorder.write("trial 1 started")
            result = recorder.attach()
            recorder.close()
        self.assertEqual(streamed[0], f"{WORKER_PREFIX} Cloning pinned revision abc123")
        self.assertEqual(streamed[1], "[harbor] trial 1 started")
        # Worker-phase and Harbor-phase lines share one recorder, so a failure
        # before Harbor ever runs is still part of the same harbor_output text.
        self.assertIn("[worker] Cloning pinned revision abc123", result["harbor_output"])


class RunCommandTests(unittest.TestCase):
    def test_stdout_and_stderr_keep_their_original_order(self) -> None:
        program = (
            "import sys\n"
            "for index in range(20):\n"
            "    print(f'out-{index}', flush=True)\n"
            "    print(f'err-{index}', file=sys.stderr, flush=True)\n"
        )
        lines: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            exit_code = run_command([sys.executable, "-c", program], Path(temporary), lines.append)
        self.assertEqual(exit_code, 0)
        self.assertEqual(lines, [f"{stream}-{index}" for index in range(20) for stream in ("out", "err")])

    def test_no_line_is_dropped_under_fast_output(self) -> None:
        program = "for index in range(5000):\n    print(f'line-{index}', flush=True)\n"
        lines: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            run_command([sys.executable, "-c", program], Path(temporary), lines.append)
        self.assertEqual(lines, [f"line-{index}" for index in range(5000)])

    def test_stdin_is_closed_for_the_child_process(self) -> None:
        program = "import sys\nprint(sys.stdin.read() == '', flush=True)\n"
        lines: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            exit_code = run_command([sys.executable, "-c", program], Path(temporary), lines.append)
        self.assertEqual(exit_code, 0)
        self.assertEqual(lines, ["True"])

    def test_a_failing_command_reports_its_exit_code_and_stderr(self) -> None:
        program = "import sys\nprint('boom', file=sys.stderr, flush=True)\nsys.exit(3)\n"
        lines: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            exit_code = run_command([sys.executable, "-c", program], Path(temporary), lines.append)
        self.assertEqual(exit_code, 3)
        self.assertEqual(lines, ["boom"])


class HarborInvocationTests(unittest.TestCase):
    config = {
        "agent": "codex",
        "model": "gpt-5",
        "n_attempts": 2,
        "n_concurrent": 4,
        "environment": "docker",
    }

    def test_the_command_carries_every_run_parameter(self) -> None:
        command = harbor_command(Path("/tmp/task"), Path("/tmp/jobs"), "run-1", self.config)
        self.assertEqual(command[:2], ["harbor", "run"])
        for flag, value in (
            ("-a", "codex"),
            ("-k", "2"),
            ("--n-concurrent", "4"),
            ("-e", "docker"),
            ("--job-name", "run-1"),
            ("-m", "gpt-5"),
        ):
            self.assertEqual(command[command.index(flag) + 1], value)

    def test_the_reported_invocation_excludes_credentials(self) -> None:
        command = harbor_command(Path("/tmp/task"), Path("/tmp/jobs"), "run-1", self.config)
        invocation = harbor_invocation(command, self.config)
        self.assertEqual(invocation["stdin"], "disabled")
        self.assertEqual(invocation["agent"], "codex")
        rendered = repr(invocation)
        for secret in ("session_token", "TBCP_JOB_TOKEN", "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(secret, rendered)


class BakedImageLaunchTests(unittest.TestCase):
    settings = Settings(
        execution_mode="ec2",
        ec2_bootstrap_mode="baked_ami",
        ec2_ami_id="ami-0123456789abcdef0",
        ec2_security_group_ids=("sg-123",),
        ec2_worker_ami_commit="b" * 40,
        job_token_secret="test-job-secret-that-is-long-enough-456",
        worker_api_base_url="https://control.example",
    )

    def user_data(self) -> str:
        provider = Boto3EC2Provider.__new__(Boto3EC2Provider)
        provider.settings = self.settings
        return provider._user_data("run-1", "worker-token")

    def test_baked_user_data_only_injects_run_values_and_executes_the_worker(self) -> None:
        script = self.user_data()
        self.assertIn("exec /opt/ai4sbench/.venv/bin/ai4sbench-worker", script)
        self.assertIn("export TBCP_RUN_ID=run-1", script)
        self.assertIn("export TBCP_JOB_TOKEN=worker-token", script)
        self.assertIn("export TBCP_API_BASE_URL=https://control.example", script)
        for installer in ("dnf install", "uv tool install", "docker-compose-linux", "buildx-v", "curl"):
            self.assertNotIn(installer, script)
        # A minimal script keeps provisioning fast and keeps the image the only
        # place worker software versions are decided.
        self.assertLessEqual(len(script.splitlines()), 10)

    def test_baked_user_data_carries_no_secret_beyond_the_one_time_job_token(self) -> None:
        script = self.user_data()
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "TBCP_JOB_TOKEN_SECRET"):
            self.assertNotIn(name, script)

    def test_development_mode_still_renders_the_installing_bootstrap(self) -> None:
        provider = Boto3EC2Provider.__new__(Boto3EC2Provider)
        provider.settings = self.settings.model_copy(update={"ec2_bootstrap_mode": "amazon_linux_2023"})
        self.assertIn("dnf install", provider._user_data("run-1", "worker-token"))

    def test_the_launch_tags_the_instance_with_its_image_and_worker_commit(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.request: dict[str, object] = {}

            def run_instances(self, **request: object) -> dict[str, object]:
                self.request = request
                return {"Instances": [{"InstanceId": "i-0123456789abcdef0"}]}

        provider = Boto3EC2Provider.__new__(Boto3EC2Provider)
        provider.settings = self.settings
        provider.client = StubClient()
        instance_id = provider.launch("run-1", "worker-token", {"instance_type": "t3.micro"})

        self.assertEqual(instance_id, "i-0123456789abcdef0")
        tags = {
            tag["Key"]: tag["Value"]
            for specification in provider.client.request["TagSpecifications"]
            if specification["ResourceType"] == "instance"
            for tag in specification["Tags"]
        }
        # These tags are what lets a finished run be traced back to the worker
        # source it executed, after the instance itself is gone.
        self.assertEqual(tags["ai4sbench:worker-ami"], "ami-0123456789abcdef0")
        self.assertEqual(tags["ai4sbench:worker-commit"], "b" * 40)
        self.assertEqual(tags["ai4sbench:bootstrap-mode"], "baked_ami")
        self.assertEqual(tags["ai4sbench:run-id"], "run-1")
        self.assertEqual(provider.client.request["ImageId"], "ami-0123456789abcdef0")

    def test_public_settings_expose_the_image_version_without_secrets(self) -> None:
        public = self.settings.public()
        self.assertEqual(public["ec2_bootstrap_mode"], "baked_ami")
        self.assertEqual(public["ec2_worker_ami_commit"], "b" * 40)
        self.assertNotIn("job_token_secret", public)


class ImageProvisioningTests(unittest.TestCase):
    def test_the_image_pins_every_component_it_installs(self) -> None:
        script = render_provisioning_script(SPEC)
        self.assertIn("harbor==0.20.0", script)
        self.assertIn("v5.4.0", script)
        self.assertIn("v0.36.1", script)
        self.assertIn(SPEC.commit_sha, script)
        self.assertIn("sha256sum --check", script)

    def test_the_image_smoke_checks_the_runtime_before_it_is_captured(self) -> None:
        script = render_provisioning_script(SPEC)
        for check in (
            "docker info",
            "docker compose version",
            "docker buildx version",
            "harbor --version",
            "/opt/ai4sbench/.venv/bin/ai4sbench-worker --help",
            "import control_panel.worker",
        ):
            self.assertIn(check, script)
        # `poweroff` is the build's success signal, so it must come last: a
        # failing check under `set -e` must abort before the instance stops.
        self.assertTrue(script.rstrip().endswith("poweroff"))

    def test_the_image_never_bakes_in_per_run_data(self) -> None:
        script = render_provisioning_script(SPEC)
        assert_image_is_runtime_free(script)
        for name in ("TBCP_JOB_TOKEN", "TBCP_RUN_ID", "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(name, script)

    def test_the_runtime_free_check_rejects_a_baked_token(self) -> None:
        with self.assertRaises(ValueError):
            assert_image_is_runtime_free("export TBCP_JOB_TOKEN=leaked")

    def test_image_tags_record_the_versions_a_run_can_be_traced_to(self) -> None:
        tags = SPEC.tags(docker_version="28.0.1")
        self.assertEqual(tags["ai4sbench:worker-commit"], SPEC.commit_sha)
        self.assertEqual(tags["ai4sbench:harbor-version"], "0.20.0")
        self.assertEqual(tags["ai4sbench:docker-version"], "28.0.1")


class InlineBootstrapTests(unittest.TestCase):
    """The development bootstrap must keep the same log guarantees."""

    def script(self) -> str:
        return render_worker_bootstrap("https://control.example", "run-1", "worker-token")

    def worker_source(self) -> str:
        body = self.script().split("<<'PYTHON'\n", 1)[1].rsplit("\nPYTHON", 1)[0]
        return body

    def test_the_inline_worker_is_valid_python(self) -> None:
        import textwrap

        compile(textwrap.dedent(self.worker_source()), "inline-worker", "exec")

    def test_the_inline_worker_queues_events_instead_of_dropping_them(self) -> None:
        source = self.worker_source()
        self.assertIn("pending = queue.Queue()", source)
        self.assertIn("sender.join(120)", source)
        self.assertNotIn("event_in_flight", source)

    def test_the_inline_worker_prefixes_and_returns_the_full_harbor_output(self) -> None:
        source = self.worker_source()
        self.assertIn('def record(line, prefix="[harbor]"):', source)
        self.assertIn('result["harbor_output"]', source)
        self.assertIn("stdin=subprocess.DEVNULL", source)

    def test_the_inline_worker_reports_pre_harbor_failures_completely(self) -> None:
        source = self.worker_source()
        # The clone/fetch/checkout phase logs through [worker], not [harbor],
        # and a failure there still carries the full traceback and log.
        self.assertIn("def record_worker(line):", source)
        self.assertIn('record(line, prefix="[worker]")', source)
        self.assertIn("traceback.format_exc()", source)
        self.assertIn('"traceback": full_traceback[-20000:]', source)
        self.assertIn("def log_console(line):", source)
        self.assertIn("ERROR claim failed", source)


class CIContractTests(unittest.TestCase):
    def test_the_ami_builder_exposes_its_interface_without_aws(self) -> None:
        script = Path(__file__).resolve().parents[2] / "scripts" / "build_worker_ami.py"
        self.assertTrue(script.is_file())
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--base-ami-id", "--repo-url", "--security-group-id", "--commit-sha"):
            self.assertIn(flag, result.stdout)


class MainFailureReportingTests(unittest.TestCase):
    """A failure before Harbor ever runs must be reported as completely as one after.

    ``main()`` clones the pinned task, then launches Harbor.  These tests
    force a failure in the clone/fetch step -- before any Harbor line exists
    -- and check that the completion payload still carries the full
    ``[worker]``-tagged log and a full traceback, not just ``str(exc)``.
    """

    def run_main(self, commit_sha: str) -> dict:
        import control_panel.worker as worker_module

        posted: list[tuple[str, dict]] = []

        def fake_post(base_url, path, value, **kwargs):
            posted.append((path, value))
            if path.endswith("/claim"):
                return {
                    "session_token": "session-token",
                    "task_revision": {
                        "repo_url": str(self.repo),
                        "commit_sha": commit_sha,
                        "task_path": "tasks/demo",
                    },
                    "run": {
                        "id": "run-1",
                        "config": {
                            "plan_config": {
                                "agent": "oracle",
                                "n_concurrent": 1,
                                "n_attempts": 1,
                                "environment": "docker",
                            }
                        },
                    },
                }
            return {}

        env = {
            "TBCP_API_BASE_URL": "https://control.example",
            "TBCP_RUN_ID": "run-1",
            "TBCP_JOB_TOKEN": "bootstrap-token",
            "TBCP_ENABLE_QUICK_TUNNEL": "0",
        }
        with patch("control_panel.worker.post", side_effect=fake_post), patch.dict(
            "os.environ", env, clear=False
        ), self.assertRaises(RuntimeError):
            worker_module.main()

        complete_path, complete_body = next(item for item in posted if item[0].endswith("/complete"))
        self.assertTrue(complete_path.endswith("/complete"))
        return complete_body

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name) / "origin"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=self.repo, check=True)
        (self.repo / "readme.txt").write_text("demo\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "demo"], cwd=self.repo, check=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_a_failed_git_fetch_reports_state_failed_with_the_full_worker_log(self) -> None:
        # A commit that does not exist in the repo makes `git fetch` fail,
        # before any [harbor] line is ever produced.
        body = self.run_main(commit_sha="0" * 40)
        self.assertEqual(body["state"], "failed")
        result = body["result"]
        self.assertEqual(result["error"], "RuntimeError")
        self.assertIn("git fetch of pinned revision failed", result["message"])
        self.assertIn("Traceback", result["traceback"])
        self.assertIn("RuntimeError: git fetch of pinned revision failed", result["traceback"])
        output = result["harbor_output"]
        self.assertIn("[worker] Cloning pinned revision", output)
        self.assertIn("[worker] fatal:", output.lower() + output)  # git's own stderr is captured
        self.assertIn("[worker] Traceback", output)
        # No line is silently missing: everything git wrote plus the raised
        # exception's full traceback are both in the one persisted log.
        self.assertTrue(all(line.startswith(("[worker]", "[harbor]")) for line in output.splitlines()))

    def test_a_missing_task_path_is_reported_with_the_full_worker_log(self) -> None:
        body = self.run_main(commit_sha=self._head())
        result = body["result"]
        self.assertEqual(result["error"], "RuntimeError")
        self.assertIn("Pinned task path does not exist", result["message"])
        self.assertIn("Pinned task path does not exist", result["traceback"])

    def _head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True, text=True, capture_output=True
        ).stdout.strip()

    def test_a_claim_failure_is_logged_locally_before_any_session_token_exists(self) -> None:
        import io

        import control_panel.worker as worker_module

        def failing_post(base_url, path, value, **kwargs):
            raise OSError("control plane unreachable")

        env = {
            "TBCP_API_BASE_URL": "https://control.example",
            "TBCP_RUN_ID": "run-1",
            "TBCP_JOB_TOKEN": "bootstrap-token",
        }
        captured = io.StringIO()
        with patch("control_panel.worker.post", side_effect=failing_post), patch.dict(
            "os.environ", env, clear=False
        ), patch("sys.stderr", captured), self.assertRaises(OSError):
            worker_module.main()
        # There is no session token yet, so /events cannot be used; the
        # failure still has to be visible somewhere (the unit's journal on a
        # real instance) rather than disappearing silently.
        self.assertIn("[worker] ERROR claim failed", captured.getvalue())
        self.assertIn("control plane unreachable", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
