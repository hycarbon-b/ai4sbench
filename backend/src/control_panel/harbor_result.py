from __future__ import annotations

from typing import Any


def terminal_state(exit_code: int, harbor_result: dict[str, Any] | None) -> str:
    """Return a fail-closed terminal state for a completed Harbor invocation.

    Harbor can exit zero after persisting a result that contains errored trials.
    The control plane must treat the structured result as authoritative, rather
    than using the process exit code alone.
    """
    if exit_code != 0 or not isinstance(harbor_result, dict):
        return "failed"
    stats = harbor_result.get("stats")
    total = harbor_result.get("n_total_trials")
    if not isinstance(stats, dict) or not isinstance(total, int) or total < 1:
        return "failed"
    return (
        "succeeded"
        if stats.get("n_completed_trials") == total
        and stats.get("n_errored_trials") == 0
        and stats.get("n_running_trials") == 0
        and stats.get("n_pending_trials") == 0
        and stats.get("n_cancelled_trials") == 0
        else "failed"
    )
