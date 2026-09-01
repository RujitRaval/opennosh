"""add disabled hosted evidence upload sessions

Revision ID: 20260831_0022
Revises: 20260829_0021
Create Date: 2026-08-31 20:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0022"
down_revision: str | Sequence[str] | None = "20260829_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_upload_sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("source_draft_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), server_default="initiated", nullable=False),
        sa.Column("object_key", sa.String(length=255), nullable=False),
        sa.Column("declared_media_type", sa.String(length=64), nullable=False),
        sa.Column("declared_byte_length", sa.Integer(), nullable=False),
        sa.Column("observed_byte_length", sa.Integer(), nullable=True),
        sa.Column("observed_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("capability_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("request_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=40), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_draft_version > 0",
            name=op.f("ck_evidence_upload_sessions_source_draft_version_positive"),
        ),
        sa.CheckConstraint(
            "state IN ('initiated','uploaded','sanitizing','sanitized','attached',"
            "'preserved','expired','failed')",
            name=op.f("ck_evidence_upload_sessions_state_allowed"),
        ),
        sa.CheckConstraint(
            "declared_media_type IN ('image/jpeg','image/png','image/webp')",
            name=op.f("ck_evidence_upload_sessions_declared_media_type_allowed"),
        ),
        sa.CheckConstraint(
            "declared_byte_length BETWEEN 1 AND 10485760",
            name=op.f("ck_evidence_upload_sessions_declared_byte_length_bounded"),
        ),
        sa.CheckConstraint(
            "observed_byte_length IS NULL OR observed_byte_length BETWEEN 1 AND 10485760",
            name=op.f("ck_evidence_upload_sessions_observed_byte_length_bounded"),
        ),
        sa.CheckConstraint(
            "observed_sha256 IS NULL OR observed_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_evidence_upload_sessions_observed_sha256_valid"),
        ),
        sa.CheckConstraint(
            "capability_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_evidence_upload_sessions_capability_hash_valid"),
        ),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_evidence_upload_sessions_idempotency_key_hash_valid"),
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_evidence_upload_sessions_request_hash_valid"),
        ),
        sa.CheckConstraint(
            "object_key ~ '^quarantine/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            "[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name=op.f("ck_evidence_upload_sessions_object_key_valid"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '10 minutes'",
            name=op.f("ck_evidence_upload_sessions_expiry_bounded"),
        ),
        sa.CheckConstraint(
            "uploaded_at IS NULL OR uploaded_at >= created_at",
            name=op.f("ck_evidence_upload_sessions_upload_after_creation"),
        ),
        sa.CheckConstraint(
            "failed_at IS NULL OR failed_at >= created_at",
            name=op.f("ck_evidence_upload_sessions_failure_after_creation"),
        ),
        sa.CheckConstraint(
            "(failure_code IS NULL) = (failed_at IS NULL)",
            name=op.f("ck_evidence_upload_sessions_failure_consistent"),
        ),
        sa.CheckConstraint(
            "state != 'failed' OR failure_code IS NOT NULL",
            name=op.f("ck_evidence_upload_sessions_failed_state_typed"),
        ),
        sa.CheckConstraint(
            "(state IN ('initiated','expired') AND observed_byte_length IS NULL "
            "AND observed_sha256 IS NULL AND uploaded_at IS NULL "
            "AND failure_code IS NULL AND failed_at IS NULL) OR "
            "(state = 'failed' AND observed_byte_length IS NULL "
            "AND observed_sha256 IS NULL AND uploaded_at IS NULL "
            "AND failure_code IS NOT NULL AND failed_at IS NOT NULL) OR "
            "(state IN ('uploaded','sanitizing','sanitized','attached','preserved') "
            "AND observed_byte_length IS NOT NULL AND observed_sha256 IS NOT NULL "
            "AND uploaded_at IS NOT NULL AND failure_code IS NULL AND failed_at IS NULL)",
            name=op.f("ck_evidence_upload_sessions_state_shape_valid"),
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code IN ('object_missing','size_mismatch',"
            "'size_exceeded','media_type_mismatch','object_changed','capability_invalid',"
            "'expired','storage_unavailable')",
            name=op.f("ck_evidence_upload_sessions_failure_code_allowed"),
        ),
        sa.CheckConstraint(
            "version > 0",
            name=op.f("ck_evidence_upload_sessions_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_evidence_upload_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["contribution_drafts.id"],
            name=op.f("fk_evidence_upload_sessions_draft_id_contribution_drafts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_upload_sessions")),
        sa.UniqueConstraint(
            "user_id",
            "draft_id",
            "idempotency_key_hash",
            name="uq_evidence_upload_user_draft_idempotency",
        ),
        sa.UniqueConstraint("object_key", name="uq_evidence_upload_object_key"),
    )
    op.create_index(
        "ix_evidence_upload_user_draft_created",
        "evidence_upload_sessions",
        ["user_id", "draft_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_evidence_upload_state_expires",
        "evidence_upload_sessions",
        ["state", "expires_at"],
    )
    op.create_index(
        "ix_evidence_upload_draft_version",
        "evidence_upload_sessions",
        ["draft_id", "source_draft_version"],
    )
    op.execute(
        """
        CREATE FUNCTION guard_evidence_upload_session_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR NEW.user_id IS DISTINCT FROM OLD.user_id
                OR NEW.draft_id IS DISTINCT FROM OLD.draft_id
                OR NEW.source_draft_version IS DISTINCT FROM OLD.source_draft_version
                OR NEW.object_key IS DISTINCT FROM OLD.object_key
                OR NEW.declared_media_type IS DISTINCT FROM OLD.declared_media_type
                OR NEW.declared_byte_length IS DISTINCT FROM OLD.declared_byte_length
                OR NEW.capability_hash IS DISTINCT FROM OLD.capability_hash
                OR NEW.idempotency_key_hash IS DISTINCT FROM OLD.idempotency_key_hash
                OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
                OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                OR NEW.version != OLD.version + 1
                OR NOT (
                    (OLD.state = 'initiated' AND NEW.state = 'uploaded'
                        AND NEW.observed_byte_length IS NOT NULL
                        AND NEW.observed_sha256 IS NOT NULL
                        AND NEW.uploaded_at IS NOT NULL
                        AND NEW.failure_code IS NULL
                        AND NEW.failed_at IS NULL)
                    OR (OLD.state = 'initiated' AND NEW.state = 'expired'
                        AND NEW.observed_byte_length IS NULL
                        AND NEW.observed_sha256 IS NULL
                        AND NEW.uploaded_at IS NULL
                        AND NEW.failure_code IS NULL
                        AND NEW.failed_at IS NULL)
                    OR (OLD.state = 'initiated' AND NEW.state = 'failed'
                        AND NEW.observed_byte_length IS NULL
                        AND NEW.observed_sha256 IS NULL
                        AND NEW.uploaded_at IS NULL
                        AND NEW.failure_code IS NOT NULL
                        AND NEW.failed_at IS NOT NULL)
                )
            THEN
                RAISE EXCEPTION 'evidence_upload_session_transition_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_evidence_upload_session_update "
        "BEFORE UPDATE ON evidence_upload_sessions FOR EACH ROW "
        "EXECUTE FUNCTION guard_evidence_upload_session_update()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS guard_evidence_upload_session_update ON evidence_upload_sessions"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_evidence_upload_session_update()")
    op.drop_index("ix_evidence_upload_draft_version", table_name="evidence_upload_sessions")
    op.drop_index("ix_evidence_upload_state_expires", table_name="evidence_upload_sessions")
    op.drop_index("ix_evidence_upload_user_draft_created", table_name="evidence_upload_sessions")
    op.drop_table("evidence_upload_sessions")
