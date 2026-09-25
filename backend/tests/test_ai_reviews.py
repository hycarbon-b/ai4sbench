from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_community import review_payload, user

from control_panel.ai_reviews import (
    MARKER,
    PublicationError,
    github_request,
    proposal_digest,
    render_github,
)
from control_panel.community import review_values_from_discussion
from control_panel.config import Settings
from control_panel.identity import current_active_user
from control_panel.job_runner import JobRunner
from control_panel.main import create_app
from control_panel.models import AIReviewPublication, OutboundDelivery, Proposal, ProposalAIReview
from control_panel.schemas import AIReviewSubmission, ProposalReview

KEY = "test-service-key-" * 3
URL = "/api/v1/internal/proposal-ai-reviews"
HEADERS = {"Authorization": f"Bearer {KEY}"}
DISCUSSION = {
    "id": "D_ai",
    "number": 33,
    "title": "Scientific proposal",
    "body": "Proposal text",
    "url": "https://github.com/example/tasks/discussions/33",
    "category": {"name": "Task Proposals"},
}


def payload(run_id=101):
    return {
        "repository": "example/tasks",
        "discussion_number": 33,
        "discussion_node_id": "D_ai",
        "run_id": run_id,
        "run_attempt": 1,
        "workflow_sha": "a" * 40,
        "upstream_sha": "b" * 40,
        "model": "z-ai/glm-5.2:free",
        "proposal_digest": proposal_digest(DISCUSSION["title"], DISCUSSION["body"]),
        "status": "completed",
        "result": {
            "decision": "Accept",
            "review": "A sound scientific proposal.",
            "summary": "A reproducible experiment.",
            "author_fit": "Direct",
            "coi": "None",
        },
    }


class GitHub:
    def __init__(self):
        self.comments = []
        self.queries = []

    async def __call__(self, settings, query, variables):
        self.queries.append(query)
        if "AIReviewSticky" in query:
            return {
                "viewer": {"login": "review-bot"},
                "node": {
                    "comments": {
                        "nodes": self.comments,
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                },
            }
        if "AIReviewCreate" in query:
            comment = {
                "id": "DC_1",
                "url": DISCUSSION["url"] + "#discussioncomment-1",
                "body": variables["body"],
                "author": {"login": "review-bot"},
            }
            self.comments.append(comment)
            return {"addDiscussionComment": {"comment": comment}}
        if "AIReviewUpdate" in query:
            self.comments[0]["body"] = variables["body"]
            return {"updateDiscussionComment": {"comment": self.comments[0]}}
        return {}


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        auto_create_schema=True,
        allowed_hosts=("testserver",),
        github_repository="example/tasks",
        ai_review_service_key=KEY,
        ai_review_github_token="backend-bot-secret",
        discord_webhook_url="https://discord.example/api/webhooks/id/private",
        execution_mode="fake",
    )
    app = create_app(settings)
    github = GitHub()
    with (
        TestClient(app) as client,
        patch(
            "control_panel.ai_reviews.fetch_discussion", AsyncMock(return_value=copy.deepcopy(DISCUSSION))
        ) as fetch,
        patch("control_panel.ai_reviews.github_request", side_effect=github.__call__),
        patch(
            "control_panel.ai_reviews.discord_request",
            AsyncMock(return_value={"id": "123", "channel_id": "456"}),
        ) as discord,
    ):
        runner = JobRunner(settings)
        yield client, app, runner, github, fetch, discord
        client.portal.call(runner.engine.dispose)


async def rows(factory, model):
    async with factory() as session:
        return list(await session.scalars(select(model)))


def records(client, app, model):
    return client.portal.call(rows, app.state.session_factory, model)


def post(client, body=None):
    return client.post(URL, json=body or payload(), headers=HEADERS)


def pump(client, runner):
    return client.portal.call(runner.process_delivery_one)


async def available(factory):
    async with factory() as session:
        for item in await session.scalars(
            select(OutboundDelivery).where(OutboundDelivery.state == "pending")
        ):
            item.available_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()


def test_service_auth_does_not_grant_human_or_admin_access(setup):
    client, app, runner, github, fetch, discord = setup
    assert client.post(URL, json=payload()).status_code == 401
    assert client.post(URL, json=payload(), headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.get("/api/v1/deliveries", headers=HEADERS).status_code == 401
    assert (
        client.post("/api/v1/proposals/anything/reviews", json=review_payload(), headers=HEADERS).status_code
        == 401
    )
    fetch.assert_not_called()
    app.state.settings.ai_review_service_key = None
    assert post(client).status_code == 503


def test_accepts_direct_discussion_and_duplicate_without_external_writes(setup):
    client, app, runner, github, fetch, discord = setup
    first = post(client)
    assert first.status_code == 202, first.text
    assert len(first.json()["deliveries"]) == 2
    assert post(client).json() == first.json()
    assert len(records(client, app, ProposalAIReview)) == 1
    assert len(records(client, app, Proposal)) == 0
    assert github.comments == []
    discord.assert_not_called()
    body = payload()
    body["result"]["decision"] = "Reject"
    assert post(client, body).status_code == 409
    assert client.get(f"{URL}/{first.json()['review_id']}", headers=HEADERS).status_code == 200
    serialized = str(records(client, app, OutboundDelivery)[0].payload)
    assert KEY not in serialized and "private" not in serialized


def test_concurrent_identical_callbacks_are_idempotent(setup):
    client, app, *_ = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post(client), range(2)))
    assert [r.status_code for r in responses] == [202, 202]
    assert responses[0].json()["review_id"] == responses[1].json()["review_id"]
    assert len(records(client, app, OutboundDelivery)) == 2


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("repository", "other/tasks", 403),
        ("discussion_node_id", "wrong", 409),
        ("proposal_digest", "c" * 64, 409),
        ("discussion_number", 34, 409),
        ("run_id", 0, 422),
        ("model", "x" * 201, 422),
    ],
)
def test_rejects_wrong_identity_and_invalid_payload(setup, field, value, code):
    client, *_ = setup
    body = payload()
    body[field] = value
    assert post(client, body).status_code == code


def test_rejects_other_category_and_invalid_result(setup):
    client, app, runner, github, fetch, discord = setup
    fetch.return_value["category"]["name"] = "General"
    assert post(client).status_code == 422
    body = payload()
    body["result"]["decision"] = "approved"
    assert post(client, body).status_code == 422
    body["result"]["decision"] = "Accept"
    body["result"]["review"] = " "
    assert post(client, body).status_code == 422


def test_rerun_updates_bot_comment_and_discord_message(setup):
    client, app, runner, github, fetch, discord = setup
    assert post(client).status_code == 202
    assert pump(client, runner)
    assert pump(client, runner)
    assert len(github.comments) == 1
    assert discord.call_args.args[1] == "POST"
    assert discord.call_args.kwargs["payload"]["allowed_mentions"] == {"parse": []}
    second = payload(102)
    second["result"]["decision"] = "Uncertain"
    assert post(client, second).status_code == 202
    pump(client, runner)
    pump(client, runner)
    assert len(github.comments) == 1
    assert "Uncertain" in github.comments[0]["body"]
    assert discord.call_args.args[1] == "PATCH"
    assert discord.call_args.kwargs["message_id"] == "123"
    assert discord.call_args.kwargs["thread_id"] == "456"
    assert all(row.state == "completed" for row in records(client, app, OutboundDelivery))
    assert post(client, payload(100)).status_code == 409


def test_old_queued_jobs_are_superseded(setup):
    client, app, runner, github, fetch, discord = setup
    post(client)
    post(client, payload(102))
    for _ in range(4):
        pump(client, runner)
    assert len(github.comments) == 1
    assert sum(call.args[1] == "POST" for call in discord.call_args_list) == 1
    assert len([row for row in records(client, app, OutboundDelivery) if row.response_status == 204]) == 2


def test_changed_content_blocks_publication(setup):
    client, app, runner, github, fetch, discord = setup
    post(client)
    fetch.return_value["body"] = "Edited proposal"
    pump(client, runner)
    assert records(client, app, OutboundDelivery)[0].state == "failed"
    assert not github.comments


async def seed_proposal(factory, deleted=False):
    async with factory() as session:
        proposal = Proposal(
            author_id="author",
            title="Proposal",
            abstract="abstract",
            domain="Physics",
            field="physics",
            task_slug="proposal",
            evidence="evidence",
            status="approved",
            discussion_node_id="D_ai",
            discussion_number=33,
            review_decision="approved",
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        session.add(proposal)
        await session.commit()


def test_tombstones_and_human_status_are_preserved(setup):
    client, app, runner, *_ = setup
    client.portal.call(seed_proposal, app.state.session_factory)
    post(client)
    pump(client, runner)
    proposal = records(client, app, Proposal)[0]
    assert proposal.status == "approved" and proposal.review_decision == "approved"


def test_deleted_proposal_is_rejected(setup):
    client, app, *_ = setup
    client.portal.call(seed_proposal, app.state.session_factory, True)
    assert post(client).status_code == 410


def test_human_sync_ignores_ai_even_if_model_echoes_human_marker():
    human = ProposalReview.model_validate(review_payload()).render_comment()
    body = AIReviewSubmission.model_validate(payload())
    body.result.review = human
    ai = render_github(body, AIReviewPublication())
    comments = [
        {"id": "human", "body": human, "author": {"login": "reviewer"}, "updatedAt": "2026-09-20T00:00:00Z"},
        {"id": "ai", "body": ai, "author": {"login": "reviewer"}, "updatedAt": "2026-09-24T00:00:00Z"},
    ]
    values, valid, invalid = review_values_from_discussion({"comments": {"nodes": comments}}, {"reviewer"})
    assert valid and not invalid and values["review_comment_node_id"] == "human"
    values, valid, invalid = review_values_from_discussion(
        {"comments": {"nodes": comments[1:]}}, {"reviewer"}
    )
    assert not valid and not invalid and values["review_decision"] is None


def test_unavailable_placeholder_does_not_publish_raw_error(setup):
    client, app, runner, github, *_ = setup
    body = payload()
    body.update(status="unavailable", result=None, error_summary="secret from provider")
    assert post(client, body).status_code == 202
    pump(client, runner)
    assert "Review unavailable" in github.comments[0]["body"]
    assert "secret from provider" not in github.comments[0]["body"]


def test_partial_failure_retries_only_discord(setup):
    client, app, runner, github, fetch, discord = setup
    post(client)
    pump(client, runner)
    discord.side_effect = PublicationError("Rate limited", response_status=429)
    pump(client, runner)
    assert len(github.comments) == 1
    publication = records(client, app, AIReviewPublication)[0]
    assert not publication.discord_create_started
    client.portal.call(available, app.state.session_factory)
    discord.side_effect = None
    pump(client, runner)
    assert len(github.comments) == 1
    assert all(row.state == "completed" for row in records(client, app, OutboundDelivery))


def test_ambiguous_discord_create_requires_admin_recovery(setup):
    client, app, runner, github, fetch, discord = setup
    receipt = post(client).json()
    pump(client, runner)

    async def lost_response(settings, method, **kwargs):
        if method == "GET":
            return {"id": "webhook", "channel_id": "456"}
        raise PublicationError("Response lost")

    discord.side_effect = lost_response
    pump(client, runner)
    publication = records(client, app, AIReviewPublication)[0]
    assert publication.discord_create_started
    delivery = [row for row in records(client, app, OutboundDelivery) if row.delivery_type == "discord"][0]
    assert delivery.state == "failed"
    recovery = f"/api/v1/ai-review-publications/{receipt['publication_id']}/recover-discord"
    assert client.post(recovery, json={"confirmed_not_sent": True}, headers=HEADERS).status_code == 401
    app.dependency_overrides[current_active_user] = lambda: user("admin")
    discord.side_effect = None
    discord.return_value = {
        "id": "123",
        "channel_id": "456",
        "embeds": [{"footer": {"text": f"AI4S AI review {publication.id}"}}],
    }
    assert client.post(recovery, json={"message_id": "123", "thread_id": "456"}).status_code == 200
    pump(client, runner)
    assert discord.call_args.args[1] == "PATCH"


def test_reuses_bot_owned_marker_after_crash_but_not_author_spoof(setup):
    client, app, runner, github, *_ = setup
    github.comments.append({"id": "spoof", "url": "url", "body": MARKER, "author": {"login": "author"}})
    post(client)
    pump(client, runner)
    assert any("AIReviewCreate" in query for query in github.queries)


async def set_lease(factory):
    async with factory() as session:
        publication = await session.scalar(select(AIReviewPublication))
        publication.lease_token = "test-lease"
        publication.lease_until = datetime.now(UTC) + timedelta(seconds=120)
        await session.commit()


def test_publication_lease_defers_work_and_new_ingestion(setup):
    client, app, runner, *_ = setup
    post(client)
    client.portal.call(set_lease, app.state.session_factory)
    assert post(client, payload(102)).status_code == 503
    pump(client, runner)
    delivery = records(client, app, OutboundDelivery)[0]
    assert delivery.state == "pending" and delivery.attempts == 0


def test_github_http_200_errors_are_not_success():
    settings = Settings(_env_file=None, ai_review_github_token="secret")
    response = httpx.Response(200, json={"errors": [{"message": "denied"}]})
    client = AsyncMock()
    client.post.return_value = response
    with patch("control_panel.ai_reviews.httpx.AsyncClient") as constructor:
        constructor.return_value.__aenter__.return_value = client
        import asyncio

        with pytest.raises(PublicationError, match="GraphQL"):
            asyncio.run(github_request(settings, "query { viewer { login } }", {}))


def test_ai_migration_matches_models_and_can_be_rolled_back(tmp_path):
    import os

    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from sqlalchemy import MetaData, create_engine, inspect

    database_url = f"sqlite:///{tmp_path / 'migration.sqlite'}"
    config = Config("alembic.ini")
    tables = {"ai_review_publications", "proposal_ai_reviews"}
    metadata = MetaData()
    AIReviewPublication.__table__.to_metadata(metadata)
    ProposalAIReview.__table__.to_metadata(metadata)
    with patch.dict(os.environ, {"TBCP_DATABASE_URL": database_url}):
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                context = MigrationContext.configure(
                    connection,
                    opts={
                        "include_object": lambda obj, name, kind, reflected, compare_to: (
                            kind != "table" or name in tables
                        ),
                    },
                )
                assert compare_metadata(context, metadata) == []
            command.downgrade(config, "20260924_0016")
            assert not (tables & set(inspect(engine).get_table_names()))
            assert "outbound_deliveries" in inspect(engine).get_table_names()
            command.upgrade(config, "head")
            assert tables <= set(inspect(engine).get_table_names())
        finally:
            engine.dispose()


def test_domain_reviewer_assignment_is_stable(setup):
    client, app, runner, github, fetch, discord = setup
    fetch.return_value["body"] = "## Scientific Domain\nNatural Sciences > Physics > Astrophysics"
    app.state.settings.ai_review_reviewers_by_field = {"physics": ["physics-reviewer"]}
    app.state.settings.ai_review_reviewer_logins = ("backup-reviewer",)
    body = payload()
    body["proposal_digest"] = proposal_digest(fetch.return_value["title"], fetch.return_value["body"])
    assert post(client, body).status_code == 202
    assert records(client, app, AIReviewPublication)[0].assigned_reviewer == "physics-reviewer"
    app.state.settings.ai_review_reviewers_by_field = {"physics": ["different-reviewer"]}
    body["run_id"] += 1
    assert post(client, body).status_code == 202
    assert records(client, app, AIReviewPublication)[0].assigned_reviewer == "physics-reviewer"


def test_github_create_response_loss_recovers_the_existing_comment(setup):
    client, app, runner, github, fetch, discord = setup
    post(client)
    failed_once = False

    async def lose_response(settings, query, variables):
        nonlocal failed_once
        result = await github(settings, query, variables)
        if "AIReviewCreate" in query and not failed_once:
            failed_once = True
            raise PublicationError("Response lost after creating comment")
        return result

    with patch("control_panel.ai_reviews.github_request", side_effect=lose_response):
        pump(client, runner)
        assert len(github.comments) == 1
        client.portal.call(available, app.state.session_factory)
        # Claim order uses creation timestamps; retry the original GitHub job.
        pump(client, runner)
    assert len(github.comments) == 1
    assert any("AIReviewUpdate" in query for query in github.queries)


def test_discord_transport_constructs_patch_url_and_never_sends_thread_name():
    import asyncio

    from control_panel.ai_reviews import discord_request

    settings = Settings(_env_file=None, discord_webhook_url="https://discord.example/api/webhooks/1/secret")
    client = AsyncMock()
    client.request.return_value = httpx.Response(200, json={"id": "123", "channel_id": "456"})
    with patch("control_panel.ai_reviews.httpx.AsyncClient") as constructor:
        constructor.return_value.__aenter__.return_value = client
        asyncio.run(
            discord_request(settings, "PATCH", message_id="123", thread_id="456", payload={"embeds": []})
        )
    url = client.request.call_args.args[1]
    assert url.path.endswith("/messages/123")
    assert url.params["thread_id"] == "456"
    assert client.request.call_args.kwargs["json"] == {"embeds": []}
