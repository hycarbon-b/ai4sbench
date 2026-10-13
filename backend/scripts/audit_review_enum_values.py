"""Read-only audit of persisted proposal and reviewer enum values."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from src.core.config import Settings
from src.db.database import create_database_engine

ALLOWED = {
    "proposals.status": ("draft", "pending", "approved", "changes_requested", "rejected"),
    "proposals.review_decision": ("approved", "changes_requested", "rejected"),
    "proposal_human_reviews.decision": ("approved", "changes_requested", "rejected"),
    "reviewer_applications.status": ("pending", "approved", "rejected"),
    "proposal_ai_reviews.status": ("completed", "unavailable"),
    "proposal_ai_reviews.decision": (
        "Strong Reject",
        "Reject",
        "Uncertain",
        "Accept",
        "Strong Accept",
    ),
}
REVIEW_FIELDS = (
    "review_schema_version",
    "review_decision",
    "review_short_description",
    "review_tags",
    "review_difficulty",
    "review_scientific_value",
    "review_primary_metric",
    "review_primary_metric_short",
    "review_secondary_metrics",
    "review_verification_method",
    "review_estimated_runtime",
    "review_compute_budget",
    "review_token_budget",
    "review_baseline_results",
    "review_failure_modes",
    "review_notes",
)
ARRAY_REVIEW_FIELDS = {
    "review_tags",
    "review_secondary_metrics",
    "review_baseline_results",
    "review_failure_modes",
}


async def main() -> int:
    settings = Settings()
    url = make_url(settings.database_url)
    if url.drivername != "sqlite":
        raise SystemExit(f"Unsupported database driver: {url.drivername}")
    if url.database and url.database != ":memory:" and not Path(url.database).expanduser().exists():
        raise SystemExit(
            "Configured SQLite database file does not exist; refusing to create an empty database."
        )

    engine = create_database_engine(settings)
    issue_count = 0
    try:
        async with engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            required = {key.split(".")[0] for key in ALLOWED}
            missing = sorted(required - table_names)
            if missing:
                raise SystemExit(f"Database is missing expected tables: {', '.join(missing)}")

            for key, allowed in ALLOWED.items():
                table, column = key.split(".")
                value_expr = column
                if table == "proposal_ai_reviews":
                    json_path = "$.status" if column == "status" else "$.result.decision"
                    value_expr = f"json_extract(document, '{json_path}')"
                rows = (
                    await connection.execute(
                        text(
                            f"SELECT {value_expr}, COUNT(*) FROM {table} "
                            f"GROUP BY {value_expr} ORDER BY {value_expr}"
                        )
                    )
                ).all()
                values = {value: count for value, count in rows}
                if key == "proposal_ai_reviews.decision":
                    values.pop(None, None)  # Unavailable reviews have no decision.
                invalid = {value: count for value, count in values.items() if value not in allowed}
                print(f"{key}: total={sum(values.values())}, values={values}")
                if invalid:
                    issue_count += sum(invalid.values())
                    print(f"  INVALID VALUES: {invalid}")

            review_columns = ", ".join(REVIEW_FIELDS)
            proposals = await connection.execute(
                text(
                    "SELECT id, status, review_decision, review_input_valid, review_document, "
                    f"{review_columns} FROM proposals WHERE review_input_valid = 1"
                )
            )
            proposal_mismatches = []
            proposal_review_count = 0
            for row in proposals.mappings():
                proposal_review_count += 1
                document = row["review_document"]
                if isinstance(document, str):
                    document = json.loads(document or "{}")
                payload = (document or {}).get("payload") or {}
                if row["status"] != row["review_decision"]:
                    proposal_mismatches.append((row["id"], "status/review_decision"))
                for field in REVIEW_FIELDS:
                    actual = row[field]
                    if field in ARRAY_REVIEW_FIELDS and isinstance(actual, str):
                        actual = json.loads(actual)
                    if actual != payload.get(field):
                        proposal_mismatches.append((row["id"], field))
            print(
                "valid proposal reviews: "
                f"rows={proposal_review_count}, field/status mismatches={len(proposal_mismatches)}"
            )
            for proposal_id, field in proposal_mismatches[:20]:
                print(f"  proposal={proposal_id} mismatched_field={field}")
            issue_count += len(proposal_mismatches)

            mismatches = (
                await connection.execute(
                    text(
                        "SELECT p.id, p.review_comment_node_id FROM proposals AS p "
                        "WHERE p.review_input_valid = 1 AND p.review_comment_node_id IS NOT NULL "
                        "AND NOT EXISTS (SELECT 1 FROM proposal_human_reviews AS h "
                        "WHERE h.comment_node_id = p.review_comment_node_id) ORDER BY p.id"
                    )
                )
            ).all()
            print(f"current proposal reviews missing from human review history: {len(mismatches)}")
            for proposal_id, comment_id in mismatches:
                print(f"  proposal={proposal_id} comment={comment_id}")
            issue_count += len(mismatches)

            human_mismatches = (
                await connection.execute(
                    text(
                        "SELECT COUNT(*) FROM proposal_human_reviews "
                        "WHERE (input_valid = 1 AND decision IS NOT "
                        "json_extract(document, '$.payload.review_decision')) "
                        "OR (input_valid = 0 AND decision IS NOT NULL)"
                    )
                )
            ).scalar_one()
            print(f"human review/document decision mismatches: {human_mismatches}")
            issue_count += human_mismatches

            ai_malformed = (
                await connection.execute(
                    text(
                        "SELECT COUNT(*) FROM proposal_ai_reviews WHERE "
                        "(json_extract(document, '$.status') = 'completed' AND "
                        "(json_extract(document, '$.result.decision') IS NULL OR "
                        "json_extract(document, '$.error_summary') IS NOT NULL)) OR "
                        "(json_extract(document, '$.status') = 'unavailable' AND "
                        "json_type(document, '$.result') IS NOT NULL AND "
                        "json_type(document, '$.result') != 'null')"
                    )
                )
            ).scalar_one()
            print(f"malformed AI review status/result combinations: {ai_malformed}")
            issue_count += ai_malformed
    finally:
        await engine.dispose()

    print(f"AUDIT RESULT: {'ISSUES FOUND' if issue_count else 'CLEAN'} (issues={issue_count})")
    return 1 if issue_count else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
