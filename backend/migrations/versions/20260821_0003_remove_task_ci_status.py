"""Remove the premature repository CI status from task revisions.

Revision ID: 20260821_0003
Revises: 20260821_0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260821_0003"
down_revision = "20260821_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("task_revisions") as batch:
        batch.drop_column("ci_status")


def downgrade() -> None:
    with op.batch_alter_table("task_revisions") as batch:
        batch.add_column(
            sa.Column("ci_status", sa.String(length=32), nullable=False, server_default="unknown")
        )
