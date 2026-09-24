"""Generalize webhook deliveries into durable outbound deliveries.

Revision ID: 20260924_0016
Revises: 20260916_0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260924_0016"
down_revision = "20260916_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("webhook_deliveries", "outbound_deliveries")
    with op.batch_alter_table("outbound_deliveries") as batch:
        batch.alter_column("destination_url", new_column_name="destination")
        batch.add_column(sa.Column("delivery_type", sa.String(32), nullable=False, server_default="discord"))
        batch.drop_constraint("uq_webhook_deliveries_dedupe_key", type_="unique")
        batch.create_unique_constraint("uq_outbound_deliveries_dedupe_key", ["dedupe_key"])
        batch.drop_index("ix_webhook_deliveries_event_type")
        batch.drop_index("ix_webhook_deliveries_claim")
        batch.create_index("ix_outbound_deliveries_event_type", ["event_type"])
        batch.create_index("ix_outbound_deliveries_delivery_type", ["delivery_type"])
        batch.create_index("ix_outbound_deliveries_claim", ["state", "available_at"])


def downgrade() -> None:
    with op.batch_alter_table("outbound_deliveries") as batch:
        batch.drop_index("ix_outbound_deliveries_claim")
        batch.drop_index("ix_outbound_deliveries_delivery_type")
        batch.drop_index("ix_outbound_deliveries_event_type")
        batch.drop_constraint("uq_outbound_deliveries_dedupe_key", type_="unique")
        batch.create_unique_constraint("uq_webhook_deliveries_dedupe_key", ["dedupe_key"])
        batch.drop_column("delivery_type")
        batch.alter_column("destination", new_column_name="destination_url")
        batch.create_index("ix_webhook_deliveries_event_type", ["event_type"])
        batch.create_index("ix_webhook_deliveries_claim", ["state", "available_at"])
    op.rename_table("outbound_deliveries", "webhook_deliveries")
