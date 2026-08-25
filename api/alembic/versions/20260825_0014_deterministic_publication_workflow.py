"""add deterministic publication workflow state

Revision ID: 20260825_0014
Revises: 20260825_0013
Create Date: 2026-08-25 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_0014"
down_revision: str | Sequence[str] | None = "20260825_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INTENT_STATE_CONSTRAINT = "ck_publication_intents_state_allowed"
_OLD_INTENT_STATES = "state IN ('pending', 'running', 'retrying', 'blocked', 'failed', 'published')"
_NEW_INTENT_STATES = (
    "state IN ('pending', 'running', 'retrying', 'blocked', 'failed', 'published', "
    "'committed', 'signed', 'publish_blocked', 'publish_retrying', 'quarantined')"
)


def upgrade() -> None:
    op.add_column(
        "publication_intents",
        sa.Column("workflow_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_publication_intents_workflow_revision_non_negative"),
        "publication_intents",
        "workflow_revision >= 0",
    )
    op.drop_constraint(op.f(_INTENT_STATE_CONSTRAINT), "publication_intents", type_="check")
    op.create_check_constraint(
        op.f(_INTENT_STATE_CONSTRAINT),
        "publication_intents",
        _NEW_INTENT_STATES,
    )
    op.drop_index("ix_publication_intents_claim", table_name="publication_intents")
    op.create_index(
        "ix_publication_intents_claim",
        "publication_intents",
        ["state", "next_attempt_at", "id"],
        postgresql_where=sa.text("state IN ('pending', 'retrying', 'publish_retrying')"),
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM publication_steps) THEN
                RAISE EXCEPTION 'T10 refuses non-empty legacy publication_steps';
            END IF;
        END $$;
        """
    )

    op.add_column("publication_steps", sa.Column("ordinal", sa.Integer(), nullable=True))
    op.add_column(
        "publication_steps",
        sa.Column("destination", sa.String(length=512), nullable=True),
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY publication_intent_id
                       ORDER BY created_at, step_name, id
                   ) - 1 AS ordinal
            FROM publication_steps
        )
        UPDATE publication_steps AS step
        SET ordinal = ordered.ordinal,
            destination = 'legacy:' || step.step_name
        FROM ordered
        WHERE step.id = ordered.id
        """
    )
    op.alter_column("publication_steps", "ordinal", nullable=False)
    op.alter_column("publication_steps", "destination", nullable=False)
    op.create_check_constraint(
        op.f("ck_publication_steps_ordinal_non_negative"),
        "publication_steps",
        "ordinal >= 0",
    )
    op.drop_constraint(
        op.f("uq_publication_steps_intent_name_version"),
        "publication_steps",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_publication_steps_intent_name_destination_version"),
        "publication_steps",
        ["publication_intent_id", "step_name", "destination", "step_version"],
    )
    op.create_unique_constraint(
        op.f("uq_publication_steps_intent_ordinal"),
        "publication_steps",
        ["publication_intent_id", "ordinal"],
    )
    op.create_index(
        "uq_publication_steps_one_lease_per_intent",
        "publication_steps",
        ["publication_intent_id"],
        unique=True,
        postgresql_where=sa.text("state = 'leased'"),
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM accepted_events WHERE receipt_digest IS NULL) THEN
                RAISE EXCEPTION 'accepted events without signed receipt proof cannot migrate';
            END IF;
        END $$;
        """
    )
    op.alter_column("accepted_events", "receipt_digest", nullable=False)


def downgrade() -> None:
    op.alter_column("accepted_events", "receipt_digest", nullable=True)
    op.drop_index(
        "uq_publication_steps_one_lease_per_intent",
        table_name="publication_steps",
    )
    op.drop_constraint(
        op.f("uq_publication_steps_intent_ordinal"),
        "publication_steps",
        type_="unique",
    )
    op.drop_constraint(
        op.f("uq_publication_steps_intent_name_destination_version"),
        "publication_steps",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_publication_steps_intent_name_version"),
        "publication_steps",
        ["publication_intent_id", "step_name", "step_version"],
    )
    op.drop_constraint(
        op.f("ck_publication_steps_ordinal_non_negative"),
        "publication_steps",
        type_="check",
    )
    op.drop_column("publication_steps", "destination")
    op.drop_column("publication_steps", "ordinal")

    op.drop_index("ix_publication_intents_claim", table_name="publication_intents")
    op.create_index(
        "ix_publication_intents_claim",
        "publication_intents",
        ["state", "next_attempt_at", "id"],
        postgresql_where=sa.text("state IN ('pending', 'retrying')"),
    )

    op.execute(
        """
        UPDATE publication_intents
        SET state = CASE
            WHEN state IN ('committed', 'signed') THEN 'running'
            WHEN state IN ('publish_blocked', 'quarantined') THEN 'blocked'
            WHEN state = 'publish_retrying' THEN 'retrying'
            ELSE state
        END
        """
    )
    op.drop_constraint(op.f(_INTENT_STATE_CONSTRAINT), "publication_intents", type_="check")
    op.create_check_constraint(
        op.f(_INTENT_STATE_CONSTRAINT),
        "publication_intents",
        _OLD_INTENT_STATES,
    )
    op.drop_constraint(
        op.f("ck_publication_intents_workflow_revision_non_negative"),
        "publication_intents",
        type_="check",
    )
    op.drop_column("publication_intents", "workflow_revision")
