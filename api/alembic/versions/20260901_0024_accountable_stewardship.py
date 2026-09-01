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
        sa.Column("decision_id", sa.Uuid(), nullable=True),
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


def downgrade() -> None:
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
