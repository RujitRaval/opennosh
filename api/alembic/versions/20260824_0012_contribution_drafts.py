"""add contribution draft and review intake

Revision ID: 20260824_0012
Revises: 20260823_0011
Create Date: 2026-08-24 00:12:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260824_0012"
down_revision: str | Sequence[str] | None = "20260823_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contribution_drafts",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("client_draft_id", sa.String(length=120), nullable=True),
        sa.Column("workflow_version", sa.String(length=16), nullable=False, server_default="1"),
        sa.Column("draft_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("review_state", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column(
            "fields_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "duplicate_candidates_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("submission_key_hash", sa.String(length=64), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "draft_version > 0", name=op.f("ck_contribution_drafts_draft_version_positive")
        ),
        sa.CheckConstraint(
            "workflow_version = '1'", name=op.f("ck_contribution_drafts_workflow_version_supported")
        ),
        sa.CheckConstraint(
            "review_state IN ('draft', 'in_review', 'changes_requested', 'approved', "
            "'publication_pending', 'published')",
            name=op.f("ck_contribution_drafts_review_state_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_contribution_drafts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contribution_drafts")),
        sa.UniqueConstraint("submission_id", name=op.f("uq_contribution_drafts_submission_id")),
        sa.UniqueConstraint(
            "user_id",
            "client_draft_id",
            name=op.f("uq_contribution_drafts_user_client_draft_unique"),
        ),
    )
    op.create_index(
        op.f("ix_contribution_drafts_user_id"), "contribution_drafts", ["user_id"], unique=False
    )
    op.create_index(
        "ix_contribution_drafts_user_updated",
        "contribution_drafts",
        ["user_id", "updated_at"],
        unique=False,
    )
    op.create_table(
        "contribution_draft_operations",
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("resulting_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["contribution_drafts.id"],
            name=op.f("fk_contribution_draft_operations_draft_id_contribution_drafts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "draft_id", "operation_id", name=op.f("pk_contribution_draft_operations")
        ),
    )
    op.create_index(
        op.f("ix_contribution_draft_operations_created_at"),
        "contribution_draft_operations",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("contribution_draft_operations")
    op.drop_index("ix_contribution_drafts_user_updated", table_name="contribution_drafts")
    op.drop_index(op.f("ix_contribution_drafts_user_id"), table_name="contribution_drafts")
    op.drop_table("contribution_drafts")
