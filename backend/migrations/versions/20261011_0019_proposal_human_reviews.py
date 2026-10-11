"""Keep each human proposal review instead of replacing the previous one.

Revision ID: 20261011_0019
Revises: 20261004_0018
"""

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "20261011_0019"
down_revision = "20261004_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposal_human_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("proposal_id", sa.String(36), sa.ForeignKey("proposals.id"), nullable=False),
        sa.Column("comment_node_id", sa.String(100), nullable=False, unique=True),
        sa.Column("comment_url", sa.String(500), nullable=False),
        sa.Column("reviewer_login", sa.String(100), nullable=False),
        sa.Column("decision", sa.String(32)),
        sa.Column("input_valid", sa.Boolean(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("github_created_at", sa.DateTime(timezone=True)),
        sa.Column("github_updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_proposal_human_reviews_proposal_id", "proposal_human_reviews", ["proposal_id"])
    connection = op.get_bind()
    rows = connection.execute(sa.text("""
        SELECT id, review_comment_node_id, review_comment_url, review_reviewer_login,
               review_decision, review_input_valid, review_document,
               review_created_at, review_updated_at, created_at, updated_at
        FROM proposals WHERE review_comment_node_id IS NOT NULL
    """))
    table = sa.table(
        "proposal_human_reviews",
        sa.column("id"), sa.column("proposal_id"), sa.column("comment_node_id"),
        sa.column("comment_url"), sa.column("reviewer_login"), sa.column("decision"),
        sa.column("input_valid"), sa.column("document", sa.JSON()),
        sa.column("github_created_at"), sa.column("github_updated_at"),
        sa.column("created_at"), sa.column("updated_at"),
    )
    for row in rows.mappings():
        connection.execute(table.insert().values(
            id=str(uuid.uuid4()), proposal_id=row["id"],
            comment_node_id=row["review_comment_node_id"],
            comment_url=row["review_comment_url"] or "",
            reviewer_login=row["review_reviewer_login"] or "",
            decision=row["review_decision"], input_valid=bool(row["review_input_valid"]),
            document=(json.loads(row["review_document"]) if isinstance(row["review_document"], str)
                      else row["review_document"] or {}),
            github_created_at=row["review_created_at"],
            github_updated_at=row["review_updated_at"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        ))


def downgrade() -> None:
    op.drop_index("ix_proposal_human_reviews_proposal_id", "proposal_human_reviews")
    op.drop_table("proposal_human_reviews")
