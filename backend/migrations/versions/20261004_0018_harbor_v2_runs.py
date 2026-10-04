"""Store Harbor CLI managed EC2 jobs separately from v1 worker runs.

Revision ID: 20261004_0018
Revises: 20260924_0017
"""

import sqlalchemy as sa
from alembic import op

revision = "20261004_0018"
down_revision = "20260924_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harbor_v2_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "task_revision_id",
            sa.String(36),
            sa.ForeignKey("task_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(200), unique=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("owner", sa.String(160)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("job_path", sa.String(1000)),
        sa.Column("exit_code", sa.Integer()),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("interrupted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_harbor_v2_runs_task_revision_id", "harbor_v2_runs", ["task_revision_id"])
    op.create_index("ix_harbor_v2_runs_state", "harbor_v2_runs", ["state"])


def downgrade() -> None:
    op.drop_index("ix_harbor_v2_runs_state", "harbor_v2_runs")
    op.drop_index("ix_harbor_v2_runs_task_revision_id", "harbor_v2_runs")
    op.drop_table("harbor_v2_runs")
