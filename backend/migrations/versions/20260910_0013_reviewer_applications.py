"""Add reviewer applications and administrator decisions.

Revision ID: 20260910_0013
Revises: 20260908_0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260910_0013"
down_revision = "20260908_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reviewer_applications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("affiliation", sa.String(300), nullable=False),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("github", sa.String(100)),
        sa.Column("role", sa.String(200)),
        sa.Column("domains", sa.JSON(), nullable=False),
        sa.Column("domains_display", sa.JSON(), nullable=False),
        sa.Column("field", sa.String(80)),
        sa.Column("subfield", sa.String(200)),
        sa.Column("research_background", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("admin_notes", sa.Text()),
        sa.Column("submitted_by_login", sa.String(100)),
        sa.Column("reviewed_by", sa.String(100)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reviewer_applications_email", "reviewer_applications", ["email"])
    op.create_index("ix_reviewer_applications_github", "reviewer_applications", ["github"])
    op.create_index("ix_reviewer_applications_status", "reviewer_applications", ["status"])
    op.create_index(
        "ix_reviewer_applications_status_created",
        "reviewer_applications",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reviewer_applications_status_created", table_name="reviewer_applications")
    op.drop_index("ix_reviewer_applications_status", table_name="reviewer_applications")
    op.drop_index("ix_reviewer_applications_github", table_name="reviewer_applications")
    op.drop_index("ix_reviewer_applications_email", table_name="reviewer_applications")
    op.drop_table("reviewer_applications")
