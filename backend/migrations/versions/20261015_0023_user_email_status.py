"""Record the source of user email authorization.

Revision ID: 20261015_0023
Revises: 20261013_0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261015_0023"
down_revision = "20261013_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.add_column(
            sa.Column(
                "email_status",
                sa.String(length=32),
                nullable=False,
                server_default="unauthorized",
            )
        )
        batch.create_check_constraint(
            "ck_user_email_status",
            "email_status IN ('unauthorized', 'from_github_oauth', 'authorized')",
        )

    op.execute(
        sa.text(
            """
            UPDATE user
            SET email_status = 'from_github_oauth'
            WHERE is_verified IS TRUE
              AND EXISTS (
                  SELECT 1 FROM oauth_account
                  WHERE oauth_account.user_id = user.id
                    AND oauth_account.oauth_name = 'github'
                    AND lower(oauth_account.account_email) = lower(user.email)
              )
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.drop_constraint("ck_user_email_status", type_="check")
        batch.drop_column("email_status")
