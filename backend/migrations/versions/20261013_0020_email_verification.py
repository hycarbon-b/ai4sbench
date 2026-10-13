"""Add purpose-scoped, one-use email verification challenges.

Revision ID: 20261013_0020
Revises: 20261011_0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261013_0020"
down_revision = "20261011_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_verification_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("subject_id", sa.String(100), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.Integer()),
        sa.Column("consumed_at", sa.Integer()),
    )
    op.create_index(
        "ix_email_verifications_email_created",
        "email_verification_challenges",
        ["email", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_verifications_email_created", table_name="email_verification_challenges")
    op.drop_table("email_verification_challenges")
