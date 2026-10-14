"""Track the first GitHub connection until the account email is confirmed."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261015_0025"
down_revision = "20261015_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.add_column(
            sa.Column("github_email_review_pending", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("email_preference_set", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.drop_column("email_preference_set")
        batch.drop_column("github_email_review_pending")
