"""Store proposal reviews and link independent task revisions.

Revision ID: 20260907_0011
Revises: 20260907_0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260907_0011"
down_revision = "20260907_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.add_column(sa.Column("review_schema_version", sa.String(80)))
        batch.add_column(sa.Column("review_decision", sa.String(32)))
        batch.add_column(sa.Column("review_short_description", sa.Text()))
        batch.add_column(sa.Column("review_tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
        batch.add_column(sa.Column("review_difficulty", sa.String(80)))
        batch.add_column(sa.Column("review_scientific_value", sa.Text()))
        batch.add_column(sa.Column("review_primary_metric", sa.Text()))
        batch.add_column(sa.Column("review_primary_metric_short", sa.String(240)))
        batch.add_column(
            sa.Column("review_secondary_metrics", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch.add_column(sa.Column("review_verification_method", sa.Text()))
        batch.add_column(sa.Column("review_estimated_runtime", sa.String(240)))
        batch.add_column(sa.Column("review_compute_budget", sa.String(240)))
        batch.add_column(sa.Column("review_token_budget", sa.String(240)))
        batch.add_column(
            sa.Column("review_baseline_results", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch.add_column(
            sa.Column("review_failure_modes", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch.add_column(sa.Column("review_notes", sa.Text()))
        batch.add_column(sa.Column("review_reviewer_login", sa.String(100)))
        batch.add_column(sa.Column("review_comment_node_id", sa.String(100)))
        batch.add_column(sa.Column("review_comment_url", sa.String(500)))
        batch.add_column(sa.Column("review_created_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("review_updated_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column("review_input_valid", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("review_document", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch.create_unique_constraint("uq_proposals_review_comment_node_id", ["review_comment_node_id"])

    with op.batch_alter_table("task_revisions") as batch:
        batch.add_column(sa.Column("proposal_id", sa.String(36)))
        batch.add_column(sa.Column("pull_request_url", sa.String(500)))
        batch.add_column(sa.Column("release", sa.String(80)))
        batch.create_foreign_key(
            "fk_task_revisions_proposal_id", "proposals", ["proposal_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_task_revisions_proposal_id", ["proposal_id"])


def downgrade() -> None:
    with op.batch_alter_table("task_revisions") as batch:
        batch.drop_index("ix_task_revisions_proposal_id")
        batch.drop_constraint("fk_task_revisions_proposal_id", type_="foreignkey")
        batch.drop_column("release")
        batch.drop_column("pull_request_url")
        batch.drop_column("proposal_id")

    with op.batch_alter_table("proposals") as batch:
        batch.drop_constraint("uq_proposals_review_comment_node_id", type_="unique")
        for column in (
            "review_document",
            "review_input_valid",
            "review_updated_at",
            "review_created_at",
            "review_comment_url",
            "review_comment_node_id",
            "review_reviewer_login",
            "review_notes",
            "review_failure_modes",
            "review_baseline_results",
            "review_token_budget",
            "review_compute_budget",
            "review_estimated_runtime",
            "review_verification_method",
            "review_secondary_metrics",
            "review_primary_metric_short",
            "review_primary_metric",
            "review_scientific_value",
            "review_difficulty",
            "review_tags",
            "review_short_description",
            "review_decision",
            "review_schema_version",
        ):
            batch.drop_column(column)
