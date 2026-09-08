"""Add inspectable webhook deliveries.

Revision ID: 20260908_0012
Revises: 20260907_0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0012"
down_revision = "20260907_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("destination_url", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(240), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", sa.Text()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dedupe_key", name="uq_webhook_deliveries_dedupe_key"),
    )
    op.create_index("ix_webhook_deliveries_event_type", "webhook_deliveries", ["event_type"])
    op.create_index(
        "ix_webhook_deliveries_claim", "webhook_deliveries", ["state", "available_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_claim", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_event_type", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
