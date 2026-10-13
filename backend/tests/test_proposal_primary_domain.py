from types import SimpleNamespace
from unittest.mock import AsyncMock

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
    assert result["task_path"] == "tasks/ai-ml/scientific-computing/example-task"
    assert result["branch"] == "task/ai-ml-scientific-computing-example-task"
