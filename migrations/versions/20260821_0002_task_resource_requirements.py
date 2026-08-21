"""Persist task resource requirements for launch-time validation.

Revision ID: 20260821_0002
Revises: 20260821_0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260821_0002"
down_revision = "20260821_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_revisions",
        sa.Column("resource_requirements", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("task_revisions", "resource_requirements")
