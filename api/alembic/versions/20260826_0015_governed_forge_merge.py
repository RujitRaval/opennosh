"""add steward governance and immutable approval decisions

Revision ID: 20260826_0015
Revises: 20260825_0014
Create Date: 2026-08-26 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_0015"
down_revision: str | Sequence[str] | None = "20260825_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "governance_role_assignments",
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("granted_by_actor_id", sa.Uuid(), nullable=False),
        sa.Column("grant_reason", sa.String(length=1000), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column("revocation_reason", sa.String(length=1000), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('steward')", name=op.f("ck_governance_role_assignments_role_allowed")
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name=op.f("ck_governance_role_assignments_revocation_after_grant"),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_actor_id IS NULL AND "
            "revocation_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_actor_id IS NOT NULL AND "
            "revocation_reason IS NOT NULL)",
            name=op.f("ck_governance_role_assignments_revocation_audit_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_governance_role_assignments_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_role_assignments_granted_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_role_assignments_revoked_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_governance_role_assignments")),
        sa.UniqueConstraint("pack_id", "actor_id", "role", name="uq_governance_role_scope"),
    )
    op.create_index(
        "ix_governance_roles_actor_scope",
        "governance_role_assignments",
        ["actor_id", "pack_id", "role"],
    )

    op.create_table(
        "governance_recusals",
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("source_draft_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("recused_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_governance_recusals_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_id"],
            ["contribution_drafts.id"],
            name=op.f("fk_governance_recusals_source_draft_id_contribution_drafts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_governance_recusals")),
        sa.UniqueConstraint(
            "source_draft_id", "actor_id", name="uq_governance_recusal_draft_actor"
        ),
    )
    op.create_index(
        "ix_governance_recusals_actor_pack", "governance_recusals", ["actor_id", "pack_id"]
    )

    op.create_table(
        "governance_decisions",
        sa.Column("source_draft_id", sa.Uuid(), nullable=False),
        sa.Column("source_draft_version", sa.Integer(), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("record_id", sa.String(length=160), nullable=False),
        sa.Column("contributor_actor_id", sa.Uuid(), nullable=False),
        sa.Column("deciding_actor_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("approved_payload_digest", sa.String(length=64), nullable=False),
        sa.Column("approved_changes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expected_base_commit", sa.String(length=64), nullable=False),
        sa.Column("required_checks_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("forge_target", sa.String(length=512), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome = 'approved'", name=op.f("ck_governance_decisions_outcome_allowed")
        ),
        sa.CheckConstraint(
            "source_draft_version > 0", name=op.f("ck_governance_decisions_draft_version_positive")
        ),
        sa.CheckConstraint(
            "approved_payload_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_governance_decisions_approved_payload_digest_sha256"),
        ),
        sa.CheckConstraint(
            "expected_base_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
            name=op.f("ck_governance_decisions_expected_base_commit_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["contributor_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_decisions_contributor_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deciding_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_decisions_deciding_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_id"],
            ["contribution_drafts.id"],
            name=op.f("fk_governance_decisions_source_draft_id_contribution_drafts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_governance_decisions")),
        sa.UniqueConstraint(
            "source_draft_id", "source_draft_version", name="uq_governance_decision_draft_version"
        ),
    )
    op.create_index(
        "ix_governance_decisions_pack_decided", "governance_decisions", ["pack_id", "decided_at"]
    )

    op.create_table(
        "governance_publication_pauses",
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("paused_by_actor_id", sa.Uuid(), nullable=False),
        sa.Column("pause_reason", sa.String(length=1000), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resumed_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column("resume_reason", sa.String(length=1000), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "resumed_at IS NULL OR resumed_at >= paused_at",
            name=op.f("ck_governance_publication_pauses_resume_after_pause"),
        ),
        sa.CheckConstraint(
            "(resumed_at IS NULL AND resumed_by_actor_id IS NULL AND resume_reason IS NULL) OR "
            "(resumed_at IS NOT NULL AND resumed_by_actor_id IS NOT NULL AND "
            "resume_reason IS NOT NULL)",
            name=op.f("ck_governance_publication_pauses_resume_audit_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["paused_by_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_publication_pauses_paused_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resumed_by_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_publication_pauses_resumed_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_governance_publication_pauses")),
    )
    op.create_index(
        "ix_governance_publication_pauses_pack_time",
        "governance_publication_pauses",
        ["pack_id", "paused_at"],
    )
    op.create_index(
        "uq_governance_publication_pauses_active_pack",
        "governance_publication_pauses",
        ["pack_id"],
        unique=True,
        postgresql_where=sa.text("resumed_at IS NULL"),
    )

    op.create_table(
        "governance_publication_interventions",
        sa.Column("publication_intent_id", sa.Uuid(), nullable=False),
        sa.Column("source_draft_id", sa.Uuid(), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("intervened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('changes_requested', 'rejected')",
            name=op.f("ck_governance_publication_interventions_action_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_governance_publication_interventions_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_intent_id"],
            ["publication_intents.id"],
            name=op.f(
                "fk_governance_publication_interventions_publication_intent_id_publication_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_id"],
            ["contribution_drafts.id"],
            name=op.f(
                "fk_governance_publication_interventions_source_draft_id_contribution_drafts"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_governance_publication_interventions")
        ),
        sa.UniqueConstraint(
            "publication_intent_id",
            name="uq_governance_intervention_publication",
        ),
    )
    op.create_index(
        "ix_governance_interventions_pack_time",
        "governance_publication_interventions",
        ["pack_id", "intervened_at"],
    )
    op.create_table(
        "governance_merge_authorizations",
        sa.Column("publication_intent_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("head_commit", sa.String(length=64), nullable=False),
        sa.Column("approved_payload_digest", sa.String(length=64), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "head_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
            name=op.f("ck_governance_merge_authorizations_head_commit_hash"),
        ),
        sa.CheckConstraint(
            "approved_payload_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_governance_merge_authorizations_payload_digest_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["governance_decisions.id"],
            name=op.f(
                "fk_governance_merge_authorizations_decision_id_governance_decisions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_intent_id"],
            ["publication_intents.id"],
            name=op.f(
                "fk_governance_merge_authorizations_publication_intent_id_publication_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_governance_merge_authorizations")
        ),
        sa.UniqueConstraint(
            "publication_intent_id",
            name="uq_governance_merge_authorization_publication",
        ),
    )
    op.create_index(
        "ix_governance_merge_authorizations_pack_time",
        "governance_merge_authorizations",
        ["pack_id", "authorized_at"],
    )
    op.execute(
        """
        CREATE FUNCTION serialize_governance_pack_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended('opennosh.governance-pack:' || NEW.pack_id, 0)
            );
            RETURN NEW;
        END;
        $$
        """
    )
    for table in (
        "governance_role_assignments",
        "governance_recusals",
        "governance_publication_pauses",
    ):
        op.execute(
            f"CREATE TRIGGER serialize_{table}_pack_change "
            f"BEFORE INSERT OR UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION serialize_governance_pack_change()"
        )
    op.execute(
        """
        CREATE FUNCTION guard_governance_intervention()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended('opennosh.governance-pack:' || NEW.pack_id, 0)
            );
            IF EXISTS (
                SELECT 1
                FROM governance_merge_authorizations
                WHERE publication_intent_id = NEW.publication_intent_id
            ) THEN
                RAISE EXCEPTION 'merge_authorization_committed'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_governance_publication_intervention "
        "BEFORE INSERT OR UPDATE ON governance_publication_interventions "
        "FOR EACH ROW EXECUTE FUNCTION guard_governance_intervention()"
    )
    op.execute(
        """
        CREATE FUNCTION prohibit_governance_audit_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'governance_audit_rows_are_immutable'
                USING ERRCODE = 'check_violation';
        END;
        $$
        """
    )
    for table in (
        "governance_role_assignments",
        "governance_recusals",
        "governance_decisions",
        "governance_publication_pauses",
        "governance_publication_interventions",
        "governance_merge_authorizations",
    ):
        op.execute(
            f"CREATE TRIGGER prohibit_{table}_delete "
            f"BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prohibit_governance_audit_delete()"
        )
    op.execute(
        """
        CREATE FUNCTION prohibit_governance_audit_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'governance_audit_rows_are_append_only'
                USING ERRCODE = 'check_violation';
        END;
        $$
        """
    )
    for table in (
        "governance_recusals",
        "governance_decisions",
        "governance_publication_interventions",
        "governance_merge_authorizations",
    ):
        op.execute(
            f"CREATE TRIGGER prohibit_{table}_update "
            f"BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prohibit_governance_audit_update()"
        )
    op.execute(
        """
        CREATE FUNCTION guard_governance_role_revocation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.pack_id IS DISTINCT FROM OLD.pack_id
                OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
                OR NEW.role IS DISTINCT FROM OLD.role
                OR NEW.granted_by_actor_id IS DISTINCT FROM OLD.granted_by_actor_id
                OR NEW.grant_reason IS DISTINCT FROM OLD.grant_reason
                OR NEW.granted_at IS DISTINCT FROM OLD.granted_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR OLD.revoked_at IS NOT NULL
                OR NEW.revoked_at IS NULL
                OR NEW.revoked_by_actor_id IS NULL
                OR NEW.revocation_reason IS NULL
            THEN
                RAISE EXCEPTION 'governance_role_update_must_be_one_way_revocation'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_governance_role_assignment_update "
        "BEFORE UPDATE ON governance_role_assignments "
        "FOR EACH ROW EXECUTE FUNCTION guard_governance_role_revocation()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_governance_pause_resume()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.pack_id IS DISTINCT FROM OLD.pack_id
                OR NEW.paused_by_actor_id IS DISTINCT FROM OLD.paused_by_actor_id
                OR NEW.pause_reason IS DISTINCT FROM OLD.pause_reason
                OR NEW.paused_at IS DISTINCT FROM OLD.paused_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR OLD.resumed_at IS NOT NULL
                OR NEW.resumed_at IS NULL
                OR NEW.resumed_by_actor_id IS NULL
                OR NEW.resume_reason IS NULL
            THEN
                RAISE EXCEPTION 'governance_pause_update_must_be_one_way_resume'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_governance_publication_pause_update "
        "BEFORE UPDATE ON governance_publication_pauses "
        "FOR EACH ROW EXECUTE FUNCTION guard_governance_pause_resume()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER guard_governance_publication_pause_update "
        "ON governance_publication_pauses"
    )
    op.execute("DROP FUNCTION guard_governance_pause_resume()")
    op.execute(
        "DROP TRIGGER guard_governance_role_assignment_update "
        "ON governance_role_assignments"
    )
    op.execute("DROP FUNCTION guard_governance_role_revocation()")
    for table in (
        "governance_recusals",
        "governance_decisions",
        "governance_publication_interventions",
        "governance_merge_authorizations",
    ):
        op.execute(f"DROP TRIGGER prohibit_{table}_update ON {table}")
    op.execute("DROP FUNCTION prohibit_governance_audit_update()")
    for table in (
        "governance_role_assignments",
        "governance_recusals",
        "governance_decisions",
        "governance_publication_pauses",
        "governance_publication_interventions",
        "governance_merge_authorizations",
    ):
        op.execute(f"DROP TRIGGER prohibit_{table}_delete ON {table}")
    op.execute("DROP FUNCTION prohibit_governance_audit_delete()")
    op.execute(
        "DROP TRIGGER guard_governance_publication_intervention "
        "ON governance_publication_interventions"
    )
    op.execute("DROP FUNCTION guard_governance_intervention()")
    for table in (
        "governance_role_assignments",
        "governance_recusals",
        "governance_publication_pauses",
    ):
        op.execute(f"DROP TRIGGER serialize_{table}_pack_change ON {table}")
    op.execute("DROP FUNCTION serialize_governance_pack_change()")
    op.drop_index(
        "ix_governance_merge_authorizations_pack_time",
        table_name="governance_merge_authorizations",
    )
    op.drop_table("governance_merge_authorizations")
    op.drop_index(
        "ix_governance_interventions_pack_time",
        table_name="governance_publication_interventions",
    )
    op.drop_table("governance_publication_interventions")
    op.drop_index(
        "uq_governance_publication_pauses_active_pack", table_name="governance_publication_pauses"
    )
    op.drop_index(
        "ix_governance_publication_pauses_pack_time", table_name="governance_publication_pauses"
    )
    op.drop_table("governance_publication_pauses")
    op.drop_index("ix_governance_decisions_pack_decided", table_name="governance_decisions")
    op.drop_table("governance_decisions")
    op.drop_index("ix_governance_recusals_actor_pack", table_name="governance_recusals")
    op.drop_table("governance_recusals")
    op.drop_index("ix_governance_roles_actor_scope", table_name="governance_role_assignments")
    op.drop_table("governance_role_assignments")
