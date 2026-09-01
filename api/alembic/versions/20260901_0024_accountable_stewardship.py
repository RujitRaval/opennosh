"""add disabled accountable stewardship records

Revision ID: 20260901_0024
Revises: 20260901_0023
Create Date: 2026-09-01 20:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260901_0024"
down_revision: str | Sequence[str] | None = "20260901_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id_and_created_columns() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def _replace_decision_lineage_guard(*, accountable_reviews: bool) -> None:
    review_successor_branch = (
        """
            IF prior_decision.outcome != 'approved' THEN
                SELECT * INTO current_draft
                FROM contribution_drafts
                WHERE id = prior_decision.source_draft_id
                FOR UPDATE;
                IF NOT FOUND
                    OR EXISTS (
                    SELECT 1 FROM publication_intents
                    WHERE reviewed_decision_id = prior_decision.id
                )
                    OR current_draft.review_state != 'in_review'
                    OR current_draft.draft_version != prior_decision.source_draft_version
                    OR current_draft.user_id = NEW.deciding_actor_id
                    OR NOT EXISTS (
                        SELECT 1 FROM governance_review_cases review_case
                        WHERE review_case.source_draft_id = NEW.source_draft_id
                          AND review_case.source_draft_version = NEW.source_draft_version
                          AND review_case.pack_id = NEW.pack_id
                          AND review_case.state = 'in_review'
                          AND review_case.assigned_steward_actor_id = NEW.deciding_actor_id
                    )
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
                THEN
                    RAISE EXCEPTION 'governance_review_successor_authority_invalid'
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
        """
        if accountable_reviews
        else ""
    )
    _apply_decision_lineage_guard(review_successor_branch)


def _create_accountable_review_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION guard_governance_review_case_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.source_draft_id IS DISTINCT FROM OLD.source_draft_id
                OR NEW.source_draft_version IS DISTINCT FROM OLD.source_draft_version
                OR NEW.pack_id IS DISTINCT FROM OLD.pack_id
                OR NEW.contributor_actor_id IS DISTINCT FROM OLD.contributor_actor_id
                OR NEW.submitted_fields_json IS DISTINCT FROM OLD.submitted_fields_json
                OR NEW.opened_at IS DISTINCT FROM OLD.opened_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION 'governance_review_case_binding_is_immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.revision != OLD.revision + 1 THEN
                RAISE EXCEPTION 'governance_review_case_revision_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.state IS DISTINCT FROM OLD.state
                AND NOT (
                    (OLD.state = 'pending' AND NEW.state IN ('in_review','closed'))
                    OR (OLD.state = 'in_review' AND NEW.state IN (
                        'pending','changes_requested','approved','rejected','closed'
                    ))
                    OR (OLD.state = 'changes_requested' AND NEW.state IN (
                        'disputed','reopened','closed'
                    ))
                    OR (OLD.state IN ('approved','rejected') AND NEW.state IN (
                        'disputed','closed'
                    ))
                    OR (OLD.state = 'disputed' AND NEW.state IN ('appealed','reopened'))
                    OR (OLD.state = 'appealed' AND NEW.state = 'reopened')
                    OR (OLD.state = 'reopened' AND NEW.state IN (
                        'in_review','approved','rejected','appealed','closed'
                    ))
                    OR (OLD.state = 'closed' AND NEW.state = 'reopened')
                )
            THEN
                RAISE EXCEPTION 'governance_review_case_transition_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_governance_review_case_update "
        "BEFORE UPDATE ON governance_review_cases "
        "FOR EACH ROW EXECUTE FUNCTION guard_governance_review_case_update()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_governance_dispute_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.review_case_id IS DISTINCT FROM OLD.review_case_id
                OR NEW.decision_id IS DISTINCT FROM OLD.decision_id
                OR NEW.pack_id IS DISTINCT FROM OLD.pack_id
                OR NEW.opened_by_actor_id IS DISTINCT FROM OLD.opened_by_actor_id
                OR NEW.category IS DISTINCT FROM OLD.category
                OR NEW.public_reason IS DISTINCT FROM OLD.public_reason
                OR NEW.requested_remedy IS DISTINCT FROM OLD.requested_remedy
                OR NEW.opened_at IS DISTINCT FROM OLD.opened_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR OLD.state != 'open'
                OR NEW.state != 'resolved'
                OR NEW.revision != OLD.revision + 1
            THEN
                RAISE EXCEPTION 'governance_dispute_history_is_immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_governance_dispute_update "
        "BEFORE UPDATE ON governance_disputes "
        "FOR EACH ROW EXECUTE FUNCTION guard_governance_dispute_update()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_governance_appeal_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.dispute_id IS DISTINCT FROM OLD.dispute_id
                OR NEW.review_case_id IS DISTINCT FROM OLD.review_case_id
                OR NEW.opened_by_actor_id IS DISTINCT FROM OLD.opened_by_actor_id
                OR NEW.original_deciding_actor_id IS DISTINCT FROM OLD.original_deciding_actor_id
                OR NEW.public_reason IS DISTINCT FROM OLD.public_reason
                OR NEW.requested_remedy IS DISTINCT FROM OLD.requested_remedy
                OR NEW.opened_at IS DISTINCT FROM OLD.opened_at
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR NEW.revision != OLD.revision + 1
                OR NOT (
                    (OLD.state IN ('open','reopened') AND NEW.state = 'resolved')
                    OR (OLD.state = 'resolved' AND NEW.state = 'reopened')
                )
            THEN
                RAISE EXCEPTION 'governance_appeal_history_is_immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_governance_appeal_update "
        "BEFORE UPDATE ON governance_appeals "
        "FOR EACH ROW EXECUTE FUNCTION guard_governance_appeal_update()"
    )
    op.execute(
        """
        CREATE FUNCTION prohibit_governance_review_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'governance_review_history_is_immutable'
                USING ERRCODE = 'check_violation';
        END;
        $$
        """
    )
    for table in (
        "governance_review_events",
        "governance_review_private_notes",
    ):
        op.execute(
            f"CREATE TRIGGER prohibit_{table}_update BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prohibit_governance_review_history_mutation()"
        )
    for table in (
        "governance_review_cases",
        "governance_review_events",
        "governance_review_private_notes",
        "governance_disputes",
        "governance_appeals",
    ):
        op.execute(
            f"CREATE TRIGGER prohibit_{table}_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prohibit_governance_review_history_mutation()"
        )


def _apply_decision_lineage_guard(review_successor_branch: str) -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION guard_governance_resubmission_decision()
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
                OR NEW.contributor_actor_id IS DISTINCT FROM prior_decision.contributor_actor_id
            THEN
                RAISE EXCEPTION 'governance_resubmission_binding_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;

            {review_successor_branch}

            IF NEW.record_id IS DISTINCT FROM prior_decision.record_id
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


def _drop_accountable_review_guards() -> None:
    for table in (
        "governance_review_cases",
        "governance_review_events",
        "governance_review_private_notes",
        "governance_disputes",
        "governance_appeals",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS prohibit_{table}_delete ON {table}")
    for table in (
        "governance_review_events",
        "governance_review_private_notes",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS prohibit_{table}_update ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prohibit_governance_review_history_mutation()")
    op.execute("DROP TRIGGER IF EXISTS guard_governance_appeal_update ON governance_appeals")
    op.execute("DROP FUNCTION IF EXISTS guard_governance_appeal_update()")
    op.execute("DROP TRIGGER IF EXISTS guard_governance_dispute_update ON governance_disputes")
    op.execute("DROP FUNCTION IF EXISTS guard_governance_dispute_update()")
    op.execute(
        "DROP TRIGGER IF EXISTS guard_governance_review_case_update ON governance_review_cases"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_governance_review_case_update()")


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_contribution_drafts_review_state_allowed"),
        "contribution_drafts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_contribution_drafts_review_state_allowed"),
        "contribution_drafts",
        "review_state IN ('draft','in_review','changes_requested','rejected',"
        "'approved','publication_pending','published')",
    )
    _extend_decision_outcomes()
    op.create_table(
        "governance_review_cases",
        sa.Column("source_draft_id", sa.Uuid(), nullable=False),
        sa.Column("source_draft_version", sa.Integer(), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("contributor_actor_id", sa.Uuid(), nullable=False),
        sa.Column(
            "submitted_fields_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("assigned_steward_actor_id", sa.Uuid(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pause_reason", sa.String(length=1000), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_id_and_created_columns(),
        sa.CheckConstraint(
            "source_draft_version > 0",
            name=op.f("ck_governance_review_cases_draft_version_positive"),
        ),
        sa.CheckConstraint(
            "revision > 0", name=op.f("ck_governance_review_cases_revision_positive")
        ),
        sa.CheckConstraint(
            "state IN ('pending','in_review','changes_requested','approved','rejected',"
            "'disputed','appealed','reopened','closed')",
            name=op.f("ck_governance_review_cases_state_allowed"),
        ),
        sa.CheckConstraint(
            "acknowledged_at IS NULL OR assigned_steward_actor_id IS NOT NULL",
            name=op.f("ck_governance_review_cases_acknowledgement_requires_assignment"),
        ),
        sa.CheckConstraint(
            "(pause_reason IS NULL AND next_review_at IS NULL) OR "
            "(pause_reason IS NOT NULL AND next_review_at IS NOT NULL)",
            name=op.f("ck_governance_review_cases_pause_shape_complete"),
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR state = 'closed'",
            name=op.f("ck_governance_review_cases_closed_time_matches_state"),
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_id"],
            ["contribution_drafts.id"],
            name=op.f("fk_governance_review_cases_source_draft_id_contribution_drafts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contributor_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_review_cases_contributor_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_steward_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_review_cases_assigned_steward_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_governance_review_cases")),
        sa.UniqueConstraint(
            "source_draft_id",
            "source_draft_version",
            name="uq_governance_review_case_draft_version",
        ),
    )
    op.create_index(
        "ix_governance_review_cases_queue",
        "governance_review_cases",
        ["state", "next_review_at", "opened_at"],
    )
    op.create_index(
        "ix_governance_review_cases_pack_opened",
        "governance_review_cases",
        ["pack_id", "opened_at"],
    )

    op.create_table(
        "governance_review_events",
        sa.Column("review_case_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("public_reason", sa.String(length=2000), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "details_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_id_and_created_columns(),
        sa.CheckConstraint(
            "sequence > 0", name=op.f("ck_governance_review_events_sequence_positive")
        ),
        sa.CheckConstraint(
            "idempotency_key_hash IS NULL OR idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_governance_review_events_idempotency_key_hash_valid"),
        ),
        sa.CheckConstraint(
            "request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_governance_review_events_request_hash_valid"),
        ),
        sa.CheckConstraint(
            "event_type IN ('opened','claimed','released','recused','paused','resumed',"
            "'changes_requested','contributor_responded','approved','rejected',"
            "'dispute_opened','dispute_resolved','appeal_opened','appeal_resolved',"
            "'reopened','closed')",
            name=op.f("ck_governance_review_events_event_type_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["review_case_id"],
            ["governance_review_cases.id"],
            name=op.f("fk_governance_review_events_review_case_id_governance_review_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_governance_review_events_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_governance_review_events")),
        sa.UniqueConstraint("review_case_id", "sequence", name="uq_governance_review_event_order"),
        sa.UniqueConstraint(
            "review_case_id",
            "idempotency_key_hash",
            name="uq_governance_review_event_idempotency",
        ),
    )
    op.create_index(
        "ix_governance_review_events_case_time",
        "governance_review_events",
        ["review_case_id", "occurred_at"],
    )

    op.create_table(
        "governance_review_private_notes",
        sa.Column("review_case_id", sa.Uuid(), nullable=False),
        sa.Column("author_actor_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.String(length=4000), nullable=False),
        sa.Column("noted_at", sa.DateTime(timezone=True), nullable=False),
        *_id_and_created_columns(),
        sa.ForeignKeyConstraint(
            ["review_case_id"],
            ["governance_review_cases.id"],
            name=op.f("fk_governance_review_private_notes_review_case_id_governance_review_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["author_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_review_private_notes_author_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_governance_review_private_notes")),
    )
    op.create_index(
        "ix_governance_review_private_notes_case_time",
        "governance_review_private_notes",
        ["review_case_id", "noted_at"],
    )

    op.create_table(
        "governance_disputes",
        sa.Column("review_case_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("opened_by_actor_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("public_reason", sa.String(length=2000), nullable=False),
        sa.Column("requested_remedy", sa.String(length=1000), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution", sa.String(length=2000), nullable=True),
        sa.Column("resolved_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_id_and_created_columns(),
        sa.CheckConstraint("revision > 0", name=op.f("ck_governance_disputes_revision_positive")),
        sa.CheckConstraint(
            "state IN ('open','resolved')",
            name=op.f("ck_governance_disputes_state_allowed"),
        ),
        sa.CheckConstraint(
            "category IN ('evidence','accuracy','rights','process','other')",
            name=op.f("ck_governance_disputes_category_allowed"),
        ),
        sa.CheckConstraint(
            "(state = 'open' AND resolution IS NULL AND resolved_by_actor_id IS NULL "
            "AND resolved_at IS NULL) OR "
            "(state = 'resolved' AND resolution IS NOT NULL "
            "AND resolved_by_actor_id IS NOT NULL AND resolved_at IS NOT NULL)",
            name=op.f("ck_governance_disputes_resolution_shape_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["review_case_id"],
            ["governance_review_cases.id"],
            name=op.f("fk_governance_disputes_review_case_id_governance_review_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["governance_decisions.id"],
            name=op.f("fk_governance_disputes_decision_id_governance_decisions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["opened_by_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_disputes_opened_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_disputes_resolved_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_governance_disputes")),
    )
    op.create_index(
        "uq_governance_disputes_active_case",
        "governance_disputes",
        ["review_case_id"],
        unique=True,
        postgresql_where=sa.text("state = 'open'"),
    )
    op.create_index(
        "ix_governance_disputes_pack_opened",
        "governance_disputes",
        ["pack_id", "opened_at"],
    )

    op.create_table(
        "governance_appeals",
        sa.Column("dispute_id", sa.Uuid(), nullable=False),
        sa.Column("review_case_id", sa.Uuid(), nullable=False),
        sa.Column("opened_by_actor_id", sa.Uuid(), nullable=False),
        sa.Column("original_deciding_actor_id", sa.Uuid(), nullable=False),
        sa.Column("public_reason", sa.String(length=2000), nullable=False),
        sa.Column("requested_remedy", sa.String(length=1000), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution", sa.String(length=2000), nullable=True),
        sa.Column("decided_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_id_and_created_columns(),
        sa.CheckConstraint("revision > 0", name=op.f("ck_governance_appeals_revision_positive")),
        sa.CheckConstraint(
            "state IN ('open','resolved','reopened')",
            name=op.f("ck_governance_appeals_state_allowed"),
        ),
        sa.CheckConstraint(
            "(state IN ('open','reopened') AND resolution IS NULL "
            "AND decided_by_actor_id IS NULL AND resolved_at IS NULL) OR "
            "(state = 'resolved' AND resolution IS NOT NULL "
            "AND decided_by_actor_id IS NOT NULL AND resolved_at IS NOT NULL)",
            name=op.f("ck_governance_appeals_resolution_shape_valid"),
        ),
        sa.CheckConstraint(
            "decided_by_actor_id IS NULL OR decided_by_actor_id != original_deciding_actor_id",
            name=op.f("ck_governance_appeals_independent_decider"),
        ),
        sa.ForeignKeyConstraint(
            ["dispute_id"],
            ["governance_disputes.id"],
            name=op.f("fk_governance_appeals_dispute_id_governance_disputes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_case_id"],
            ["governance_review_cases.id"],
            name=op.f("fk_governance_appeals_review_case_id_governance_review_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["opened_by_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_appeals_opened_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["original_deciding_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_appeals_original_deciding_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_actor_id"],
            ["users.id"],
            name=op.f("fk_governance_appeals_decided_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_governance_appeals")),
        sa.UniqueConstraint("dispute_id", name="uq_governance_appeal_dispute"),
    )
    op.create_index("ix_governance_appeals_opened", "governance_appeals", ["opened_at"])
    _create_accountable_review_guards()
    _replace_decision_lineage_guard(accountable_reviews=True)


def downgrade() -> None:
    _replace_decision_lineage_guard(accountable_reviews=False)
    _drop_accountable_review_guards()
    op.drop_index("ix_governance_appeals_opened", table_name="governance_appeals")
    op.drop_table("governance_appeals")
    op.drop_index("ix_governance_disputes_pack_opened", table_name="governance_disputes")
    op.drop_index("uq_governance_disputes_active_case", table_name="governance_disputes")
    op.drop_table("governance_disputes")
    op.drop_index(
        "ix_governance_review_private_notes_case_time",
        table_name="governance_review_private_notes",
    )
    op.drop_table("governance_review_private_notes")
    op.drop_index("ix_governance_review_events_case_time", table_name="governance_review_events")
    op.drop_table("governance_review_events")
    op.drop_index("ix_governance_review_cases_pack_opened", table_name="governance_review_cases")
    op.drop_index("ix_governance_review_cases_queue", table_name="governance_review_cases")
    op.drop_table("governance_review_cases")
    _restore_approval_only_decisions()
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM contribution_drafts WHERE review_state = 'rejected') THEN
            RAISE EXCEPTION
              'cannot downgrade accountable stewardship with rejected contributions';
          END IF;
        END $$
        """
    )
    op.drop_constraint(
        op.f("ck_contribution_drafts_review_state_allowed"),
        "contribution_drafts",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_contribution_drafts_review_state_allowed"),
        "contribution_drafts",
        "review_state IN ('draft','in_review','changes_requested','approved',"
        "'publication_pending','published')",
    )


def _extend_decision_outcomes() -> None:
    table = "governance_decisions"
    for name in (
        "outcome_allowed",
        "approved_payload_digest_sha256",
        "expected_base_commit_hash",
    ):
        op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    for column in (
        "approved_payload_digest",
        "approved_changes_json",
        "expected_base_commit",
        "required_checks_json",
        "forge_target",
    ):
        op.alter_column(table, column, existing_nullable=False, nullable=True)
    op.create_check_constraint(
        op.f(f"ck_{table}_outcome_allowed"),
        table,
        "outcome IN ('approved','changes_requested','rejected')",
    )
    op.create_check_constraint(
        op.f(f"ck_{table}_approved_payload_digest_sha256"),
        table,
        "approved_payload_digest IS NULL OR approved_payload_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f(f"ck_{table}_expected_base_commit_hash"),
        table,
        "expected_base_commit IS NULL OR expected_base_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
    )
    op.create_check_constraint(
        op.f(f"ck_{table}_outcome_shape_valid"),
        table,
        "(outcome = 'approved' AND approved_payload_digest IS NOT NULL "
        "AND approved_changes_json IS NOT NULL AND expected_base_commit IS NOT NULL "
        "AND required_checks_json IS NOT NULL AND forge_target IS NOT NULL) OR "
        "(outcome IN ('changes_requested','rejected') "
        "AND approved_payload_digest IS NULL AND approved_changes_json IS NULL "
        "AND expected_base_commit IS NULL AND required_checks_json IS NULL "
        "AND forge_target IS NULL)",
    )


def _restore_approval_only_decisions() -> None:
    table = "governance_decisions"
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM governance_decisions WHERE outcome != 'approved') THEN
            RAISE EXCEPTION
              'cannot downgrade accountable stewardship with non-approval decisions';
          END IF;
        END $$
        """
    )
    for name in (
        "outcome_shape_valid",
        "outcome_allowed",
        "approved_payload_digest_sha256",
        "expected_base_commit_hash",
    ):
        op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    for column in (
        "approved_payload_digest",
        "approved_changes_json",
        "expected_base_commit",
        "required_checks_json",
        "forge_target",
    ):
        op.alter_column(table, column, existing_nullable=True, nullable=False)
    op.create_check_constraint(op.f(f"ck_{table}_outcome_allowed"), table, "outcome = 'approved'")
    op.create_check_constraint(
        op.f(f"ck_{table}_approved_payload_digest_sha256"),
        table,
        "approved_payload_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f(f"ck_{table}_expected_base_commit_hash"),
        table,
        "expected_base_commit ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'",
    )
