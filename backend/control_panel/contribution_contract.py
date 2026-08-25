"""Terminal-Bench-Science dashboard contract, implemented server-side.

The public JSON shape and state semantics intentionally match the upstream
``tb-science-task-dashboard`` contract.  This module is an independent Python
implementation of its stable derivation rules; it never substitutes an
ai4sbench-specific status vocabulary for GitHub's contribution state.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

PROPOSAL_TITLE_RE = re.compile(r"^\s*\[\s*Task Proposal\s*#(\d+)\s*\]\s*(.*)", re.I)
SLOT_MARKER_RE = re.compile(r"<!--\s*reviewer-slots:\s*(\{.*?\})\s*-->", re.S)
MECHANICAL_CHECKS = ("static-checks", "execution-checks")
CI_FAILURES = {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
FIRST_PARTY = {"COLLABORATOR", "MEMBER", "OWNER"}


def proposal_status(labels: Iterable[str]) -> str:
    """Exact TBS proposal status mapping; close state remains independent."""
    values = tuple(labels)
    if any(value.startswith("proposal-approved") for value in values):
        return "approved"
    if any(value.startswith("proposal-declined") for value in values):
        return "rejected"
    return "pending"


def reviewer_slots(comments: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Read the most recent upstream reviewer-slots marker."""
    for comment in reversed(list(comments)):
        match = SLOT_MARKER_RE.search(str(comment.get("body") or ""))
        if not match:
            continue
        try:
            slots = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if "general" in slots and "technical" not in slots:
            slots["technical"] = slots["general"]
        return {
            str(login): role
            for role in ("domain", "technical", "final")
            if (login := str(slots.get(role) or "").strip())
        }
    return {}


def reviewers_from_pr(node: dict[str, Any], roles: dict[str, str]) -> list[dict[str, Any]]:
    """TBS reviewer status: assignees plus each first-party terminal review."""
    assignees = (node.get("assignees") or {}).get("nodes") or []
    terminal: dict[str, str] = {}
    for review in (node.get("reviews") or {}).get("nodes") or []:
        if review.get("authorAssociation") not in FIRST_PARTY:
            continue
        login = ((review.get("author") or {}).get("login") or "").strip()
        state = str(review.get("state") or "").upper()
        if login and state in {"APPROVED", "CHANGES_REQUESTED"}:
            terminal[login] = "approved" if state == "APPROVED" else "changes_requested"
    return [
        {
            "login": str(item.get("login") or "ghost"),
            "avatar_url": item.get("avatarUrl"),
            "status": terminal.get(str(item.get("login") or ""), "pending"),
            "role": roles.get(str(item.get("login") or "")),
        }
        for item in assignees
        if item.get("login")
    ]


def review_stage(reviewers: list[dict[str, Any]]) -> str:
    """TBS stages count approvals in actual slots, never review labels."""
    slotted = [reviewer for reviewer in reviewers if reviewer.get("role")]
    counted = slotted or reviewers
    approvals = sum(item.get("status") == "approved" for item in counted)
    return {3: "3rd", 2: "2nd", 1: "1st"}.get(min(approvals, 3), "none")


def ball_in_court(labels: Iterable[str], reviewers: list[dict[str, Any]]) -> str | None:
    """Use waiting labels, with TBS' reviewer-based contradiction recovery."""
    values = set(labels)
    author, reviewer = "waiting on author" in values, "waiting on reviewer" in values
    if author and reviewer and reviewers:
        statuses = {str(item.get("status")) for item in reviewers}
        if "changes_requested" in statuses:
            return "author"
        if "pending" in statuses:
            return "reviewer"
        return None
    if author:
        return "author"
    if reviewer:
        return "reviewer"
    return None


def ci_status(rollup: dict[str, Any] | None) -> str | None:
    """TBS CI is only static-checks + execution-checks; rubric is advisory."""
    if not rollup:
        return None
    latest: dict[str, str] = {}
    contexts = ((rollup.get("contexts") or {}).get("nodes") or [])
    for context in contexts:
        name = str(context.get("name") or context.get("context") or "")
        for gate in MECHANICAL_CHECKS:
            if not name.startswith(gate):
                continue
            if str(context.get("status") or "COMPLETED").upper() != "COMPLETED":
                latest[gate] = "pending"
            elif str(context.get("conclusion") or context.get("state") or "").upper() in {
                "SUCCESS",
                "SKIPPED",
            }:
                latest[gate] = "pass"
            elif str(context.get("conclusion") or context.get("state") or "").upper() in CI_FAILURES:
                latest[gate] = "fail"
            else:
                latest[gate] = "pending"
    if not latest:
        return None
    if "fail" in latest.values():
        return "failure"
    return "pending" if "pending" in latest.values() else "success"


def parse_proposal_title(title: str) -> tuple[int | None, str]:
    match = PROPOSAL_TITLE_RE.match(title)
    return (int(match.group(1)), match.group(2).strip()) if match else (None, title)


def contribution_shell(repository: str) -> dict[str, Any]:
    """The complete top-level TBS JSON contract, empty but schema-stable."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "upstream": repository,
        "fetch_status": {"open_prs": "stale", "discussions": "stale"},
        "partial": True,
        "taxonomy": {},
        "field_labels": {},
        "field_to_domain": {},
        "coverage_check": {"complete": False, "sources": {}},
        "prs": [],
        "fixes": [],
        "proposals": [],
        "coverage": {},
        "stats": {
            "open_prs": 0, "merged_prs": 0, "closed_prs": 0,
            "open_proposals": 0, "closed_proposals": 0,
            "approved_proposals": 0, "declined_proposals": 0,
            "pending_proposals": 0, "needs_reviewer": 0, "needs_author": 0,
        },
    }
