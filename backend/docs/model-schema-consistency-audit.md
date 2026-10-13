# Model / Schema Consistency Audit

**Audit date:** 2026-10-13 (baseline branch audit; latest-main follow-up noted below)
**Scope:** SQLAlchemy models, Pydantic request/response schemas, API route transformations, website proposal/task lifecycle contract, Alembic migrations, and a read-only snapshot of the deployed SQLite database.
**Method:** static comparison of model fields, schemas and route payload construction; review of migration chain; read-only production queries (`quick_check`, enum/value checks, relationship/document checks). No production data was changed.

## Executive summary

The highest-risk inconsistency is in proposal taxonomy: the proposal accepts multiple human-readable domains, but the PR guide interpolates that combined value directly into a repository path. This can yield an invalid `tasks/<domain>/<field>/<slug>` path. The proposal form's domain list also does not match the AI review rubric's scientific scope (tracked upstream as Issues [#44](https://github.com/AI4S-Bench/ai4s-benchmark/issues/44) and [#46](https://github.com/AI4S-Bench/ai4s-benchmark/issues/46)).

Proposal, human-review, reviewer-application, and AI-review decision/status enums are shared between ORM and Pydantic where they represent the same vocabulary. Operational run/job/delivery states are less consistently constrained: most are plain strings, while selected schemas use `Literal`. Outbound delivery type is the clearest model/schema contract gap. The public task lifecycle is a separate derived vocabulary and should remain explicitly documented as a projection of proposal/review/revision evidence.

The production snapshot had no observed invalid proposal/reviewer/review enum values or review-column/document drift. The deployed DB was at Alembic revision `20261013_0020`; local revision `20261013_0021` adds enum checks and therefore had not yet been applied at the time of the snapshot.

## Findings

### A1 — Proposal domain is not safe to use as a task-directory segment — High

`ProposalSubmission.domain` accepts a comma-separated list, canonicalizes it as readable labels, and does not validate entries against the selectable domain list. The PR-instructions route then uses `proposal.domain` verbatim in `tasks/{domain}/{field}/{task_slug}` and in the branch name. A multi-domain value such as `Biology, AI / ML` therefore becomes multiple path components and includes spaces/punctuation. Even a single readable label such as `Materials Science` does not match the benchmark repository's lowercase hyphenated directory convention. This is an implementation-level contract mismatch, not just missing documentation.

**Evidence:** `backend/src/community/schemas.py` (`PROPOSAL_DOMAIN_OPTIONS`, `ProposalSubmission.normalize_domain_list`); `backend/src/community/routes.py:1390-1391` (branch/path construction); upstream Issue [#44](https://github.com/AI4S-Bench/ai4s-benchmark/issues/44).

**Impact:** contributors can receive a PR guide that points to a non-existent/non-conforming folder. Persisting display labels in `Proposal.domain` also means a future path fix needs an explicit canonical slug or a deterministic primary-domain rule.

**Recommendation:** settle the allowed scientific domain/field taxonomy first; store structured selected domains separately from a single canonical task-directory domain and field slug. Generate repository paths only from validated canonical identifiers. Decide the primary-folder rule for genuinely cross-domain proposals and document `task.toml` metadata keys alongside it.

### A2 — Proposal form, AI rubric, and task taxonomy have different scopes — High / policy decision

The API publishes seven selectable labels: Materials Science, Physics, Chemistry, Biology, AI / ML, Applied Mathematics, and Interdisciplinary. The review rubric describes the covered sciences as life, physical, earth, mathematical, and engineering sciences, and says an in-scope method such as ML does not make an out-of-scope subject in-scope. Earth and engineering sciences are absent from the form; AI / ML and Interdisciplinary are selectable as if they were scientific domains. `field_name` is free text and `field_slug` is generated mechanically, but there is no canonical mapping from form labels/fields to repository folder slugs or task metadata.

**Evidence:** `backend/src/community/schemas.py` (`PROPOSAL_DOMAIN_OPTIONS`, `ProposalSubmission.field_name`, `field_slug`); upstream rubric [task-proposal.md](https://github.com/AI4S-Bench/ai4s-benchmark/blob/main/rubrics/task-proposal.md); Issues [#44](https://github.com/AI4S-Bench/ai4s-benchmark/issues/44) and [#46](https://github.com/AI4S-Bench/ai4s-benchmark/issues/46).

**Impact:** applicant-facing categories do not reliably predict AI-review scope decisions or the task's eventual directory. A task may be accepted/rejected based on its actual subject while the form's primary label suggests a different policy.

**Recommendation:** treat #44 and #46 as one taxonomy decision. Decide whether categories denote scientific subject, methods, or cross-cutting tags; align the form, reviewer rubric, folder taxonomy, `task.toml`, and API model in one change. Do not make a code-only slug mapping until that policy is agreed.

### A3 — Delivery contract is stricter in Pydantic than in the ORM — Medium

`OutboundDelivery.delivery_type` and `OutboundDelivery.state` are unrestricted SQLAlchemy strings. `OutboundDeliveryResponse.delivery_type` only permits `discord`, `github_discussion`, or `smtp`, while its `state` is an unrestricted string. Thus ORM/database writes can create a delivery type that cannot be serialized by the admin response schema. The mailing-specific response independently constrains state to `pending | sending | completed | failed`, but that response is a projection of the same durable delivery workflow and does not constrain the database column.

**Evidence:** `backend/src/db/models.py` (`OutboundDelivery`); `backend/src/community/schemas.py` (`OutboundDeliveryResponse`); `backend/src/mailing/schemas.py` (`MailingDeliveryResponse`); `backend/src/community/admin_routes.py` and `backend/src/mailing/routes.py` response models.

**Impact:** unsupported delivery type values can cause response validation failures; unknown states can pass through to callers and UI code without an explicit contract. Multiple independently maintained vocabularies can drift.

**Recommendation:** define shared string enums for delivery type and durable delivery state, use them in model and schemas, and keep any response-specific subset as a separately named projection if it is intentional. Verify that all existing values are represented before adding database constraints.

### A4 — Operational lifecycle states have uneven validation strength — Medium / consistency debt

`ExecutionPlan.state`, `Run.state`, `DatabaseJob.state`, `OutboundDelivery.state`, `HarborRunV2.state`, and `User.role` are stored as strings. Their schemas vary: v1 plan/run/job responses use `str`; Harbor v2 response uses a `Literal`; worker completion accepts only `succeeded | failed`; mailing response uses a four-value `Literal`; `AuthenticatedUserResponse.role` uses `str`. This is not evidence that the stored rows are currently wrong, but the ORM does not reject typos and a few response surfaces enforce vocabularies that other surfaces do not.

**Evidence:** `backend/src/db/models.py`; `backend/src/control_panel/v2/models.py`; `backend/src/core/identity.py`; `backend/src/control_panel/schemas.py`; `backend/src/control_panel/v2/schemas.py`; `backend/src/community/schemas.py`; `backend/src/mailing/schemas.py`.

**Recommendation:** inventory the actual transition graphs before enum-typing these columns. Then share enums for persisted finite state machines (plan, run, job, delivery, user role) across SQLAlchemy and Pydantic. Keep provider-reported `Run.instance_state` separate because its vocabulary is owned by AWS and may evolve independently. Worker completion can remain a narrower command schema if that is deliberate.

### A5 — Proposal status vs public lifecycle status is a dual vocabulary and should be made explicit — Low / documentation

The ORM/Pydantic proposal status enum is `draft | pending | approved | changes_requested | rejected`. The website also displays derived values such as `implementation`, `agent_testing`, and `released` based on review columns and task-revision data. The frontend explicitly describes this derivation: it combines `status`, `review_*`, and `revision_*` because proposal status alone does not represent downstream work. This is a valid read-model design, but clients may mistake the display status for a persisted proposal status. The website's `site.json` additionally uses editorial labels such as `proposed` / `under_review`.

**Evidence:** `backend/src/core/enums.py` (`ProposalStatus`); `backend/src/community/schemas.py` (`ProposalBoardItemResponse`); `ai4s-bench-website/js/lifecycle.js` (`displayStatus`, `STATUS_INFO`); `ai4s-bench-website/data/site.json` (`statuses`).

**Recommendation:** name and document the API's persisted proposal status separately from the website's derived task lifecycle status. Maintain one explicit mapping/projection test when additional workflow stages become live.

### A6 — Email-verification gap in the baseline branch was resolved on latest `main`

At the baseline branch revision audited above, migration `20261013_0020` created `email_verification_challenges` but there was no ORM model or application code using it. After updating local `main` to `origin/main` and carrying the local unpushed backend-contract commit, the gap is resolved: `EmailVerificationChallenge` is now in `backend/src/db/models.py`; `backend/src/mailing/verification.py` implements challenge issuance, code hashing, expiry, attempt/rate limits, proof-token verification and one-time consumption; `backend/src/community/reviewer_otp.py` adapts this service for reviewer intake; reviewer routes and the website form call the OTP endpoints. The GitHub OAuth `is_verified_by_default=True` setting remains separate and does not implement the email-code flow.

**Evidence:** `backend/migrations/versions/20261013_0020_email_verification.py`; `backend/src/db/models.py` (`EmailVerificationChallenge`); `backend/src/mailing/verification.py`; `backend/src/community/reviewer_otp.py`; reviewer OTP endpoints in `backend/src/community/routes.py`; website reviewer form in `backend/src/website_dist/js/pages/reviewer.js`.

**Status:** implemented on latest `main`; this finding only describes the earlier baseline. The feature is controlled by `TBCP_FORM_MAIL_REQUIRE_OTP`. The audit did not execute its tests during the branch transfer.

## Reviewed design differences (not counted as defects)

1. **Proposal latest-review snapshot and human-review history:** `Proposal.review_*` stores the latest review projection, while `ProposalHumanReview` stores individual GitHub review replies. The board schema deliberately combines both. This duplication can drift unless synchronization is kept transactional/idempotent, but it is useful for a latest-value query and audit history. Current production cross-check showed no mismatch in the reviewed rows.
2. **AI review and human review are separate contracts:** AI review decisions (`Strong Reject`…`Strong Accept`) and machine statuses (`completed | unavailable`) are not the same vocabulary as human proposal decisions (`approved | changes_requested | rejected`). They should not share an enum merely because both contain “decision/status”. AI review is stored as versioned JSON in `ProposalAIReview.document`; human review history has dedicated columns and a JSON document.
3. **Board response is a projection:** `ProposalBoardItemResponse` intentionally combines proposal fields, latest human review fields, human-review history, and task-revision/run evidence. Its extra/flattened fields are not expected to map one-to-one to a single ORM table.
4. **Task revisions and execution runs:** revision resources are stored as JSON and execution snapshots are copied into runs by design. This preserves the immutable inputs used for a run; it is not a schema mismatch.

## Deployed database snapshot

Read-only snapshot through the project EC2 environment, database `control-panel.sqlite3`:

- `PRAGMA quick_check` returned `ok`.
- Database Alembic revision was `20261013_0020`; local head was `20261013_0021`.
- Proposal rows: 48 total, of which 29 are active and 19 soft-deleted. Statuses are 46 `pending` and 2 `changes_requested`; the active rows are 27 `pending` and 2 `changes_requested`. No invalid proposal status or review decision was found.
- Human review decisions: 2 `changes_requested`; no invalid value found.
- Reviewer applications: 24 (`approved` 19, `pending` 4, `rejected` 1); no invalid value found.
- AI review records: 5 (1 `completed`, 4 `unavailable`); no invalid status/result shape found.
- Parsed proposal-review document fields matched their duplicated latest-review columns for the reviewed records; no malformed review JSON was found in the scan.
- The enum `CHECK` constraints in local revision `0021` were not present in production because that migration had not yet run. This is expected rollout lag, not a local migration-chain defect; deploy only after the normal migration window and re-run the same integrity query.

The snapshot verifies current values only; it does not prove that unconstrained operational-state columns cannot accumulate invalid values later.

### Live domain / field recheck

A second read-only query compared all 48 stored proposal rows against their JSON form payloads and the current slug-generation rules:

- **20 active, `input_valid=true` proposals:** `domain` matches the form payload, `field` matches the slug derived from `field_name`, and `task_slug` matches the title-derived slug. No current valid-row field offset was found.
- **9 active, `input_valid=false` legacy/incomplete rows:** five have `field="unknown"` because the imported payload has no usable `field_name`; four have legacy domain strings longer than the ORM's `String(80)` limit (up to 101 characters). These are invalid inputs, but they remain active and can still be returned by administrative/listing paths.
- Of all 48 rows, 24 use the older sync-document shape (`form_payload` plus `source`/`validation_errors`) and 24 use the direct form payload shape. Historical readable domain values and normalized slug values differ on some invalid legacy rows; do not bulk-normalize those without distinguishing source/validity.
- The longest stored `field` is 79 characters, within the model's `String(80)` and the slug generator's 79-character cap. There are no field values longer than the model column.
- The `ProposalSubmission.domain` schema permits up to 160 characters while `Proposal.domain` is `String(80)`. Four existing invalid active values already exceed 80; the selectable labels also permit comma-separated combinations. SQLite does not enforce the declared VARCHAR length, so these values persisted. This is a model/schema capacity mismatch that can fail if moved to a length-enforcing database.
- Every one of the 20 active valid proposals stores display labels (including multi-select comma-separated values) in `Proposal.domain`, rather than a repository path slug. The PR-guide route uses that column as a path segment. The field slug is consistent; the domain-to-folder value is not.

**Resolution implication:** `field` is currently aligned for valid active data. The immediate mismatch is `domain` semantics and capacity, plus legacy invalid records. Agree on canonical domain/folder mapping before changing stored values; then either widen the database/model column to the schema's valid maximum or cap/validate domain input consistently, and quarantine/repair the nine active invalid legacy records without treating them as current form submissions.

## Coverage inventory

| Flow / model family | ORM / persistence | Pydantic / API | Audit result |
|---|---|---|---|
| Task revision, execution plan, run, worker credentials/events, cloud profile | `backend/src/db/models.py` | `backend/src/control_panel/schemas.py` | A4: lifecycle state types are mostly unconstrained; resource/path validation exists on selected inputs. |
| Harbor v2 run | `backend/src/control_panel/v2/models.py` | `backend/src/control_panel/v2/schemas.py` | Response has a finite `Literal`; ORM column remains unconstrained (A4). |
| Proposal submission/edit/publish/board | `Proposal` in `backend/src/db/models.py` | `backend/src/community/schemas.py`, `routes.py` | A1/A2 path and taxonomy contract conflict; A5 persisted vs derived statuses. |
| Human proposal review | `ProposalHumanReview` plus latest snapshot columns on `Proposal` | `ProposalReview`, `ProposalHumanReviewResponse` | Shared decision enum; production snapshot had no detected data mismatch. |
| AI review publication/results/delivery | `AIReviewPublication`, `ProposalAIReview`, `OutboundDelivery` | AI review schemas and delivery response | AI vs human vocabularies correctly separate; delivery type/state consistency gap A3. |
| Reviewer application | `ReviewerApplication` | submission, update, created/admin response schemas | Status enum shared; domains/display domains and field/subfield slug pairs are checked at request boundary. |
| Auth identity | FastAPI Users `User` / `OAuthAccount` | `AuthenticatedUserResponse` projection | role remains free-form string in both layers (A4); user response is an intentional projection. |
| Mailing | Shared `OutboundDelivery` table | `MailingDeliveryCreate` / `MailingDeliveryResponse` | Request is strongly validated; response is a state-limited projection (A3/A4). |
| Contribution dashboard snapshot, audit/run events, locks | `ContributionDashboardSnapshot`, `AuditEvent`, `RunEvent`, `ControlLock` | Mostly endpoint-specific dicts/response schemas | JSON payloads are intentionally flexible; no one-to-one Pydantic model exists for stored arbitrary payloads. |
| Email verification | `EmailVerificationChallenge` ORM model and Alembic-managed table | Reviewer OTP endpoints and typed request/response contracts | Implemented on latest `main`; see A6 for baseline-to-main change. |

## Recommended order

1. Resolve the shared taxonomy decision tracked by Issues #44 and #46; then fix canonical proposal domain/field identifiers and generated PR paths together.
2. Introduce shared enums for finite operational state columns, starting with outbound delivery type/state and then plan/run/job/user role after transition audit.
3. Name the public task lifecycle as a derived projection, separate from persisted `ProposalStatus`, and document the `proposed`/`under_review` website labels.
4. Keep email-verification ORM fields aligned with migration `0020`; verify OTP behavior in the normal test/CI workflow.
5. After migration `0021` is deployed, rerun the production value and schema checks; continue monitoring the denormalized latest-review snapshot against `ProposalHumanReview` history.


## Compatibility implementation: ordered domains

The compatibility policy now preserves the existing comma-separated `domain` string and its original order. The first entry is the primary domain; `field` is its research-field slug. No historical records are rewritten and no new persisted fields are added. The model domain length is expanded from 80 to 160, matching request validation, by migration `20261013_0022` (prepared locally, not applied to production).

Website submission and editing explicitly select a primary domain and serialize it first, while preserving remaining domain selections. Editing defaults to the existing first domain. The task detail view identifies the primary domain and retains all domain labels. Model comments and schema descriptions document the same order semantics. The PR guide slugifies only the first domain and rejects missing/unknown domain or field values.

This fixes the combined-domain path and length mismatch locally. It does not define a mapping to the benchmark rubric's five science families: a primary label such as Physics generates `physics`, and existing `mathematical-sciences` remains that slug. The upstream taxonomy-policy question and invalid legacy records remain open. Production rollout still requires applying the migration and deploying the updated backend and website.
