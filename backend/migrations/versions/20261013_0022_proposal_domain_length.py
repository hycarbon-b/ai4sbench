"""Expand ordered comma-separated proposal domains without rewriting data."""

import sqlalchemy as sa
from alembic import op

revision = "20261013_0022"
down_revision = "20261013_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.alter_column(
            "domain", existing_type=sa.String(80), type_=sa.String(160), existing_nullable=False
        )


def downgrade() -> None:
    # Refuse to truncate existing values on databases that enforce VARCHAR lengths.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM proposals WHERE length(domain) > 80")):
        raise RuntimeError("Cannot shrink proposal domains while values exceed 80 characters")
    with op.batch_alter_table("proposals") as batch:
        batch.alter_column(
            "domain", existing_type=sa.String(160), type_=sa.String(80), existing_nullable=False
        )
