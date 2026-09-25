# AI proposal review integration

The workflow lives in `AI4S-Bench/ai4s-benchmark`, the repository hosting proposal
Discussions. The backend stores AI recommendations independently of human
`Proposal.review_*` fields and approval status. Full Sync ignores the AI marker,
even if a model quotes a human-review template.

## Configuration

Deploy migration `20260924_0017`, the API and the job runner before enabling the
benchmark workflow. Configure these backend settings in the protected environment:

| Setting | Value |
| --- | --- |
| `TBCP_GITHUB_REPOSITORY` | `AI4S-Bench/ai4s-benchmark` |
| `TBCP_AI_REVIEW_SERVICE_KEY` | Independent random secret, at least 32 characters |
| `TBCP_AI_REVIEW_GITHUB_TOKEN` | Dedicated bot PAT with repository Discussions write access |
| `TBCP_DISCORD_WEBHOOK_URL` | Discord destination webhook |
| `TBCP_AI_REVIEW_DISCORD_FORUM` | `true` for a forum, `false` for a text channel |
| `TBCP_AI_REVIEW_SYNC_LABELS` | Optional, defaults to `false` |
| `TBCP_AI_REVIEW_REVIEWERS_BY_FIELD` | Optional JSON object, e.g. `{"physics":["your-reviewer"]}` |
| `TBCP_AI_REVIEW_REVIEWER_LOGINS` | Optional comma-separated backup reviewers |

Use a long-lived dedicated bot PAT for this implementation. A GitHub App
installation token can be supplied, but automatic token minting/refresh is not
implemented, so short-lived tokens require external rotation. The token owner
must be able to edit its own Discussion comments. The OAuth cookie used by human
reviewers is not involved.

The service key is only accepted by the two internal AI review routes. It cannot
access administrator or human-review APIs. Keys are read at runtime and are not
stored in AI delivery payloads. Missing service authentication or publishing
configuration fails closed. The AI model key is only needed in Actions.

In the benchmark repository, configure:

- Secret `OPENROUTER_API_KEY`: selected OpenRouter account key.
- Secret `AI_REVIEW_SERVICE_KEY`: same value as the backend service key.
- Variable `AI_REVIEW_BACKEND_URL`: backend HTTPS origin, e.g. `https://dashboard.ai4sbench.org`.

The chosen model is `z-ai/glm-5.2:free` at `https://openrouter.ai/api/v1`.
The script uses the synthetic `openai/` prefix solely to select the upstream SDK;
OpenRouter receives the exact selected model ID. There is no paid fallback.

## API

`POST /api/v1/internal/proposal-ai-reviews`, authenticated with
`Authorization: Bearer <service-key>`, accepts `AIReviewSubmission` (see OpenAPI).

```json
{
  "schema_version": "ai4sbench-proposal-ai-review/v1",
  "repository": "AI4S-Bench/ai4s-benchmark",
  "discussion_number": 33,
  "discussion_node_id": "D_example",
  "run_id": 123456,
  "run_attempt": 1,
  "workflow_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "upstream_sha": "f55c14ea065243c8d094e02c0aa156d5fd22fdd4",
  "model": "z-ai/glm-5.2:free",
  "proposal_digest": "<sha256 of title + newline + body>",
  "status": "completed",
  "result": {
    "decision": "Accept",
    "review": "Full rubric review in Markdown",
    "summary": "Review summary",
    "author_fit": null,
    "coi": null
  }
}
```

`unavailable` requires `result: null`; provider errors are never rendered into
public comments. Completed results must use the original TBS five-level decision
enum. The backend verifies Discussion identity, category and content digest using
GitHub, and checks deletion tombstones. A local Proposal row need not exist yet;
AI records join to proposals using the Discussion node ID, including after Full Sync.

Response `202` contains `review_id`, `publication_id`, and each destination's
delivery ID/state. It means persisted/queued, not externally published.
`GET /api/v1/internal/proposal-ai-reviews/{review_id}` returns current states with
the same service key. Operators also see AI jobs in the existing delivery dashboard.

Identical callbacks for the same repository/run/attempt return the existing record.
Conflicting content returns 409. A newer accepted run supersedes older queued
publications; old callbacks return 409. A rerun of an older run does not replace a
newer run. During an active publication lease, new ingestion returns 503 with
`Retry-After: 10`; the workflow retries. No SQLite transaction spans network I/O.
Changed proposal content or deletion blocks pending publication. Repeat `/review`
with write/admin permissions to produce a fresh review after editing a proposal.

## Delivery and recovery

GitHub gets one bot-owned sticky comment; Discord gets one review message/forum
thread updated on later reviews. Human comments and the initial proposal Discord
announcement are unchanged. A GitHub retry searches all Discussion-comment pages
for the AI marker owned by the publishing bot, which recovers a lost create response.
A different author's marker is never adopted or edited. The bot must retain its
identity and permissions across deployments.

Each channel retries independently. A Discord retry does not republish GitHub.
Known Discord message IDs are PATCHed. Explicit rejection such as HTTP 429 can
retry creation. A timeout, server error or process crash during initial creation
is ambiguous: the job stops rather than automatically creating a duplicate.

An administrator can reconcile an uncertain initial send via:

`POST /api/v1/ai-review-publications/{publication_id}/recover-discord`

Use the normal administrator session. If the message exists, submit its ID and
forum thread ID:

```json
{"message_id": "123456789", "thread_id": "987654321"}
```

The backend fetches it using the configured webhook and verifies its AI-review
footer. It then queues an update. For a regular text channel omit `thread_id`.
Only after inspecting Discord and confirming no message was created, submit:

```json
{"confirmed_not_sent": true}
```

This clears the uncertainty flag and requeues the latest Discord job. Ordinary
Resend cannot bypass the uncertainty guard. Do not rotate to a different webhook
channel while pending deliveries exist; old message IDs belong to the old webhook.

Optional reviewer assignment selects the least-loaded reviewer in the configured
field pool, then backup pool, and preserves that reviewer on reruns. Optional
labels reflect current author-fit; COI disclosure is add-only. No AI label changes
human approval status. Missing optional labels are skipped.

## Verification and operational limits

- Backend: `uv run pytest` and `uv run ruff check control_panel tests`.
- Migration: `uv run alembic upgrade head` against a disposable SQLite database.
- Benchmark: `python -m unittest discover -s ci_checks -p test_proposal_review.py`.
- Workflow: `actionlint .github/workflows/proposal-review.yml .github/workflows/proposal-review-tests.yml`.
- Live smoke test after deployment: new proposal, authorized/unauthorized `/review`,
  dispatch, model failure, Discord failure and a successful rerun updating both messages.

The free endpoint is text-only. Image links remain in the proposal but are not
fetched, and published reviews disclose this limitation. The runner conservatively
bounds combined rubric/input to 28,000 UTF-8 bytes to reserve output space inside
the endpoint's 32,768-token context; oversized proposals produce an unavailable
review. Empty/truncated model output also produces an unavailable review. The
optional author-fit pass can fail without suppressing a valid primary review.

Workflow artifacts retain result, proposal and sanitized diagnostics for seven
days. A failed backend callback fails Actions and preserves those artifacts.
Publishing remains asynchronous; inspect backend delivery state after Actions
acceptance. Disabling the workflow or service key stops new ingestion, while
already queued publication continues until the job runner is stopped.

### Validation note

The repository-wide `alembic check` already reports differences in the original
`user`, `oauth_account` and `proposals` metadata at the inspected baseline commit.
This change does not alter those tables. The focused migration regression checks
both new AI tables against their models, downgrades to `20260924_0016`, and upgrades
again while preserving the existing outbound-delivery table.
