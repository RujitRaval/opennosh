"""add immutable federation release ledger

Revision ID: 20260902_0025
Revises: 20260901_0024
Create Date: 2026-09-02 01:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0025"
down_revision: str | Sequence[str] | None = "20260901_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "federation_releases",
        sa.Column("maintainer_id", sa.Uuid(), nullable=False),
        sa.Column("role_key_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_event_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("repository", sa.String(length=201), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("release_version", sa.String(length=255), nullable=False),
        sa.Column(
            "statement_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("statement_digest", sa.String(length=64), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("receipt_digest", sa.String(length=64), nullable=False),
        sa.Column("public_url", sa.String(length=2048), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.String(length=86), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "repository_id > 0",
            name=op.f("ck_federation_releases_repository_id_positive"),
        ),
        sa.CheckConstraint(
            "statement_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_releases_statement_digest_sha256"),
        ),
        sa.CheckConstraint(
            "manifest_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_releases_manifest_digest_sha256"),
        ),
        sa.CheckConstraint(
            "receipt_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_releases_receipt_digest_sha256"),
        ),
        sa.CheckConstraint(
            "signature ~ '^[A-Za-z0-9_-]{86}$'",
            name=op.f("ck_federation_releases_signature_base64url"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(statement_json) = 'object'",
            name=op.f("ck_federation_releases_statement_json_object"),
        ),
        sa.CheckConstraint(
            "receipt_published_at <= issued_at",
            name=op.f("ck_federation_releases_receipt_publication_before_issue"),
        ),
        sa.CheckConstraint(
            "verified_at + INTERVAL '5 minutes' >= issued_at",
            name=op.f("ck_federation_releases_verification_within_clock_skew"),
        ),
        sa.ForeignKeyConstraint(
            ["maintainer_id"],
            ["federation_maintainers.id"],
            name=op.f("fk_federation_releases_maintainer_id_federation_maintainers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_key_id"],
            ["federation_role_keys.id"],
            name=op.f("fk_federation_releases_role_key_id_federation_role_keys"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_event_id"],
            ["accepted_events.id"],
            name=op.f("fk_federation_releases_accepted_event_id_accepted_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["receipt_digest"],
            ["publication_receipts.receipt_digest"],
            name=op.f("fk_federation_releases_receipt_digest_publication_receipts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_releases")),
        sa.UniqueConstraint(
            "statement_digest",
            name="uq_federation_release_statement_digest",
        ),
        sa.UniqueConstraint(
            "repository_id",
            "pack_id",
            "release_version",
            name="uq_federation_release_scope_version",
        ),
        sa.UniqueConstraint(
            "repository_id",
            "pack_id",
            "publication_id",
            name="uq_federation_release_scope_publication",
        ),
    )
    op.create_index(
        "ix_federation_releases_scope_order",
        "federation_releases",
        [
            "repository_id",
            "pack_id",
            "receipt_published_at",
        ],
    )
    op.execute(
        """
        CREATE FUNCTION prohibit_federation_release_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'federation_release_is_append_only'
                USING ERRCODE = 'check_violation';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_append_only_federation_release "
        "BEFORE UPDATE OR DELETE ON federation_releases FOR EACH ROW "
        "EXECUTE FUNCTION prohibit_federation_release_mutation()"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM federation_releases) THEN
                RAISE EXCEPTION
                    'T34.5a refuses to discard immutable federation release rows';
            END IF;
        END $$;
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS guard_append_only_federation_release "
        "ON federation_releases"
    )
    op.execute("DROP FUNCTION IF EXISTS prohibit_federation_release_mutation()")
    op.drop_index("ix_federation_releases_scope_order", table_name="federation_releases")
    op.drop_table("federation_releases")
