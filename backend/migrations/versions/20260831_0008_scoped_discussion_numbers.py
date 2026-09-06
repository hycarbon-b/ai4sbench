"""Scope proposal Discussion identity to GitHub's global node ID.

Revision ID: 20260831_0008
Revises: 20260831_0007
Create Date: 2026-08-31
"""

from __future__ import annotations

from alembic import op

revision = "20260831_0008"
down_revision = "20260831_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_proposals_discussion_number", table_name="proposals")


def downgrade() -> None:
    op.create_index(
        "uq_proposals_discussion_number",
        "proposals",
        ["discussion_number"],
        unique=True,
    )
