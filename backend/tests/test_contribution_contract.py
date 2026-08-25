from control_panel.contribution_contract import (
    ball_in_court,
    ci_status,
    parse_proposal_title,
    proposal_status,
    review_stage,
)


def test_tbs_proposal_status_uses_label_prefixes() -> None:
    assert proposal_status(["proposal-approved ✅"]) == "approved"
    assert proposal_status(["proposal-declined ❌"]) == "rejected"
    assert proposal_status(["other"]) == "pending"


def test_review_stage_and_ball_match_tbs_semantics() -> None:
    reviewers = [
        {"role": "domain", "status": "approved"},
        {"role": "technical", "status": "pending"},
        {"role": "final", "status": "pending"},
    ]
    assert review_stage(reviewers) == "1st"
    assert ball_in_court(["waiting on author", "waiting on reviewer"], reviewers) == "reviewer"


def test_ci_excludes_advisory_rubric() -> None:
    rollup = {
        "contexts": {
            "nodes": [
                {"name": "static-checks", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "execution-checks task", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "rubric-review", "status": "COMPLETED", "conclusion": "FAILURE"},
            ]
        }
    }
    assert ci_status(rollup) == "success"


def test_tbs_proposal_title_is_preserved() -> None:
    assert parse_proposal_title("[Task Proposal #42] A task") == (42, "A task")
