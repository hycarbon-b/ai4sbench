"""Store AI recommendations separately from human approval.

Revision ID: 20260924_0017
Revises: 20260924_0016
"""

import sqlalchemy as sa
from alembic import op

revision = "20260924_0017"
down_revision = "20260924_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_review_publications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("discussion_node_id", sa.String(100), nullable=False, unique=True),
        sa.Column("repository", sa.String(200), nullable=False),
        sa.Column("discussion_number", sa.Integer(), nullable=False),
        sa.Column("latest_review_id", sa.String(36)),
        sa.Column("github_comment_id", sa.String(100)),
        sa.Column("github_comment_url", sa.String(500)),
        sa.Column("discord_message_id", sa.String(30)),
        sa.Column("discord_thread_id", sa.String(30)),
        sa.Column("discord_message_url", sa.String(500)),
        sa.Column("discord_create_started", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("assigned_reviewer", sa.String(100)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "proposal_ai_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "publication_id", sa.String(36), sa.ForeignKey("ai_review_publications.id"), nullable=False
        ),
        sa.Column("repository", sa.String(200), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository", "run_id", "run_attempt"),
    )
    op.create_index("ix_proposal_ai_reviews_publication_id", "proposal_ai_reviews", ["publication_id"])


def downgrade() -> None:
    op.drop_table("proposal_ai_reviews")
    op.drop_table("ai_review_publications")
