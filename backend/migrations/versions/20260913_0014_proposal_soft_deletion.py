"""Keep proposal deletion tombstones across Discussion synchronization.

Revision ID: 20260913_0014
Revises: 20260910_0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260913_0014"
down_revision = "20260910_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_proposals_deleted_at", ["deleted_at"])


def downgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.drop_index("ix_proposals_deleted_at")
        batch.drop_column("deleted_at")
