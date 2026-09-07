"""Store readable comma-separated proposal domains rather than derived slugs.

Revision ID: 20260907_0010
Revises: 20260831_0008
"""

from __future__ import annotations

from alembic import op

revision = "20260907_0010"
down_revision = "20260831_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE proposals
        SET domain = json_extract(document, '$.domain')
        WHERE input_valid = 1
          AND json_type(document, '$.domain') = 'text'
          AND trim(json_extract(document, '$.domain')) != ''
        """
    )


def downgrade() -> None:
    # Raw domain text cannot be losslessly converted back to the former slug.
    pass
