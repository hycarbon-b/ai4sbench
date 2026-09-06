"""Mark proposal records that satisfy the current Website form contract.

Revision ID: 20260831_0007
Revises: 20260825_0006
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260831_0007"
down_revision = "20260825_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.add_column(
            sa.Column("input_valid", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.drop_column("input_valid")
