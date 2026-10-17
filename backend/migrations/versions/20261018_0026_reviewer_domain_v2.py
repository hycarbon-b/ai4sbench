"""Store reviewer expertise in the same domain/field shape as proposals.

Revision ID: 20261018_0026
Revises: 20261015_0025
"""

from __future__ import annotations

import json
import re

import sqlalchemy as sa
from alembic import op

revision = "20261018_0026"
down_revision = "20261015_0025"
branch_labels = None
depends_on = None

DOMAIN_LABELS = frozenset(
    {
        "Materials Science",
        "Physics",
        "Chemistry",
        "Biology",
        "AI / ML",
        "Applied Mathematics",
        "Interdisciplinary",
    }
)


def _labels(value: str, application_id: str) -> list[str]:
    try:
        labels = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"reviewer {application_id} has invalid domains_display JSON") from exc
    if (
        not isinstance(labels, list)
        or not labels
        or any(not isinstance(label, str) or label not in DOMAIN_LABELS for label in labels)
        or len(set(labels)) != len(labels)
    ):
        raise ValueError(f"reviewer {application_id} has incompatible proposal domains")
    return labels


def _slug(value: str) -> str:
    return re.sub(r"^-+|-+$", "", re.sub(r"-{2,}", "-", re.sub(r"[^a-z]+", "-", value.lower())))[:79]


def upgrade() -> None:
    connection = op.get_bind()
    # Check every row before changing the schema, so a bad row cannot be silently lost.
    rows = connection.execute(
        sa.text("SELECT id, schema_version, domains_display, subfield FROM reviewer_applications")
    ).mappings().all()
    converted = []
    for row in rows:
        application_id = row["id"]
        if row["schema_version"] != "tb-reviewer-application/v1":
            raise ValueError(f"reviewer {application_id} has an unexpected schema version")
        domain = ", ".join(_labels(row["domains_display"], application_id))
        field_name = row["subfield"]
        if len(domain) > 160 or (field_name is not None and len(field_name) > 160):
            raise ValueError(f"reviewer {application_id} exceeds the v2 field limit")
        converted.append((application_id, domain, field_name))

    with op.batch_alter_table("reviewer_applications") as batch:
        batch.add_column(sa.Column("domain", sa.String(160), nullable=True))
        batch.add_column(sa.Column("field_name", sa.String(160), nullable=True))
    for application_id, domain, field_name in converted:
        connection.execute(
            sa.text(
                "UPDATE reviewer_applications SET domain = :domain, field_name = :field_name, "
                "schema_version = 'tb-reviewer-application/v2' WHERE id = :id"
            ),
            {"id": application_id, "domain": domain, "field_name": field_name},
        )
    with op.batch_alter_table("reviewer_applications") as batch:
        batch.alter_column("domain", existing_type=sa.String(160), nullable=False)
        batch.drop_column("domains")
        batch.drop_column("domains_display")
        batch.drop_column("field")
        batch.drop_column("subfield")


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, domain, field_name FROM reviewer_applications")
    ).mappings().all()
    converted = []
    for row in rows:
        labels = [label.strip() for label in row["domain"].split(",")]
        if not labels or any(label not in DOMAIN_LABELS for label in labels):
            raise ValueError(f"reviewer {row['id']} has incompatible proposal domains")
        converted.append((row["id"], labels, row["field_name"]))

    with op.batch_alter_table("reviewer_applications") as batch:
        batch.add_column(sa.Column("domains", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("domains_display", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("field", sa.String(80), nullable=True))
        batch.add_column(sa.Column("subfield", sa.String(200), nullable=True))
    for application_id, labels, field_name in converted:
        connection.execute(
            sa.text(
                "UPDATE reviewer_applications SET domains = :domains, domains_display = :domains_display, "
                "field = :field, subfield = :subfield, schema_version = 'tb-reviewer-application/v1' "
                "WHERE id = :id"
            ),
            {
                "id": application_id,
                "domains": json.dumps([_slug(label) for label in labels]),
                "domains_display": json.dumps(labels),
                "field": _slug(field_name) if field_name else None,
                "subfield": field_name,
            },
        )
    with op.batch_alter_table("reviewer_applications") as batch:
        batch.alter_column("domains", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("domains_display", existing_type=sa.JSON(), nullable=False)
        batch.drop_column("domain")
        batch.drop_column("field_name")
