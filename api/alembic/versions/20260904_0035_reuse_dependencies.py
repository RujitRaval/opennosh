"""add proof-bound reuse dependency projection

Revision ID: 20260904_0035
Revises: 20260904_0034
Create Date: 2026-09-04 05:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_0035"
down_revision: str | Sequence[str] | None = "20260904_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reuse_dependencies",
        sa.Column("declaration_id", sa.Uuid(), nullable=False),
        sa.Column("source_pack_id", sa.String(length=160), nullable=False),
        sa.Column("source_release_id", sa.String(length=160), nullable=False),
        sa.Column("source_artifact_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("dependency_kind", sa.String(length=24), nullable=False),
        sa.Column("evidence_event_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_pack_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name=op.f("ck_reuse_dependencies_source_pack_id_safe"),
        ),
        sa.CheckConstraint(
            "source_release_id ~ '^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$'",
            name=op.f("ck_reuse_dependencies_source_release_id_version"),
        ),
        sa.CheckConstraint(
            "source_artifact_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_reuse_dependencies_source_artifact_digest_sha256"),
        ),
        sa.CheckConstraint(
            "dependency_kind IN ('runtime','data','research','derived')",
            name=op.f("ck_reuse_dependencies_dependency_kind_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["declaration_id"],
            ["reuse_declarations.id"],
            name=op.f("fk_reuse_dependencies_declaration_id_reuse_declarations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_event_id"],
            ["reuse_declaration_events.id"],
            name=op.f("fk_reuse_dependencies_evidence_event_id_reuse_declaration_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reuse_dependencies")),
        sa.UniqueConstraint(
            "declaration_id",
            "source_pack_id",
            "source_release_id",
            "dependency_kind",
            name="uq_reuse_dependency_identity",
        ),
    )
    op.create_index(
        "ix_reuse_dependencies_declaration",
        "reuse_dependencies",
        ["declaration_id", "created_at"],
    )
    op.create_index(
        "ix_reuse_dependencies_source",
        "reuse_dependencies",
        ["source_pack_id", "source_release_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_reuse_dependencies_source", table_name="reuse_dependencies")
    op.drop_index("ix_reuse_dependencies_declaration", table_name="reuse_dependencies")
    op.drop_table("reuse_dependencies")
