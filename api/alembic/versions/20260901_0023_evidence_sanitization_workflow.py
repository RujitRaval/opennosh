"""add disabled evidence sanitization workflow

Revision ID: 20260901_0023
Revises: 20260831_0022
Create Date: 2026-09-01 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0023"
down_revision: str | Sequence[str] | None = "20260831_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "evidence_upload_sessions"


def upgrade() -> None:
    op.execute(f"DROP TRIGGER guard_evidence_upload_session_update ON {_TABLE}")
    op.execute("DROP FUNCTION guard_evidence_upload_session_update()")
    op.drop_constraint(op.f(f"ck_{_TABLE}_state_shape_valid"), _TABLE, type_="check")
    op.drop_constraint(op.f(f"ck_{_TABLE}_failure_code_allowed"), _TABLE, type_="check")
    for column in (
        sa.Column("observed_revision_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("sanitized_object_key", sa.String(length=255), nullable=True),
        sa.Column("sanitized_media_type", sa.String(length=64), nullable=True),
        sa.Column("sanitized_byte_length", sa.Integer(), nullable=True),
        sa.Column("sanitized_sha256", sa.CHAR(length=64), nullable=True),
        sa.Column("sanitized_width", sa.Integer(), nullable=True),
        sa.Column("sanitized_height", sa.Integer(), nullable=True),
        sa.Column("sanitized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attached_evidence_id", sa.Uuid(), nullable=True),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preserved_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column(_TABLE, column)
    op.create_foreign_key(
        op.f(f"fk_{_TABLE}_attached_evidence_id_evidence_manifests"),
        _TABLE,
        "evidence_manifests",
        ["attached_evidence_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_evidence_upload_attached_evidence",
        _TABLE,
        ["attached_evidence_id"],
    )
    _create_t34_2_constraints()
    _create_t34_2_transition_guard()


def downgrade() -> None:
    op.execute(f"DROP TRIGGER guard_evidence_upload_session_update ON {_TABLE}")
    op.execute("DROP FUNCTION guard_evidence_upload_session_update()")
    for name in (
        "preserved_after_attachment",
        "attachment_after_sanitized",
        "sanitized_after_upload",
        "attachment_consistent",
        "sanitized_result_consistent",
        "sanitized_pixels_bounded",
        "sanitized_height_bounded",
        "sanitized_width_bounded",
        "sanitized_sha256_valid",
        "sanitized_byte_length_bounded",
        "sanitized_media_type_allowed",
        "sanitized_object_key_valid",
        "observed_revision_sha256_valid",
        "failure_code_allowed",
        "state_shape_valid",
    ):
        op.drop_constraint(op.f(f"ck_{_TABLE}_{name}"), _TABLE, type_="check")
    op.drop_constraint("uq_evidence_upload_attached_evidence", _TABLE, type_="unique")
    op.drop_constraint(
        op.f(f"fk_{_TABLE}_attached_evidence_id_evidence_manifests"),
        _TABLE,
        type_="foreignkey",
    )
    for name in (
        "preserved_at",
        "attached_at",
        "attached_evidence_id",
        "sanitized_at",
        "sanitized_height",
        "sanitized_width",
        "sanitized_sha256",
        "sanitized_byte_length",
        "sanitized_media_type",
        "sanitized_object_key",
        "observed_revision_sha256",
    ):
        op.drop_column(_TABLE, name)
    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_state_shape_valid"),
        _TABLE,
        "(state IN ('initiated','expired') AND observed_byte_length IS NULL "
        "AND observed_sha256 IS NULL AND uploaded_at IS NULL "
        "AND failure_code IS NULL AND failed_at IS NULL) OR "
        "(state = 'failed' AND observed_byte_length IS NULL "
        "AND observed_sha256 IS NULL AND uploaded_at IS NULL "
        "AND failure_code IS NOT NULL AND failed_at IS NOT NULL) OR "
        "(state IN ('uploaded','sanitizing','sanitized','attached','preserved') "
        "AND observed_byte_length IS NOT NULL AND observed_sha256 IS NOT NULL "
        "AND uploaded_at IS NOT NULL AND failure_code IS NULL AND failed_at IS NULL)",
    )
    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_failure_code_allowed"),
        _TABLE,
        "failure_code IS NULL OR failure_code IN ('object_missing','size_mismatch',"
        "'size_exceeded','media_type_mismatch','object_changed','capability_invalid',"
        "'expired','storage_unavailable')",
    )
    _create_t34_1_transition_guard()


def _create_t34_2_constraints() -> None:
    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_state_shape_valid"),
        _TABLE,
        "(state IN ('initiated','expired') AND observed_byte_length IS NULL "
        "AND observed_sha256 IS NULL AND uploaded_at IS NULL "
        "AND failure_code IS NULL AND failed_at IS NULL "
        "AND sanitized_object_key IS NULL AND attached_evidence_id IS NULL "
        "AND preserved_at IS NULL) OR "
        "(state = 'failed' AND failure_code IS NOT NULL AND failed_at IS NOT NULL "
        "AND sanitized_object_key IS NULL AND attached_evidence_id IS NULL "
        "AND preserved_at IS NULL) OR "
        "(state IN ('uploaded','sanitizing') "
        "AND observed_byte_length IS NOT NULL AND observed_sha256 IS NOT NULL "
        "AND uploaded_at IS NOT NULL AND failure_code IS NULL AND failed_at IS NULL "
        "AND sanitized_object_key IS NULL AND attached_evidence_id IS NULL "
        "AND preserved_at IS NULL) OR "
        "(state = 'sanitized' AND observed_byte_length IS NOT NULL "
        "AND observed_sha256 IS NOT NULL AND uploaded_at IS NOT NULL "
        "AND failure_code IS NULL AND failed_at IS NULL "
        "AND sanitized_object_key IS NOT NULL "
        "AND attached_evidence_id IS NULL AND preserved_at IS NULL) OR "
        "(state = 'attached' AND observed_byte_length IS NOT NULL "
        "AND observed_sha256 IS NOT NULL AND uploaded_at IS NOT NULL "
        "AND failure_code IS NULL AND failed_at IS NULL "
        "AND sanitized_object_key IS NOT NULL "
        "AND attached_evidence_id IS NOT NULL AND preserved_at IS NULL) OR "
        "(state = 'preserved' AND observed_byte_length IS NOT NULL "
        "AND observed_sha256 IS NOT NULL AND uploaded_at IS NOT NULL "
        "AND failure_code IS NULL AND failed_at IS NULL "
        "AND sanitized_object_key IS NOT NULL "
        "AND attached_evidence_id IS NOT NULL AND preserved_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_failure_code_allowed"),
        _TABLE,
        "failure_code IS NULL OR failure_code IN ('object_missing','size_mismatch',"
        "'size_exceeded','media_type_mismatch','object_changed','capability_invalid',"
        "'expired','storage_unavailable','signature_mismatch','decode_failed',"
        "'pixel_limit_exceeded','animation_unsupported','metadata_rewrite_failed',"
        "'sanitized_size_exceeded','malware_detected','scanner_unavailable',"
        "'sanitized_storage_unavailable','sanitized_storage_conflict')",
    )
    checks = {
        "observed_revision_sha256_valid": (
            "observed_revision_sha256 IS NULL "
            "OR observed_revision_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        "sanitized_object_key_valid": (
            "sanitized_object_key IS NULL OR sanitized_object_key ~ "
            "'^sanitized/[0-9a-f]{64}\\.png$'"
        ),
        "sanitized_media_type_allowed": (
            "sanitized_media_type IS NULL OR sanitized_media_type = 'image/png'"
        ),
        "sanitized_byte_length_bounded": (
            "sanitized_byte_length IS NULL OR sanitized_byte_length BETWEEN 1 AND 10485760"
        ),
        "sanitized_sha256_valid": (
            "sanitized_sha256 IS NULL OR sanitized_sha256 ~ '^[0-9a-f]{64}$'"
        ),
        "sanitized_width_bounded": (
            "sanitized_width IS NULL OR sanitized_width BETWEEN 1 AND 20000"
        ),
        "sanitized_height_bounded": (
            "sanitized_height IS NULL OR sanitized_height BETWEEN 1 AND 20000"
        ),
        "sanitized_pixels_bounded": (
            "sanitized_width IS NULL OR sanitized_height IS NULL "
            "OR sanitized_width * sanitized_height <= 20000000"
        ),
        "sanitized_result_consistent": (
            "(sanitized_object_key IS NULL AND sanitized_media_type IS NULL "
            "AND sanitized_byte_length IS NULL AND sanitized_sha256 IS NULL "
            "AND sanitized_width IS NULL AND sanitized_height IS NULL "
            "AND sanitized_at IS NULL) OR "
            "(sanitized_object_key IS NOT NULL AND sanitized_media_type IS NOT NULL "
            "AND sanitized_byte_length IS NOT NULL AND sanitized_sha256 IS NOT NULL "
            "AND sanitized_width IS NOT NULL AND sanitized_height IS NOT NULL "
            "AND sanitized_at IS NOT NULL)"
        ),
        "attachment_consistent": "(attached_evidence_id IS NULL) = (attached_at IS NULL)",
        "sanitized_after_upload": "sanitized_at IS NULL OR sanitized_at >= uploaded_at",
        "attachment_after_sanitized": "attached_at IS NULL OR attached_at >= sanitized_at",
        "preserved_after_attachment": "preserved_at IS NULL OR preserved_at >= attached_at",
    }
    for name, condition in checks.items():
        op.create_check_constraint(op.f(f"ck_{_TABLE}_{name}"), _TABLE, condition)


def _create_t34_2_transition_guard() -> None:
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
                OR NEW.updated_at < OLD.updated_at
                OR NEW.version != OLD.version + 1
                OR (
                    (NEW.observed_byte_length IS DISTINCT FROM OLD.observed_byte_length
                        OR NEW.observed_sha256 IS DISTINCT FROM OLD.observed_sha256
                        OR NEW.observed_revision_sha256
                            IS DISTINCT FROM OLD.observed_revision_sha256
                        OR NEW.uploaded_at IS DISTINCT FROM OLD.uploaded_at)
                    AND NOT (OLD.state = 'initiated' AND NEW.state = 'uploaded')
                )
                OR (
                    (NEW.failure_code IS DISTINCT FROM OLD.failure_code
                        OR NEW.failed_at IS DISTINCT FROM OLD.failed_at)
                    AND NEW.state != 'failed'
                )
                OR (
                    (NEW.sanitized_object_key IS DISTINCT FROM OLD.sanitized_object_key
                        OR NEW.sanitized_media_type IS DISTINCT FROM OLD.sanitized_media_type
                        OR NEW.sanitized_byte_length IS DISTINCT FROM OLD.sanitized_byte_length
                        OR NEW.sanitized_sha256 IS DISTINCT FROM OLD.sanitized_sha256
                        OR NEW.sanitized_width IS DISTINCT FROM OLD.sanitized_width
                        OR NEW.sanitized_height IS DISTINCT FROM OLD.sanitized_height
                        OR NEW.sanitized_at IS DISTINCT FROM OLD.sanitized_at)
                    AND NOT (OLD.state = 'sanitizing' AND NEW.state = 'sanitized')
                )
                OR (
                    (NEW.attached_evidence_id IS DISTINCT FROM OLD.attached_evidence_id
                        OR NEW.attached_at IS DISTINCT FROM OLD.attached_at)
                    AND NOT (OLD.state = 'sanitized' AND NEW.state = 'attached')
                )
                OR (
                    NEW.preserved_at IS DISTINCT FROM OLD.preserved_at
                    AND NOT (OLD.state = 'attached' AND NEW.state = 'preserved')
                )
                OR NOT (
                    (OLD.state = 'initiated' AND NEW.state = 'uploaded')
                    OR (OLD.state = 'initiated' AND NEW.state = 'expired')
                    OR (OLD.state IN ('initiated','uploaded','sanitizing') AND NEW.state = 'failed')
                    OR (OLD.state = 'uploaded' AND NEW.state = 'sanitizing')
                    OR (OLD.state = 'sanitizing' AND NEW.state = 'sanitized')
                    OR (OLD.state = 'sanitized' AND NEW.state = 'attached')
                    OR (OLD.state = 'attached' AND NEW.state = 'preserved')
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
        f"CREATE TRIGGER guard_evidence_upload_session_update "
        f"BEFORE UPDATE ON {_TABLE} FOR EACH ROW "
        "EXECUTE FUNCTION guard_evidence_upload_session_update()"
    )


def _create_t34_1_transition_guard() -> None:
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
                    (OLD.state = 'initiated' AND NEW.state = 'uploaded')
                    OR (OLD.state = 'initiated' AND NEW.state = 'expired')
                    OR (OLD.state = 'initiated' AND NEW.state = 'failed')
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
        f"CREATE TRIGGER guard_evidence_upload_session_update "
        f"BEFORE UPDATE ON {_TABLE} FOR EACH ROW "
        "EXECUTE FUNCTION guard_evidence_upload_session_update()"
    )
