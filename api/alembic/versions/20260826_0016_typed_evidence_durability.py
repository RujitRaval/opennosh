"""add typed evidence manifests and durable acknowledgements

Revision ID: 20260826_0016
Revises: 20260826_0015
Create Date: 2026-08-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_0016"
down_revision: str | Sequence[str] | None = "20260826_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contribution_drafts",
        sa.Column("submission_request_hash", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "evidence_manifests",
        sa.Column("source_draft_id", sa.Uuid(), nullable=False),
        sa.Column("source_draft_version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("evidence_class", sa.String(length=64), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("public_state", sa.String(length=40), nullable=True),
        sa.Column("preservation_failure_code", sa.String(length=120), nullable=True),
        sa.Column("preservation_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "evidence_class IN ('sanitized_media', 'versioned_public_dataset', "
            "'public_document', 'maintainer_attestation')",
            name=op.f("ck_evidence_manifests_evidence_class_allowed"),
        ),
        sa.CheckConstraint(
            "manifest_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_evidence_manifests_manifest_digest_sha256"),
        ),
        sa.CheckConstraint(
            "public_state IS NULL OR public_state IN ('evidence_preserved', "
            "'source_verified', 'reference_preserved', 'reference_only', 'attested', "
            "'tombstoned')",
            name=op.f("ck_evidence_manifests_public_state_allowed"),
        ),
        sa.CheckConstraint(
            "(preservation_failure_code IS NULL) = (preservation_failed_at IS NULL) "
            "AND (preservation_failure_code IS NULL OR public_state IS NULL)",
            name=op.f("ck_evidence_manifests_preservation_failure_consistent"),
        ),
        sa.CheckConstraint(
            "schema_version = '1.0'",
            name=op.f("ck_evidence_manifests_schema_version_supported"),
        ),
        sa.CheckConstraint(
            "source_draft_version > 0",
            name=op.f("ck_evidence_manifests_source_draft_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_id"],
            ["contribution_drafts.id"],
            name=op.f("fk_evidence_manifests_source_draft_id_contribution_drafts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_manifests")),
        sa.UniqueConstraint("manifest_digest", name=op.f("uq_evidence_manifests_manifest_digest")),
        sa.UniqueConstraint(
            "source_draft_id",
            "source_draft_version",
            name="uq_evidence_manifest_draft_version",
        ),
    )
    op.create_index(
        "ix_evidence_manifests_draft_created",
        "evidence_manifests",
        ["source_draft_id", "created_at"],
    )
    op.create_table(
        "evidence_durable_acknowledgements",
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("evidence_class", sa.String(length=64), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("acknowledgement_kind", sa.String(length=80), nullable=False),
        sa.Column("destination", sa.String(length=2048), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("external_reference", sa.String(length=2048), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adapter_identity", sa.String(length=255), nullable=False),
        sa.Column("adapter_version", sa.String(length=80), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_evidence_durable_acknowledgements_content_digest_sha256"),
        ),
        sa.CheckConstraint(
            "manifest_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_evidence_durable_acknowledgements_manifest_digest_sha256"),
        ),
        sa.CheckConstraint(
            "schema_version = '1.0'",
            name=op.f("ck_evidence_durable_acknowledgements_schema_version_supported"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_manifests.id"],
            name=op.f(
                "fk_evidence_durable_acknowledgements_evidence_id_evidence_manifests"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_evidence_durable_acknowledgements")
        ),
        sa.UniqueConstraint(
            "evidence_id",
            "acknowledgement_kind",
            "destination",
            name="uq_evidence_acknowledgement_kind_destination",
        ),
    )
    op.create_index(
        "ix_evidence_acknowledgements_evidence_verified",
        "evidence_durable_acknowledgements",
        ["evidence_id", "verified_at"],
    )
    op.create_table(
        "evidence_removal_tombstones",
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("prior_state", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("removed_by_actor_id", sa.Uuid(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "manifest_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_evidence_removal_tombstones_manifest_digest_sha256"),
        ),
        sa.CheckConstraint(
            "prior_state IN ('evidence_preserved', 'source_verified', "
            "'reference_preserved', 'reference_only', 'attested')",
            name=op.f("ck_evidence_removal_tombstones_prior_state_allowed"),
        ),
        sa.CheckConstraint(
            "schema_version = '1.0'",
            name=op.f("ck_evidence_removal_tombstones_schema_version_supported"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence_manifests.id"],
            name=op.f("fk_evidence_removal_tombstones_evidence_id_evidence_manifests"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["removed_by_actor_id"],
            ["users.id"],
            name=op.f("fk_evidence_removal_tombstones_removed_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("evidence_id", name=op.f("pk_evidence_removal_tombstones")),
    )
    op.execute(
        """
        CREATE FUNCTION opennosh_guard_evidence_manifest() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'evidence manifests are immutable';
          END IF;
          IF (to_jsonb(NEW) - ARRAY['public_state', 'preservation_failure_code',
                                    'preservation_failed_at']) IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY['public_state', 'preservation_failure_code',
                                    'preservation_failed_at']) THEN
            RAISE EXCEPTION 'evidence manifest identity is immutable';
          END IF;
          IF NEW.public_state IS NOT DISTINCT FROM OLD.public_state AND
             NEW.preservation_failure_code IS NOT DISTINCT FROM
               OLD.preservation_failure_code AND
             NEW.preservation_failed_at IS NOT DISTINCT FROM OLD.preservation_failed_at THEN
            RETURN NEW;
          END IF;
          IF OLD.public_state IS NULL AND OLD.preservation_failure_code IS NULL AND
             NEW.preservation_failure_code IS NULL AND NEW.public_state IN (
            'evidence_preserved', 'source_verified', 'reference_preserved',
            'reference_only', 'attested'
          ) THEN
            RETURN NEW;
          END IF;
          IF OLD.public_state IS NULL AND OLD.preservation_failure_code IS NULL AND
             NEW.public_state IS NULL AND NEW.preservation_failure_code IS NOT NULL AND
             NEW.preservation_failed_at IS NOT NULL THEN
            RETURN NEW;
          END IF;
          IF OLD.public_state IN (
            'evidence_preserved', 'source_verified', 'reference_preserved',
            'reference_only', 'attested'
          ) AND NEW.public_state = 'tombstoned' THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'evidence public state transition is not allowed';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION opennosh_guard_append_only_evidence() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER evidence_manifests_immutable
        BEFORE UPDATE OR DELETE ON evidence_manifests
        FOR EACH ROW EXECUTE FUNCTION opennosh_guard_evidence_manifest()
        """
    )
    for table in (
        "evidence_durable_acknowledgements",
        "evidence_removal_tombstones",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION opennosh_guard_append_only_evidence()
            """
        )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER evidence_removal_tombstones_append_only "
        "ON evidence_removal_tombstones"
    )
    op.execute(
        "DROP TRIGGER evidence_durable_acknowledgements_append_only "
        "ON evidence_durable_acknowledgements"
    )
    op.execute("DROP TRIGGER evidence_manifests_immutable ON evidence_manifests")
    op.execute("DROP FUNCTION opennosh_guard_append_only_evidence()")
    op.execute("DROP FUNCTION opennosh_guard_evidence_manifest()")
    op.drop_table("evidence_removal_tombstones")
    op.drop_index(
        "ix_evidence_acknowledgements_evidence_verified",
        table_name="evidence_durable_acknowledgements",
    )
    op.drop_table("evidence_durable_acknowledgements")
    op.drop_index("ix_evidence_manifests_draft_created", table_name="evidence_manifests")
    op.drop_table("evidence_manifests")
    op.drop_column("contribution_drafts", "submission_request_hash")
