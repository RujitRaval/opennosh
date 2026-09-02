"""add verified federation artifacts and atomic projection facts

Revision ID: 20260902_0027
Revises: 20260902_0026
Create Date: 2026-09-02 12:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260902_0027"
down_revision: str | Sequence[str] | None = "20260902_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _identity_columns() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def upgrade() -> None:
    op.create_table(
        "federation_verified_releases",
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("pack_version", sa.String(length=64), nullable=False),
        sa.Column("pack_license", sa.String(length=32), nullable=False),
        sa.Column("manifest_key_id", sa.String(length=64), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("artifact_object_key", sa.String(length=1024), nullable=False),
        sa.Column("artifact_digest", sa.String(length=64), nullable=False),
        sa.Column("artifact_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("records_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("record_set_digest", sa.String(length=64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "pack_license = 'CC0-1.0'",
            name=op.f("ck_federation_verified_releases_pack_license_allowed"),
        ),
        sa.CheckConstraint(
            "manifest_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_verified_releases_manifest_digest_sha256"),
        ),
        sa.CheckConstraint(
            "artifact_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_verified_releases_artifact_digest_sha256"),
        ),
        sa.CheckConstraint(
            "record_set_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_verified_releases_record_set_digest_sha256"),
        ),
        sa.CheckConstraint(
            "artifact_size_bytes > 0",
            name=op.f("ck_federation_verified_releases_artifact_size_positive"),
        ),
        sa.CheckConstraint(
            "record_count > 0",
            name=op.f("ck_federation_verified_releases_record_count_positive"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(records_json) = 'array'",
            name=op.f("ck_federation_verified_releases_records_json_array"),
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["federation_releases.id"],
            name=op.f("fk_federation_verified_releases_release_id_federation_releases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_verified_releases")),
        sa.UniqueConstraint(
            "release_id",
            name="uq_federation_verified_release_release",
        ),
    )
    op.create_index(
        "ix_federation_verified_releases_verified",
        "federation_verified_releases",
        ["verified_at"],
    )

    op.create_table(
        "federation_release_status_events",
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason_digest", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "state IN ('verified','quarantined')",
            name=op.f("ck_federation_release_status_events_state_allowed"),
        ),
        sa.CheckConstraint(
            "reason_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_release_status_events_reason_digest_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["federation_releases.id"],
            name=op.f("fk_federation_release_status_events_release_id_federation_releases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_federation_release_status_events_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_release_status_events")),
    )
    op.create_index(
        "ix_federation_release_status_latest",
        "federation_release_status_events",
        ["release_id", "occurred_at"],
    )

    op.create_table(
        "federation_projection_checkpoints",
        sa.Column("release_set_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("release_set_digest", sa.String(length=64), nullable=False),
        sa.Column("release_count", sa.Integer(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "release_set_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_projection_checkpoints_release_set_sha256"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(release_set_json) = 'array'",
            name=op.f("ck_federation_projection_checkpoints_release_set_json_array"),
        ),
        sa.CheckConstraint(
            "release_count > 0",
            name=op.f("ck_federation_projection_checkpoints_release_count_positive"),
        ),
        sa.CheckConstraint(
            "record_count > 0",
            name=op.f("ck_federation_projection_checkpoints_record_count_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_projection_checkpoints")),
        sa.UniqueConstraint(
            "release_set_digest",
            name="uq_federation_projection_release_set",
        ),
    )
    op.create_index(
        "ix_federation_projection_checkpoints_built",
        "federation_projection_checkpoints",
        ["built_at"],
    )

    op.create_table(
        "federation_projection_releases",
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("verified_release_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "ordinal >= 0",
            name=op.f("ck_federation_projection_releases_ordinal_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["federation_projection_checkpoints.id"],
            name=op.f("fk_federation_projection_releases_checkpoint_id_federation_projection_checkpoints"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_release_id"],
            ["federation_verified_releases.id"],
            name=op.f("fk_federation_projection_releases_verified_release_id_federation_verified_releases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_projection_releases")),
        sa.UniqueConstraint(
            "checkpoint_id",
            "verified_release_id",
            name="uq_federation_projection_release_membership",
        ),
        sa.UniqueConstraint(
            "checkpoint_id",
            "ordinal",
            name="uq_federation_projection_release_ordinal",
        ),
    )

    op.create_table(
        "federation_projection_foods",
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("verified_release_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.String(length=120), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("pack_version", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("name_local", sa.String(length=160), nullable=True),
        sa.Column("locale", sa.String(length=35), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("provenance", sa.String(length=64), nullable=False),
        sa.Column("source_uri", sa.String(length=2048), nullable=True),
        sa.Column("source_license", sa.String(length=255), nullable=False),
        sa.Column("source_note", sa.String(length=1000), nullable=True),
        sa.Column("nutrients_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("portions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pack_license", sa.String(length=32), nullable=False),
        sa.Column("contributed_by", sa.String(length=100), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "pack_license = 'CC0-1.0'",
            name=op.f("ck_federation_projection_foods_pack_license_allowed"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(nutrients_json) = 'object'",
            name=op.f("ck_federation_projection_foods_nutrients_json_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(portions_json) = 'array'",
            name=op.f("ck_federation_projection_foods_portions_json_array"),
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["federation_projection_checkpoints.id"],
            name=op.f("fk_federation_projection_foods_checkpoint_id_federation_projection_checkpoints"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_release_id"],
            ["federation_verified_releases.id"],
            name=op.f("fk_federation_projection_foods_verified_release_id_federation_verified_releases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_projection_foods")),
        sa.UniqueConstraint(
            "checkpoint_id",
            "verified_release_id",
            "source_record_id",
            name="uq_federation_projection_food_source",
        ),
    )
    op.create_index(
        "ix_federation_projection_foods_checkpoint_pack",
        "federation_projection_foods",
        ["checkpoint_id", "pack_id"],
    )
    op.create_index(
        "ix_federation_projection_foods_checkpoint_name",
        "federation_projection_foods",
        ["checkpoint_id", "name"],
    )

    op.create_table(
        "federation_projection_activations",
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason_digest", sa.String(length=64), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "reason_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_projection_activations_reason_digest_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["federation_projection_checkpoints.id"],
            name=op.f("fk_federation_projection_activations_checkpoint_id_federation_projection_checkpoints"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_federation_projection_activations_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_projection_activations")),
    )
    op.create_index(
        "ix_federation_projection_activations_latest",
        "federation_projection_activations",
        ["activated_at"],
    )

    op.execute(
        """
        CREATE FUNCTION prohibit_federation_projection_fact_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'federation_projection_fact_is_append_only'
                USING ERRCODE = 'check_violation';
        END;
        $$
        """
    )
    for table in (
        "federation_verified_releases",
        "federation_release_status_events",
        "federation_projection_checkpoints",
        "federation_projection_releases",
        "federation_projection_foods",
        "federation_projection_activations",
    ):
        op.execute(
            f"CREATE TRIGGER guard_append_only_{table} "
            f"BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION prohibit_federation_projection_fact_mutation()"
        )


def downgrade() -> None:
    tables = (
        "federation_projection_activations",
        "federation_projection_foods",
        "federation_projection_releases",
        "federation_projection_checkpoints",
        "federation_release_status_events",
        "federation_verified_releases",
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM federation_verified_releases)
               OR EXISTS (SELECT 1 FROM federation_release_status_events)
               OR EXISTS (SELECT 1 FROM federation_projection_checkpoints)
               OR EXISTS (SELECT 1 FROM federation_projection_releases)
               OR EXISTS (SELECT 1 FROM federation_projection_foods)
               OR EXISTS (SELECT 1 FROM federation_projection_activations) THEN
                RAISE EXCEPTION
                    'T34.5c refuses to discard verified federation or projection facts';
            END IF;
        END $$;
        """
    )
    for table in tables:
        op.execute(f"DROP TRIGGER IF EXISTS guard_append_only_{table} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prohibit_federation_projection_fact_mutation()")
    op.drop_index(
        "ix_federation_projection_activations_latest",
        table_name="federation_projection_activations",
    )
    op.drop_table("federation_projection_activations")
    op.drop_index(
        "ix_federation_projection_foods_checkpoint_name",
        table_name="federation_projection_foods",
    )
    op.drop_index(
        "ix_federation_projection_foods_checkpoint_pack",
        table_name="federation_projection_foods",
    )
    op.drop_table("federation_projection_foods")
    op.drop_table("federation_projection_releases")
    op.drop_index(
        "ix_federation_projection_checkpoints_built",
        table_name="federation_projection_checkpoints",
    )
    op.drop_table("federation_projection_checkpoints")
    op.drop_index(
        "ix_federation_release_status_latest",
        table_name="federation_release_status_events",
    )
    op.drop_table("federation_release_status_events")
    op.drop_index(
        "ix_federation_verified_releases_verified",
        table_name="federation_verified_releases",
    )
    op.drop_table("federation_verified_releases")
