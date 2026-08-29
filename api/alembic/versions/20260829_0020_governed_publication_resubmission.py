"""add governed publication resubmission lineage

Revision ID: 20260829_0020
Revises: 20260828_0019
Create Date: 2026-08-29 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0020"
down_revision: str | Sequence[str] | None = "20260828_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_governance_decision_draft_version",
        "governance_decisions",
        type_="unique",
    )
    op.add_column(
        "governance_decisions",
        sa.Column("prior_decision_id", sa.Uuid(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_governance_decisions_prior_decision_not_self"),
        "governance_decisions",
        "prior_decision_id IS NULL OR prior_decision_id != id",
    )
    op.create_foreign_key(
        op.f("fk_governance_decisions_prior_decision_id_governance_decisions"),
        "governance_decisions",
        "governance_decisions",
        ["prior_decision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_governance_decision_successor",
        "governance_decisions",
        ["prior_decision_id"],
    )
    op.create_index(
        "uq_governance_decision_initial_draft_version",
        "governance_decisions",
        ["source_draft_id", "source_draft_version"],
        unique=True,
        postgresql_where=sa.text("prior_decision_id IS NULL"),
    )

    op.drop_constraint(
        "uq_publication_intents_source_draft_version",
        "publication_intents",
        type_="unique",
    )
    op.add_column(
        "publication_intents",
        sa.Column("prior_publication_intent_id", sa.Uuid(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_publication_intents_prior_publication_intent_not_self"),
        "publication_intents",
        "prior_publication_intent_id IS NULL OR prior_publication_intent_id != id",
    )
    op.create_foreign_key(
        op.f("fk_publication_intents_prior_publication_intent_id_publication_intents"),
        "publication_intents",
        "publication_intents",
        ["prior_publication_intent_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_publication_intent_successor",
        "publication_intents",
        ["prior_publication_intent_id"],
    )
    op.create_index(
        "uq_publication_intent_initial_draft_version",
        "publication_intents",
        ["source_draft_id", "source_draft_version"],
        unique=True,
        postgresql_where=sa.text("prior_publication_intent_id IS NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION guard_publication_intent_lineage_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.prior_publication_intent_id
                IS DISTINCT FROM OLD.prior_publication_intent_id
            THEN
                RAISE EXCEPTION 'publication_intent_lineage_is_immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_publication_intent_lineage_update "
        "BEFORE UPDATE ON publication_intents "
        "FOR EACH ROW EXECUTE FUNCTION guard_publication_intent_lineage_update()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_governance_resubmission_decision()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            prior_decision governance_decisions%ROWTYPE;
            prior_intent publication_intents%ROWTYPE;
            prior_intent_count bigint;
            current_draft contribution_drafts%ROWTYPE;
            current_evidence_id uuid;
        BEGIN
            IF NEW.prior_decision_id IS NULL THEN
                RETURN NEW;
            END IF;

            PERFORM pg_advisory_xact_lock(
                hashtextextended('opennosh.governance-pack:' || NEW.pack_id, 0)
            );
            SELECT * INTO prior_decision
            FROM governance_decisions
            WHERE id = NEW.prior_decision_id;
            IF NOT FOUND
                OR NEW.source_draft_id IS DISTINCT FROM prior_decision.source_draft_id
                OR NEW.source_draft_version IS DISTINCT FROM prior_decision.source_draft_version
                OR NEW.pack_id IS DISTINCT FROM prior_decision.pack_id
                OR NEW.record_id IS DISTINCT FROM prior_decision.record_id
                OR NEW.contributor_actor_id IS DISTINCT FROM prior_decision.contributor_actor_id
                OR NEW.outcome IS DISTINCT FROM prior_decision.outcome
                OR NEW.approved_payload_digest
                    IS DISTINCT FROM prior_decision.approved_payload_digest
                OR NEW.approved_changes_json IS DISTINCT FROM prior_decision.approved_changes_json
                OR NEW.required_checks_json IS DISTINCT FROM prior_decision.required_checks_json
                OR NEW.forge_target IS DISTINCT FROM prior_decision.forge_target
            THEN
                RAISE EXCEPTION 'governance_resubmission_binding_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;

            SELECT count(*) INTO prior_intent_count
            FROM publication_intents
            WHERE reviewed_decision_id = prior_decision.id;
            IF prior_intent_count != 1 THEN
                RAISE EXCEPTION 'governance_resubmission_predecessor_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;
            SELECT * INTO prior_intent
            FROM publication_intents
            WHERE reviewed_decision_id = prior_decision.id
            FOR UPDATE;
            IF prior_intent.state NOT IN (
                'blocked', 'failed', 'publish_blocked', 'quarantined'
            )
                OR EXISTS (
                    SELECT 1 FROM governance_publication_interventions
                    WHERE publication_intent_id = prior_intent.id
                )
                OR EXISTS (
                    SELECT 1 FROM governance_merge_authorizations
                    WHERE publication_intent_id = prior_intent.id
                )
            THEN
                RAISE EXCEPTION 'governance_resubmission_predecessor_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;

            SELECT evidence.id INTO current_evidence_id
            FROM evidence_manifests evidence
            WHERE evidence.source_draft_id = prior_decision.source_draft_id
              AND evidence.source_draft_version = prior_decision.source_draft_version
              AND evidence.public_state IS NOT NULL
              AND evidence.public_state != 'tombstoned'
              AND jsonb_array_length(
                    prior_intent.evidence_manifest_digests_json
                  ) = 1
              AND prior_intent.evidence_manifest_digests_json ? evidence.manifest_digest
              AND NOT EXISTS (
                    SELECT 1 FROM evidence_removal_tombstones tombstone
                    WHERE tombstone.evidence_id = evidence.id
              )
            FOR SHARE OF evidence;
            IF current_evidence_id IS NULL THEN
                RAISE EXCEPTION 'governance_resubmission_evidence_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;

            SELECT * INTO current_draft
            FROM contribution_drafts
            WHERE id = prior_decision.source_draft_id
            FOR UPDATE;
            IF NOT FOUND
                OR current_draft.review_state != 'publication_pending'
                OR current_draft.draft_version != prior_decision.source_draft_version
                OR current_draft.user_id = NEW.deciding_actor_id
                OR NOT EXISTS (
                    SELECT 1 FROM governance_role_assignments role
                    WHERE role.pack_id = NEW.pack_id
                      AND role.actor_id = NEW.deciding_actor_id
                      AND role.role = 'steward'
                      AND role.granted_at <= NEW.decided_at
                      AND (role.revoked_at IS NULL OR role.revoked_at > NEW.decided_at)
                )
                OR EXISTS (
                    SELECT 1 FROM governance_recusals recusal
                    WHERE recusal.source_draft_id = NEW.source_draft_id
                      AND recusal.actor_id = NEW.deciding_actor_id
                      AND recusal.recused_at <= NEW.decided_at
                )
                OR EXISTS (
                    SELECT 1 FROM governance_publication_pauses pause
                    WHERE pause.pack_id = NEW.pack_id
                      AND pause.paused_at <= NEW.decided_at
                      AND (pause.resumed_at IS NULL OR pause.resumed_at > NEW.decided_at)
                )
            THEN
                RAISE EXCEPTION 'governance_resubmission_authority_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_governance_resubmission_decision "
        "BEFORE INSERT ON governance_decisions "
        "FOR EACH ROW EXECUTE FUNCTION guard_governance_resubmission_decision()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_publication_intent_successor_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            prior_intent publication_intents%ROWTYPE;
            successor_decision governance_decisions%ROWTYPE;
            current_evidence_id uuid;
        BEGIN
            IF NEW.prior_publication_intent_id IS NULL THEN
                RETURN NEW;
            END IF;

            PERFORM pg_advisory_xact_lock(
                hashtextextended('opennosh.governance-pack:' || NEW.pack_id, 0)
            );
            SELECT * INTO prior_intent
            FROM publication_intents
            WHERE id = NEW.prior_publication_intent_id
            FOR UPDATE;
            SELECT * INTO successor_decision
            FROM governance_decisions
            WHERE id = NEW.reviewed_decision_id;

            IF prior_intent.id IS NULL
                OR successor_decision.id IS NULL
                OR successor_decision.prior_decision_id
                    IS DISTINCT FROM prior_intent.reviewed_decision_id
                OR prior_intent.state NOT IN (
                    'blocked', 'failed', 'publish_blocked', 'quarantined'
                )
                OR NEW.source_draft_id IS DISTINCT FROM prior_intent.source_draft_id
                OR NEW.source_draft_version IS DISTINCT FROM prior_intent.source_draft_version
                OR NEW.pack_id IS DISTINCT FROM prior_intent.pack_id
                OR NEW.record_id IS DISTINCT FROM prior_intent.record_id
                OR NEW.approved_payload_digest IS DISTINCT FROM prior_intent.approved_payload_digest
                OR NEW.required_checks_json IS DISTINCT FROM prior_intent.required_checks_json
                OR NEW.forge_target IS DISTINCT FROM prior_intent.forge_target
                OR NEW.event_type IS DISTINCT FROM prior_intent.event_type
                OR NEW.prior_receipt_digest IS DISTINCT FROM prior_intent.prior_receipt_digest
                OR NEW.evidence_manifest_digests_json
                    IS DISTINCT FROM prior_intent.evidence_manifest_digests_json
                OR NEW.evidence_acknowledgements_json
                    IS DISTINCT FROM prior_intent.evidence_acknowledgements_json
                OR NEW.source_draft_id IS DISTINCT FROM successor_decision.source_draft_id
                OR NEW.source_draft_version IS DISTINCT FROM successor_decision.source_draft_version
                OR NEW.pack_id IS DISTINCT FROM successor_decision.pack_id
                OR NEW.record_id IS DISTINCT FROM successor_decision.record_id
                OR NEW.approving_actor_id IS DISTINCT FROM successor_decision.deciding_actor_id
                OR NEW.approved_payload_digest
                    IS DISTINCT FROM successor_decision.approved_payload_digest
                OR NEW.expected_base_commit
                    IS DISTINCT FROM successor_decision.expected_base_commit
                OR NEW.required_checks_json IS DISTINCT FROM successor_decision.required_checks_json
                OR NEW.forge_target IS DISTINCT FROM successor_decision.forge_target
                OR NEW.state != 'pending'
                OR NEW.workflow_revision != 0
                OR NEW.attempt_count != 0
                OR EXISTS (
                    SELECT 1 FROM governance_publication_interventions
                    WHERE publication_intent_id = prior_intent.id
                )
                OR EXISTS (
                    SELECT 1 FROM governance_merge_authorizations
                    WHERE publication_intent_id = prior_intent.id
                )
            THEN
                RAISE EXCEPTION 'publication_resubmission_binding_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;

            SELECT evidence.id INTO current_evidence_id
            FROM evidence_manifests evidence
            WHERE evidence.source_draft_id = NEW.source_draft_id
              AND evidence.source_draft_version = NEW.source_draft_version
              AND evidence.public_state IS NOT NULL
              AND evidence.public_state != 'tombstoned'
              AND jsonb_array_length(NEW.evidence_manifest_digests_json) = 1
              AND NEW.evidence_manifest_digests_json ? evidence.manifest_digest
              AND NOT EXISTS (
                    SELECT 1 FROM evidence_removal_tombstones tombstone
                    WHERE tombstone.evidence_id = evidence.id
              )
            FOR SHARE OF evidence;
            IF current_evidence_id IS NULL THEN
                RAISE EXCEPTION 'publication_resubmission_evidence_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_publication_intent_successor_insert "
        "BEFORE INSERT ON publication_intents "
        "FOR EACH ROW EXECUTE FUNCTION guard_publication_intent_successor_insert()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_merge_authorization_against_resubmission()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            canonical_pack_id text;
        BEGIN
            SELECT intent.pack_id INTO canonical_pack_id
            FROM publication_intents intent
            WHERE intent.id = NEW.publication_intent_id;
            IF canonical_pack_id IS NULL THEN
                RAISE EXCEPTION 'governance_merge_authorization_binding_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('opennosh.governance-pack:' || canonical_pack_id, 0)
            );
            IF NOT EXISTS (
                SELECT 1
                FROM publication_intents intent
                JOIN governance_decisions decision
                  ON decision.id = intent.reviewed_decision_id
                WHERE intent.id = NEW.publication_intent_id
                  AND intent.reviewed_decision_id = NEW.decision_id
                  AND intent.pack_id = canonical_pack_id
                  AND decision.pack_id = canonical_pack_id
                  AND NEW.pack_id = canonical_pack_id
                  AND intent.approved_payload_digest = NEW.approved_payload_digest
                  AND decision.approved_payload_digest = NEW.approved_payload_digest
            ) THEN
                RAISE EXCEPTION 'governance_merge_authorization_binding_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF EXISTS (
                SELECT 1 FROM publication_intents successor
                WHERE successor.prior_publication_intent_id = NEW.publication_intent_id
            ) THEN
                RAISE EXCEPTION 'publication_already_resubmitted'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_merge_authorization_against_resubmission "
        "BEFORE INSERT ON governance_merge_authorizations "
        "FOR EACH ROW EXECUTE FUNCTION guard_merge_authorization_against_resubmission()"
    )
    op.execute(
        """
        CREATE FUNCTION prohibit_publication_intent_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'publication_intent_history_is_immutable'
                USING ERRCODE = 'check_violation';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER prohibit_publication_intents_delete "
        "BEFORE DELETE ON publication_intents "
        "FOR EACH ROW EXECUTE FUNCTION prohibit_publication_intent_delete()"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM publication_intents
                WHERE prior_publication_intent_id IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM governance_decisions
                WHERE prior_decision_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'cannot remove governed publication resubmission history';
            END IF;
        END $$;
        """
    )
    op.execute(
        "DROP TRIGGER guard_merge_authorization_against_resubmission "
        "ON governance_merge_authorizations"
    )
    op.execute("DROP FUNCTION guard_merge_authorization_against_resubmission()")
    op.execute("DROP TRIGGER prohibit_publication_intents_delete ON publication_intents")
    op.execute("DROP FUNCTION prohibit_publication_intent_delete()")
    op.execute("DROP TRIGGER guard_publication_intent_successor_insert ON publication_intents")
    op.execute("DROP FUNCTION guard_publication_intent_successor_insert()")
    op.execute("DROP TRIGGER guard_governance_resubmission_decision ON governance_decisions")
    op.execute("DROP FUNCTION guard_governance_resubmission_decision()")
    op.execute("DROP TRIGGER guard_publication_intent_lineage_update ON publication_intents")
    op.execute("DROP FUNCTION guard_publication_intent_lineage_update()")
    op.drop_index(
        "uq_publication_intent_initial_draft_version",
        table_name="publication_intents",
    )
    op.drop_constraint(
        "uq_publication_intent_successor",
        "publication_intents",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_publication_intents_prior_publication_intent_id_publication_intents"),
        "publication_intents",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_publication_intents_prior_publication_intent_not_self"),
        "publication_intents",
        type_="check",
    )
    op.drop_column("publication_intents", "prior_publication_intent_id")
    op.create_unique_constraint(
        "uq_publication_intents_source_draft_version",
        "publication_intents",
        ["source_draft_id", "source_draft_version"],
    )

    op.drop_index(
        "uq_governance_decision_initial_draft_version",
        table_name="governance_decisions",
    )
    op.drop_constraint(
        "uq_governance_decision_successor",
        "governance_decisions",
        type_="unique",
    )
    op.drop_constraint(
        op.f("fk_governance_decisions_prior_decision_id_governance_decisions"),
        "governance_decisions",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_governance_decisions_prior_decision_not_self"),
        "governance_decisions",
        type_="check",
    )
    op.drop_column("governance_decisions", "prior_decision_id")
    op.create_unique_constraint(
        "uq_governance_decision_draft_version",
        "governance_decisions",
        ["source_draft_id", "source_draft_version"],
    )
