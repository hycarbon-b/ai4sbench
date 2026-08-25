from control_panel.bootstrap import render_worker_bootstrap
from control_panel.harbor_result import terminal_state


def result(*, completed: int = 1, errored: int = 0) -> dict:
    return {
        "n_total_trials": 1,
        "stats": {
            "n_completed_trials": completed,
            "n_errored_trials": errored,
            "n_running_trials": 0,
            "n_pending_trials": 0,
            "n_cancelled_trials": 0,
        },
    }


def test_terminal_state_accepts_a_complete_harbor_result() -> None:
    assert terminal_state(0, result()) == "succeeded"


def test_terminal_state_rejects_a_zero_exit_with_errored_trial() -> None:
    assert terminal_state(0, result(completed=0, errored=1)) == "failed"


def test_terminal_state_fails_closed_without_a_result() -> None:
    assert terminal_state(0, None) == "failed"


def test_worker_bootstrap_installs_the_compose_cli_plugin() -> None:
    bootstrap = render_worker_bootstrap("https://control.example", "run-id", "worker-token")
    assert "docker-compose-linux-${compose_arch}" in bootstrap
    assert "buildx-v0.36.1.linux-${buildx_arch}" in bootstrap
    assert "docker compose version" in bootstrap
    assert "docker buildx version" in bootstrap
    assert 'glob("*/exception.txt")' in bootstrap
    assert "def post(path, value, attempts=6, timeout_seconds=30)" in bootstrap
    assert "threading.Thread(target=send_event, daemon=True).start()" in bootstrap
    assert "AI4SBENCH_TRIAL_EXCEPTIONS=" in bootstrap
    assert '["git", "sparse-checkout", "init", "--cone"]' in bootstrap
    assert '["git", "sparse-checkout", "set", "--cone", revision["task_path"]]' in bootstrap
