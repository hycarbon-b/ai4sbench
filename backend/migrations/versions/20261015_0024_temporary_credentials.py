"""Link reviewer applications to users and store temporary credential hashes."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261015_0024"
down_revision = "20261015_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("reviewer_applications") as batch:
        batch.add_column(sa.Column("user_id", sa.CHAR(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_reviewer_applications_user", "user", ["user_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_reviewer_applications_user_id", ["user_id"])

    op.create_table(
        "temporary_credentials",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id", sa.CHAR(length=36), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("used_at", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.Integer(), nullable=True),
        sa.CheckConstraint("kind IN ('invite', 'api')", name="ck_temporary_credentials_kind"),
    )
    op.create_index("ix_temporary_credentials_user_id", "temporary_credentials", ["user_id"])

    # Only a confirmed sign-in or an exact OAuth email can establish this link.
    op.execute(
        sa.text(
            """
            UPDATE reviewer_applications
            SET user_id = (
                SELECT u.id FROM user AS u
                JOIN oauth_account AS o ON o.user_id = u.id AND o.oauth_name = 'github'
                WHERE (reviewer_applications.submitted_by_login IS NOT NULL
                       AND lower(u.github_login) = lower(reviewer_applications.submitted_by_login))
                   OR lower(u.email) = lower(reviewer_applications.email)
                   OR lower(o.account_email) = lower(reviewer_applications.email)
                ORDER BY u.id LIMIT 1
            )
            WHERE status != 'rejected'
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_temporary_credentials_user_id", table_name="temporary_credentials")
    op.drop_table("temporary_credentials")
    with op.batch_alter_table("reviewer_applications") as batch:
        batch.drop_index("ix_reviewer_applications_user_id")
        batch.drop_constraint("fk_reviewer_applications_user", type_="foreignkey")
        batch.drop_column("user_id")
