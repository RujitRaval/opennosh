"""record explicitly authorized owner mission approvals

Revision ID: 20260912_0042
Revises: 20260910_0041
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260912_0042"
down_revision: str | Sequence[str] | None = "20260910_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE mission_lifecycle_events
    ADD COLUMN approval_mode varchar(16),
    ADD COLUMN owner_authorization_id uuid
        REFERENCES governance_owner_authorizations(id) ON DELETE RESTRICT,
    ADD CONSTRAINT ck_mission_lifecycle_events_approval_mode_shape CHECK (
        (action = 'approve' AND approval_mode IN ('independent','owner'))
        OR (action != 'approve' AND approval_mode IS NULL)
    ) NOT VALID,
    ADD CONSTRAINT ck_mission_lifecycle_events_owner_authorization_shape CHECK (
        (approval_mode = 'owner' AND owner_authorization_id IS NOT NULL)
        OR (approval_mode IS DISTINCT FROM 'owner' AND owner_authorization_id IS NULL)
    ) NOT VALID
    """)
    op.execute("""
UPDATE mission_lifecycle_events SET approval_mode = 'independent'
WHERE action = 'approve'
    """)
    op.execute("""
ALTER TABLE mission_lifecycle_events
    VALIDATE CONSTRAINT ck_mission_lifecycle_events_approval_mode_shape,
    VALIDATE CONSTRAINT ck_mission_lifecycle_events_owner_authorization_shape
    """)
    op.execute("""
CREATE FUNCTION guard_owner_mission_approval() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_pack varchar(160);
    proposing_actor uuid;
BEGIN
    IF NEW.action != 'approve' THEN
        RETURN NEW;
    END IF;
    SELECT d.target_pack_id, prior.actor_id
      INTO target_pack, proposing_actor
      FROM mission_definitions d
      JOIN mission_lifecycle_events prior
        ON prior.id = NEW.prior_event_id AND prior.mission_id = NEW.mission_id
     WHERE d.id = NEW.definition_id AND d.mission_id = NEW.mission_id;
    IF target_pack IS NULL OR proposing_actor IS NULL THEN
        RAISE EXCEPTION 'mission_approval_lineage_invalid' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.approval_mode = 'independent' AND NEW.actor_id = proposing_actor THEN
        RAISE EXCEPTION 'mission_self_approval_prohibited' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.approval_mode = 'owner' THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended('opennosh.governance-pack:' || target_pack, 0));
        IF NEW.actor_id != proposing_actor OR NOT EXISTS (
            SELECT 1 FROM governance_owner_authorizations a
             WHERE a.id = NEW.owner_authorization_id
               AND a.pack_id = target_pack
               AND a.actor_id = NEW.actor_id
               AND a.role = 'owner'
               AND a.granted_at <= NEW.occurred_at
               AND a.granted_at <= statement_timestamp()
               AND (a.revoked_at IS NULL OR
                    (a.revoked_at > NEW.occurred_at AND a.revoked_at > statement_timestamp()))
        ) THEN
            RAISE EXCEPTION 'owner_authorization_not_active' USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$
    """)
    op.execute("""
CREATE TRIGGER guard_owner_mission_approval
BEFORE INSERT ON mission_lifecycle_events
FOR EACH ROW EXECUTE FUNCTION guard_owner_mission_approval()
    """)


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM mission_lifecycle_events WHERE approval_mode = 'owner'
    ) THEN
        RAISE EXCEPTION 'owner_mission_history_prevents_downgrade';
    END IF;
END $$
    """)
    op.execute("DROP TRIGGER guard_owner_mission_approval ON mission_lifecycle_events")
    op.execute("DROP FUNCTION guard_owner_mission_approval()")
    op.execute("""
ALTER TABLE mission_lifecycle_events
    DROP CONSTRAINT ck_mission_lifecycle_events_owner_authorization_shape,
    DROP CONSTRAINT ck_mission_lifecycle_events_approval_mode_shape,
    DROP COLUMN owner_authorization_id,
    DROP COLUMN approval_mode
    """)
