"""Persist canonical proposal documents and GitHub Discussion identity.

Revision ID: 20260825_0005
Revises: 20260822_0004
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260825_0005"
down_revision = "20260822_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("proposals", sa.Column("document", sa.JSON(), nullable=True))
    op.add_column("proposals", sa.Column("discussion_node_id", sa.String(length=100), nullable=True))
    op.add_column("proposals", sa.Column("discussion_number", sa.Integer(), nullable=True))
    op.add_column("proposals", sa.Column("github_created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("proposals", sa.Column("github_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_proposals_discussion_node_id", "proposals", ["discussion_node_id"], unique=True
    )
    op.create_index(
        "uq_proposals_discussion_number", "proposals", ["discussion_number"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_proposals_discussion_number", table_name="proposals")
    op.drop_index("uq_proposals_discussion_node_id", table_name="proposals")
    op.drop_column("proposals", "github_updated_at")
    op.drop_column("proposals", "github_created_at")
    op.drop_column("proposals", "discussion_number")
    op.drop_column("proposals", "discussion_node_id")
    op.drop_column("proposals", "document")
