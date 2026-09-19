"""Store the Discord message URL for each successfully announced Proposal.

Revision ID: 20260916_0015
Revises: 20260913_0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260916_0015"
down_revision = "20260913_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.add_column(sa.Column("discord_message_url", sa.String(500)))


def downgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.drop_column("discord_message_url")
