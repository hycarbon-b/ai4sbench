"""Constrain proposal and reviewer states to their shared enum values.

Revision ID: 20261013_0021
Revises: 20261013_0020
"""

from __future__ import annotations

from alembic import op

revision = "20261013_0021"
down_revision = "20261013_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The read-only enum audit must be clean before applying this migration.
    with op.batch_alter_table("proposals") as batch:
        batch.create_check_constraint(
            "ck_proposals_status",
            "status IN ('draft', 'pending', 'approved', 'changes_requested', 'rejected')",
        )
        batch.create_check_constraint(
            "ck_proposals_review_decision",
            "review_decision IS NULL OR review_decision IN ('approved', 'changes_requested', 'rejected')",
        )

    with op.batch_alter_table("proposal_human_reviews") as batch:
        batch.create_check_constraint(
            "ck_proposal_human_reviews_decision",
            "decision IS NULL OR decision IN ('approved', 'changes_requested', 'rejected')",
        )

    with op.batch_alter_table("reviewer_applications") as batch:
        batch.create_check_constraint(
            "ck_reviewer_applications_status",
            "status IN ('pending', 'approved', 'rejected')",
        )


def downgrade() -> None:
    with op.batch_alter_table("reviewer_applications") as batch:
        batch.drop_constraint("ck_reviewer_applications_status", type_="check")

    with op.batch_alter_table("proposal_human_reviews") as batch:
        batch.drop_constraint("ck_proposal_human_reviews_decision", type_="check")

    with op.batch_alter_table("proposals") as batch:
        batch.drop_constraint("ck_proposals_review_decision", type_="check")
        batch.drop_constraint("ck_proposals_status", type_="check")
