"""Store manual proposal reviewer assignments independently of submitted reviews."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261022_0027"
down_revision = "20261018_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.add_column(sa.Column("assigned_reviewer_application_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("assigned_reviewer_login", sa.String(100), nullable=True))
        batch.add_column(sa.Column("reviewer_assigned_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("reviewer_assigned_by", sa.String(100), nullable=True))
        batch.create_foreign_key(
            "fk_proposals_assigned_reviewer_application_id_reviewer_applications",
            "reviewer_applications",
            ["assigned_reviewer_application_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(
            "ix_proposals_assigned_reviewer_application_id", ["assigned_reviewer_application_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("proposals") as batch:
        batch.drop_index("ix_proposals_assigned_reviewer_application_id")
        batch.drop_constraint(
            "fk_proposals_assigned_reviewer_application_id_reviewer_applications", type_="foreignkey"
        )
        batch.drop_column("reviewer_assigned_by")
        batch.drop_column("reviewer_assigned_at")
        batch.drop_column("assigned_reviewer_login")
        batch.drop_column("assigned_reviewer_application_id")
