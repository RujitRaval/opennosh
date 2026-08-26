"""add signed publication receipt projections and lineage

Revision ID: 20260826_0017
Revises: 20260826_0016
Create Date: 2026-08-26 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_0017"
down_revision: str | Sequence[str] | None = "20260826_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "publication_intents",
        sa.Column(
            "event_type",
            sa.String(length=32),
            server_default="publication",
            nullable=False,
        ),
    )
    op.add_column(
        "publication_intents",
        sa.Column("prior_receipt_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "publication_intents",
        sa.Column(
            "evidence_manifest_digests_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "publication_intents",
        sa.Column(
            "evidence_acknowledgements_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE publication_intents AS pi
        SET evidence_manifest_digests_json = evidence.digests
        FROM (
            SELECT source_draft_id, source_draft_version,
                   jsonb_agg(manifest_digest ORDER BY manifest_digest) AS digests
            FROM evidence_manifests
            WHERE public_state IS NOT NULL AND public_state != 'tombstoned'
            GROUP BY source_draft_id, source_draft_version
        ) AS evidence
        WHERE evidence.source_draft_id = pi.source_draft_id
          AND evidence.source_draft_version = pi.source_draft_version
        """
    )
    op.execute(
        """
        UPDATE publication_intents AS pi
        SET evidence_acknowledgements_json = evidence.acknowledgements
        FROM (
            SELECT em.source_draft_id, em.source_draft_version,
                   jsonb_agg(
                       jsonb_build_object(
                           'schema_version', acknowledgement.schema_version,
                           'evidence_id', acknowledgement.evidence_id,
                           'evidence_class', acknowledgement.evidence_class,
                           'manifest_digest', acknowledgement.manifest_digest,
                           'kind', acknowledgement.acknowledgement_kind,
                           'destination', acknowledgement.destination,
                           'content_digest', acknowledgement.content_digest,
                           'external_reference', acknowledgement.external_reference,
                           'verified_at', acknowledgement.verified_at,
                           'adapter_identity', acknowledgement.adapter_identity,
                           'adapter_version', acknowledgement.adapter_version
                       )
                       ORDER BY acknowledgement.acknowledgement_kind,
                                acknowledgement.destination
                   ) AS acknowledgements
            FROM evidence_manifests AS em
            JOIN evidence_durable_acknowledgements AS acknowledgement
              ON acknowledgement.evidence_id = em.id
            WHERE em.public_state IS NOT NULL AND em.public_state != 'tombstoned'
            GROUP BY em.source_draft_id, em.source_draft_version
        ) AS evidence
        WHERE evidence.source_draft_id = pi.source_draft_id
          AND evidence.source_draft_version = pi.source_draft_version
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM publication_intents
                WHERE evidence_manifest_digests_json IS NULL
                   OR evidence_acknowledgements_json IS NULL
            ) THEN
                RAISE EXCEPTION
                    'T5 requires frozen evidence for every publication intent';
            END IF;
        END $$;
        """
    )
    op.alter_column("publication_intents", "evidence_manifest_digests_json", nullable=False)
    op.alter_column("publication_intents", "evidence_acknowledgements_json", nullable=False)
    op.create_check_constraint(
        op.f("ck_publication_intents_evidence_manifest_digests_bounded"),
        "publication_intents",
        "jsonb_typeof(evidence_manifest_digests_json) = 'array' AND "
        "jsonb_array_length(evidence_manifest_digests_json) BETWEEN 1 AND 128",
    )
    op.create_check_constraint(
        op.f("ck_publication_intents_evidence_acknowledgements_bounded"),
        "publication_intents",
        "jsonb_typeof(evidence_acknowledgements_json) = 'array' AND "
        "jsonb_array_length(evidence_acknowledgements_json) BETWEEN 1 AND 128",
    )
    op.create_check_constraint(
        op.f("ck_publication_intents_event_type_allowed"),
        "publication_intents",
        "event_type IN ('publication', 'correction', 'revocation')",
    )
    op.create_check_constraint(
        op.f("ck_publication_intents_receipt_lineage_consistent"),
        "publication_intents",
        "(event_type = 'publication' AND prior_receipt_digest IS NULL) OR "
        "(event_type IN ('correction', 'revocation') AND "
        "prior_receipt_digest IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_publication_intents_prior_receipt_digest_sha256"),
        "publication_intents",
        "prior_receipt_digest IS NULL OR prior_receipt_digest ~ '^[0-9a-f]{64}$'",
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM accepted_events) THEN
                RAISE EXCEPTION
                    'T5 refuses accepted events without canonical signed receipt rows';
            END IF;
        END $$;
        """
    )
    op.create_table(
        "publication_receipts",
        sa.Column("publication_intent_id", sa.Uuid(), nullable=True),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("receipt_digest", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("prior_receipt_digest", sa.String(length=64), nullable=True),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("record_id", sa.String(length=160), nullable=False),
        sa.Column("envelope_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signature_key_id", sa.String(length=64), nullable=False),
        sa.Column("registry_reference", sa.String(length=1024), nullable=False),
        sa.Column("artifact_reference", sa.String(length=1024), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('publication', 'correction', 'revocation')",
            name=op.f("ck_publication_receipts_event_type_allowed"),
        ),
        sa.CheckConstraint(
            "(event_type = 'publication' AND prior_receipt_digest IS NULL) OR "
            "(event_type IN ('correction', 'revocation') AND "
            "prior_receipt_digest IS NOT NULL)",
            name=op.f("ck_publication_receipts_lineage_consistent"),
        ),
        sa.CheckConstraint(
            "prior_receipt_digest IS NULL OR prior_receipt_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_publication_receipts_prior_receipt_digest_sha256"),
        ),
        sa.CheckConstraint(
            "receipt_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_publication_receipts_receipt_digest_sha256"),
        ),
        sa.CheckConstraint(
            "schema_version = '1.0'",
            name=op.f("ck_publication_receipts_schema_version_supported"),
        ),
        sa.ForeignKeyConstraint(
            ["prior_receipt_digest"],
            ["publication_receipts.receipt_digest"],
            name=op.f("fk_publication_receipts_prior_receipt_digest_publication_receipts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_intent_id"],
            ["publication_intents.id"],
            name=op.f("fk_publication_receipts_publication_intent_id_publication_intents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publication_receipts")),
        sa.UniqueConstraint(
            "publication_intent_id",
            name=op.f("uq_publication_receipts_publication_intent_id"),
        ),
        sa.UniqueConstraint("publication_id", name=op.f("uq_publication_receipts_publication_id")),
        sa.UniqueConstraint("receipt_digest", name=op.f("uq_publication_receipts_receipt_digest")),
    )
    op.create_foreign_key(
        "fk_pub_intent_prior_receipt",
        "publication_intents",
        "publication_receipts",
        ["prior_receipt_digest"],
        ["receipt_digest"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_publication_receipts_pack_time",
        "publication_receipts",
        ["pack_id", "published_at"],
    )
    op.alter_column("accepted_events", "publication_intent_id", nullable=True)
    op.create_unique_constraint(
        op.f("uq_accepted_events_receipt_digest"),
        "accepted_events",
        ["receipt_digest"],
    )
    op.create_foreign_key(
        op.f("fk_accepted_events_receipt_digest_publication_receipts"),
        "accepted_events",
        "publication_receipts",
        ["receipt_digest"],
        ["receipt_digest"],
        ondelete="RESTRICT",
    )
    op.execute(
        """
        CREATE FUNCTION opennosh_guard_append_only_publication_receipt() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'signed publication receipts are append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER guard_append_only_publication_receipt
        BEFORE UPDATE OR DELETE ON publication_receipts
        FOR EACH ROW EXECUTE FUNCTION opennosh_guard_append_only_publication_receipt()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM publication_receipts) THEN
                RAISE EXCEPTION
                    'T5 refuses to discard canonical signed receipt rows';
            END IF;
        END $$;
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS guard_append_only_publication_receipt ON publication_receipts"
    )
    op.execute("DROP FUNCTION IF EXISTS opennosh_guard_append_only_publication_receipt()")
    op.drop_constraint(
        op.f("fk_accepted_events_receipt_digest_publication_receipts"),
        "accepted_events",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("uq_accepted_events_receipt_digest"),
        "accepted_events",
        type_="unique",
    )
    op.alter_column("accepted_events", "publication_intent_id", nullable=False)
    op.drop_constraint(
        "fk_pub_intent_prior_receipt",
        "publication_intents",
        type_="foreignkey",
    )
    op.drop_index("ix_publication_receipts_pack_time", table_name="publication_receipts")
    op.drop_table("publication_receipts")
    op.drop_constraint(
        op.f("ck_publication_intents_prior_receipt_digest_sha256"),
        "publication_intents",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_publication_intents_receipt_lineage_consistent"),
        "publication_intents",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_publication_intents_event_type_allowed"),
        "publication_intents",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_publication_intents_evidence_acknowledgements_bounded"),
        "publication_intents",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_publication_intents_evidence_manifest_digests_bounded"),
        "publication_intents",
        type_="check",
    )
    op.drop_column("publication_intents", "evidence_acknowledgements_json")
    op.drop_column("publication_intents", "evidence_manifest_digests_json")
    op.drop_column("publication_intents", "prior_receipt_digest")
    op.drop_column("publication_intents", "event_type")
