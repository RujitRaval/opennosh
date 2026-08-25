"""add namespaced jobs and publication ledger

Revision ID: 20260825_0013
Revises: 20260824_0012
Create Date: 2026-08-25 00:13:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260825_0013"
down_revision: str | Sequence[str] | None = "20260824_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QUEUE_TABLE = "opennosh_pgqueuer"
QUEUE_LOG_TABLE = "opennosh_pgqueuer_log"
QUEUE_STATISTICS_TABLE = "opennosh_pgqueuer_statistics"
QUEUE_SCHEDULES_TABLE = "opennosh_pgqueuer_schedules"
QUEUE_STATUS_TYPE = "opennosh_pgqueuer_status"
QUEUE_FUNCTION = "opennosh_fn_pgqueuer_changed"
QUEUE_TRIGGER = "opennosh_tg_pgqueuer_changed"


def _create_pgqueuer_schema() -> None:
    bind = op.get_bind()
    status = postgresql.ENUM(
        "queued",
        "picked",
        "successful",
        "exception",
        "canceled",
        "deleted",
        "failed",
        name=QUEUE_STATUS_TYPE,
        create_type=False,
    )
    status.create(bind, checkfirst=False)

    op.create_table(
        QUEUE_TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("queue_manager_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "heartbeat", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "execute_after",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("status", status, nullable=False),
        sa.Column("entrypoint", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("payload", sa.LargeBinary(), nullable=True),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{QUEUE_TABLE}"),
    )
    op.create_index(
        f"{QUEUE_TABLE}_priority_id_id1_idx",
        QUEUE_TABLE,
        [sa.text("priority ASC"), sa.text("id DESC")],
        postgresql_include=["id"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        f"{QUEUE_TABLE}_updated_id_id1_idx",
        QUEUE_TABLE,
        [sa.text("updated ASC"), sa.text("id DESC")],
        postgresql_include=["id"],
        postgresql_where=sa.text("status = 'picked'"),
    )
    op.create_index(
        f"{QUEUE_TABLE}_queue_manager_id_idx",
        QUEUE_TABLE,
        ["queue_manager_id"],
        postgresql_where=sa.text("queue_manager_id IS NOT NULL"),
    )
    op.create_index(
        f"{QUEUE_TABLE}_ep_prio_id_idx",
        QUEUE_TABLE,
        [sa.text("entrypoint"), sa.text("priority DESC"), sa.text("id ASC")],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        f"{QUEUE_TABLE}_ep_ea_idx",
        QUEUE_TABLE,
        ["entrypoint", "execute_after"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        f"{QUEUE_TABLE}_unique_dedupe_key",
        QUEUE_TABLE,
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'picked') AND dedupe_key IS NOT NULL"),
    )

    op.create_table(
        QUEUE_LOG_TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "created", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("entrypoint", sa.Text(), nullable=False),
        sa.Column("traceback", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("aggregated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{QUEUE_LOG_TABLE}"),
    )
    op.create_index(
        f"{QUEUE_LOG_TABLE}_not_aggregated",
        QUEUE_LOG_TABLE,
        [sa.text("(1)")],
        postgresql_where=sa.text("NOT aggregated"),
    )
    op.create_index(f"{QUEUE_LOG_TABLE}_created", QUEUE_LOG_TABLE, ["created"])
    op.create_index(f"{QUEUE_LOG_TABLE}_status", QUEUE_LOG_TABLE, ["status"])
    op.create_index(
        f"{QUEUE_LOG_TABLE}_job_id_status",
        QUEUE_LOG_TABLE,
        ["job_id", sa.text("created DESC")],
    )

    op.create_table(
        QUEUE_STATISTICS_TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "created",
            sa.DateTime(timezone=True),
            server_default=sa.text("date_trunc('second', now() AT TIME ZONE 'UTC')"),
            nullable=False,
        ),
        sa.Column("count", sa.BigInteger(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("entrypoint", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{QUEUE_STATISTICS_TABLE}"),
    )
    op.create_index(
        f"{QUEUE_STATISTICS_TABLE}_unique_count",
        QUEUE_STATISTICS_TABLE,
        [
            "priority",
            sa.text("date_trunc('second', created AT TIME ZONE 'UTC')"),
            "status",
            "entrypoint",
        ],
        unique=True,
    )

    op.create_table(
        QUEUE_SCHEDULES_TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("entrypoint", sa.Text(), nullable=False),
        sa.Column(
            "heartbeat", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "next_run", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_run", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", status, server_default="queued", nullable=True),
        sa.PrimaryKeyConstraint("id", name=f"pk_{QUEUE_SCHEDULES_TABLE}"),
        sa.UniqueConstraint(
            "expression", "entrypoint", name=f"uq_{QUEUE_SCHEDULES_TABLE}_expression_entrypoint"
        ),
    )

    op.execute(
        f"""
        CREATE FUNCTION {QUEUE_FUNCTION}() RETURNS TRIGGER AS $$
        BEGIN
            PERFORM pg_notify(
                'opennosh_ch_pgqueuer',
                json_build_object(
                    'channel', 'opennosh_ch_pgqueuer',
                    'operation', lower(TG_OP),
                    'sent_at', NOW(),
                    'table', TG_TABLE_NAME,
                    'type', 'table_changed_event'
                )::text
            );
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                RETURN NEW;
            ELSIF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {QUEUE_TRIGGER}
        AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON {QUEUE_TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION {QUEUE_FUNCTION}()
        """
    )


def _create_publication_ledger() -> None:
    op.create_table(
        "publication_intents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("source_draft_id", sa.Uuid(), nullable=False),
        sa.Column("source_draft_version", sa.Integer(), nullable=False),
        sa.Column("reviewed_decision_id", sa.Uuid(), nullable=False),
        sa.Column("approving_actor_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), server_default="1.0", nullable=False),
        sa.Column("workflow_version", sa.String(length=16), server_default="1.0", nullable=False),
        sa.Column("state", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("record_id", sa.String(length=160), nullable=False),
        sa.Column("approved_payload_digest", sa.String(length=64), nullable=False),
        sa.Column("expected_base_commit", sa.String(length=64), nullable=False),
        sa.Column(
            "required_checks_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("forge_target", sa.String(length=512), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_failure_code", sa.String(length=120), nullable=True),
        sa.Column(
            "last_failure_context_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "schema_version = '1.0'", name=op.f("ck_publication_intents_schema_version_supported")
        ),
        sa.CheckConstraint(
            "workflow_version = '1.0'",
            name=op.f("ck_publication_intents_workflow_version_supported"),
        ),
        sa.CheckConstraint(
            "source_draft_version > 0",
            name=op.f("ck_publication_intents_source_draft_version_positive"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'retrying', 'blocked', 'failed', 'published')",
            name=op.f("ck_publication_intents_state_allowed"),
        ),
        sa.CheckConstraint(
            "approved_payload_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_publication_intents_approved_payload_digest_sha256"),
        ),
        sa.CheckConstraint(
            "expected_base_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
            name=op.f("ck_publication_intents_expected_base_commit_hash"),
        ),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_publication_intents_idempotency_key_hash_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_id"],
            ["contribution_drafts.id"],
            name=op.f("fk_publication_intents_source_draft_id_contribution_drafts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publication_intents")),
        sa.UniqueConstraint(
            "idempotency_key_hash", name=op.f("uq_publication_intents_idempotency_key_hash")
        ),
        sa.UniqueConstraint(
            "source_draft_id",
            "source_draft_version",
            name="uq_publication_intents_source_draft_version",
        ),
    )
    op.create_index(
        "ix_publication_intents_claim",
        "publication_intents",
        ["state", "next_attempt_at", "id"],
        postgresql_where=sa.text("state IN ('pending', 'retrying')"),
    )

    op.create_table(
        "publication_steps",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("publication_intent_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_version", sa.String(length=16), server_default="1.0", nullable=False),
        sa.Column("step_name", sa.String(length=120), nullable=False),
        sa.Column("step_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("state", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("queue_job_id", sa.BigInteger(), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("input_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "observation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
        sa.Column(
            "failure_context_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "workflow_version = '1.0'", name=op.f("ck_publication_steps_workflow_version_supported")
        ),
        sa.CheckConstraint(
            "step_version > 0", name=op.f("ck_publication_steps_step_version_positive")
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_publication_steps_attempt_count_non_negative")
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'leased', 'retrying', 'blocked', 'failed', 'verified')",
            name=op.f("ck_publication_steps_state_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["publication_intent_id"],
            ["publication_intents.id"],
            name=op.f("fk_publication_steps_publication_intent_id_publication_intents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publication_steps")),
        sa.UniqueConstraint("lease_token", name=op.f("uq_publication_steps_lease_token")),
        sa.UniqueConstraint(
            "publication_intent_id",
            "step_name",
            "step_version",
            name="uq_publication_steps_intent_name_version",
        ),
    )
    op.create_index(
        "ix_publication_steps_claim",
        "publication_steps",
        ["state", "next_attempt_at", "lease_expires_at", "id"],
        postgresql_where=sa.text("state IN ('pending', 'retrying', 'leased')"),
    )

    op.create_table(
        "publication_durable_acknowledgements",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("publication_intent_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), server_default="1.0", nullable=False),
        sa.Column("acknowledgement_kind", sa.String(length=80), nullable=False),
        sa.Column("destination", sa.String(length=512), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("external_reference", sa.String(length=1024), nullable=True),
        sa.Column(
            "context_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "schema_version = '1.0'",
            name=op.f("ck_publication_durable_acknowledgements_schema_v1"),
        ),
        sa.CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_publication_durable_acknowledgements_content_digest_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["publication_intent_id"],
            ["publication_intents.id"],
            name=op.f(
                "fk_publication_durable_acknowledgements_publication_intent_id_publication_intents"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publication_durable_acknowledgements")),
        sa.UniqueConstraint(
            "publication_intent_id",
            "acknowledgement_kind",
            "destination",
            name="uq_publication_acknowledgements_intent_kind_destination",
        ),
    )

    op.create_table(
        "accepted_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("publication_intent_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), server_default="1.0", nullable=False),
        sa.Column("repository", sa.String(length=512), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("record_id", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("receipt_digest", sa.String(length=64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "schema_version = '1.0'", name=op.f("ck_accepted_events_schema_version_supported")
        ),
        sa.CheckConstraint(
            "commit_sha ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
            name=op.f("ck_accepted_events_commit_sha_hash"),
        ),
        sa.CheckConstraint(
            "receipt_digest IS NULL OR receipt_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_accepted_events_receipt_digest_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["publication_intent_id"],
            ["publication_intents.id"],
            name=op.f("fk_accepted_events_publication_intent_id_publication_intents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accepted_events")),
        sa.UniqueConstraint(
            "publication_intent_id", name=op.f("uq_accepted_events_publication_intent_id")
        ),
        sa.UniqueConstraint(
            "repository",
            "commit_sha",
            "pack_id",
            "record_id",
            name="uq_accepted_events_canonical_record",
        ),
    )
    op.create_index(
        "ix_accepted_events_published_type", "accepted_events", ["published_at", "event_type"]
    )


def upgrade() -> None:
    _create_pgqueuer_schema()
    _create_publication_ledger()


def downgrade() -> None:
    op.drop_index("ix_accepted_events_published_type", table_name="accepted_events")
    op.drop_table("accepted_events")
    op.drop_table("publication_durable_acknowledgements")
    op.drop_index("ix_publication_steps_claim", table_name="publication_steps")
    op.drop_table("publication_steps")
    op.drop_index("ix_publication_intents_claim", table_name="publication_intents")
    op.drop_table("publication_intents")

    op.execute(f"DROP TRIGGER IF EXISTS {QUEUE_TRIGGER} ON {QUEUE_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {QUEUE_FUNCTION}()")
    op.drop_table(QUEUE_SCHEDULES_TABLE)
    op.drop_table(QUEUE_STATISTICS_TABLE)
    op.drop_table(QUEUE_LOG_TABLE)
    op.drop_table(QUEUE_TABLE)
    postgresql.ENUM(name=QUEUE_STATUS_TYPE).drop(op.get_bind(), checkfirst=False)
