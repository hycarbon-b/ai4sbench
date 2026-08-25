"""Persist TBS-compatible contribution dashboard snapshots.

Revision ID: 20260825_0006
Revises: 20260825_0005
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260825_0006"
down_revision = "20260825_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contribution_dashboard_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("repository", sa.String(length=200), nullable=False),
        sa.Column("source_revision", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fetch_status", sa.JSON(), nullable=False),
        sa.Column("partial", sa.Boolean(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("repository", "source_revision"),
    )
    op.create_index(
        "ix_contribution_dashboard_snapshots_repository",
        "contribution_dashboard_snapshots",
        ["repository"],
    )


def downgrade() -> None:
    op.drop_index("ix_contribution_dashboard_snapshots_repository", table_name="contribution_dashboard_snapshots")
    op.drop_table("contribution_dashboard_snapshots")
