from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.community import routes
from src.community.schemas import ProposalSubmission
from src.db.models import Proposal


def test_ordered_domains_remain_compatible():
    assert ProposalSubmission.normalize_domain_list(" Physics, Biology, Physics ") == "Physics, Biology"
    assert Proposal(domain="Biology, AI / ML", field="genetics").primary_domain == "Biology"
    assert Proposal.__table__.c.domain.type.length == 160


def test_primary_domain_path_uses_first_domain(monkeypatch):
    import asyncio

    proposal = Proposal(
        domain="AI / ML, Biology",
        field="scientific-computing",
        task_slug="example-task",
        author_id="author",
        discussion_url="https://github.com/example/repo/discussions/1",
    )
    monkeypatch.setattr(routes, "active_proposal", AsyncMock(return_value=proposal))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(github_repository="example/repo")))
    )
    result = asyncio.run(
        routes.pull_request_instructions(
            "proposal", request, None, SimpleNamespace(id="author", role="member")
        )
    )
    assert result["task_path"] == "tasks/ai-ml/example-task"
    assert result["branch"] == "task/ai-ml-example-task"


def test_normalized_domain_length_fits_storage():
    # Comma-space normalization can make an otherwise short raw value exceed the column capacity.
    raw = ",".join(f"d{i}" for i in range(40))
    assert len(raw) <= 160
    with pytest.raises(ValueError, match="at most 160"):
        ProposalSubmission.normalize_domain_list(raw)
