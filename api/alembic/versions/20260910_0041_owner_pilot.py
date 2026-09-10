"""Explicit owner review and bounded citation preservation on existing PostgreSQL.

Revision ID: 20260910_0041
Revises: 20260909_0040
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0041"
down_revision: str | Sequence[str] | None = "20260909_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_foods_community_source_license_allowed"), "foods_community")
    op.create_check_constraint(
        op.f("ck_foods_community_source_license_allowed"),
        "foods_community",
        "source_license IN ('contributor-original', 'CC0-1.0', 'public-domain', 'reference-only')",
    )
    op.execute("""
CREATE TABLE governance_owner_authorizations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            pack_id varchar(160) NOT NULL,
            actor_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            role varchar(32) NOT NULL DEFAULT 'owner'
                CONSTRAINT ck_governance_owner_authorizations_role_allowed CHECK (role = 'owner'),
            granted_by_actor_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            grant_reason varchar(1000) NOT NULL
                CONSTRAINT ck_governance_owner_authorizations_grant_reason_nonempty
                CHECK (length(trim(grant_reason)) > 0),
            granted_at timestamptz NOT NULL,
            revoked_by_actor_id uuid REFERENCES users(id) ON DELETE RESTRICT,
            revocation_reason varchar(1000),
            revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_governance_owner_scope UNIQUE (pack_id, actor_id, role),
            CONSTRAINT ck_governance_owner_authorizations_revocation_after_grant
                CHECK (revoked_at IS NULL OR revoked_at >= granted_at),
            CONSTRAINT ck_governance_owner_authorizations_revocation_audit_complete
            CHECK ((revoked_at IS NULL AND revoked_by_actor_id IS NULL
                AND revocation_reason IS NULL)
                OR (revoked_at IS NOT NULL AND revoked_by_actor_id IS NOT NULL
                    AND revocation_reason IS NOT NULL AND length(trim(revocation_reason)) > 0))
        )
    """)
    op.execute("""
CREATE INDEX ix_governance_owners_actor_scope
            ON governance_owner_authorizations(actor_id, pack_id, role)
    """)
    op.execute("""
CREATE TRIGGER serialize_owner_authorization_change
            BEFORE INSERT OR UPDATE ON governance_owner_authorizations
            FOR EACH ROW EXECUTE FUNCTION serialize_governance_pack_change()
    """)
    op.execute("""
CREATE TRIGGER guard_owner_authorization_update
            BEFORE UPDATE ON governance_owner_authorizations
            FOR EACH ROW EXECUTE FUNCTION guard_governance_role_revocation()
    """)
    op.execute("""
CREATE TRIGGER prohibit_owner_authorization_delete
            BEFORE DELETE ON governance_owner_authorizations
            FOR EACH ROW EXECUTE FUNCTION prohibit_governance_audit_delete()
    """)
    op.execute("""
ALTER TABLE governance_decisions
            ADD COLUMN approval_mode varchar(16) NOT NULL DEFAULT 'independent',
            ADD COLUMN owner_authorization_id uuid
                REFERENCES governance_owner_authorizations(id) ON DELETE RESTRICT,
            ADD CONSTRAINT ck_governance_decisions_approval_mode_valid CHECK (
                (approval_mode = 'independent' AND owner_authorization_id IS NULL
                    AND contributor_actor_id != deciding_actor_id)
                OR (approval_mode = 'owner' AND owner_authorization_id IS NOT NULL
                    AND contributor_actor_id = deciding_actor_id)
            )
    """)
    op.execute("""
CREATE FUNCTION guard_owner_decision_authorization() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.approval_mode = 'owner' THEN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended('opennosh.governance-pack:' || NEW.pack_id, 0));
                IF NOT EXISTS (
                    SELECT 1 FROM governance_owner_authorizations a
                    JOIN contribution_drafts draft ON draft.id = NEW.source_draft_id
                    WHERE a.id = NEW.owner_authorization_id AND a.pack_id = NEW.pack_id
                      AND draft.user_id = NEW.contributor_actor_id
                      AND draft.draft_version = NEW.source_draft_version
                      AND draft.fields_json->>'pack_id' = NEW.pack_id
                      AND a.actor_id = NEW.deciding_actor_id
                      AND a.granted_at <= NEW.decided_at
                      AND a.granted_at <= statement_timestamp()
                      AND (a.revoked_at IS NULL OR
                        (a.revoked_at > NEW.decided_at AND a.revoked_at > statement_timestamp()))
                ) THEN
                    RAISE EXCEPTION 'owner_authorization_not_active'
                USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
CREATE TRIGGER guard_owner_decision_authorization
            BEFORE INSERT ON governance_decisions
            FOR EACH ROW EXECUTE FUNCTION guard_owner_decision_authorization()
    """)
    # Preserve the complete existing successor/resubmission guard, changing only its
    # two self-review predicates. The new trigger independently verifies the bound grant.
    definition = op.get_bind().scalar(
        sa.text(
            "SELECT pg_get_functiondef('guard_governance_resubmission_decision()'::regprocedure)"
        )
    )
    old = "current_draft.user_id = NEW.deciding_actor_id"
    if not isinstance(definition, str) or definition.count(old) != 2:
        raise RuntimeError("Unexpected governance lineage guard; owner migration requires review")
    op.execute(definition.replace(old, f"({old} AND NEW.approval_mode != 'owner')"))
    op.execute("""
CREATE TABLE evidence_citation_copies (
            evidence_id uuid PRIMARY KEY REFERENCES evidence_manifests(id) ON DELETE RESTRICT,
            canonical_bytes bytea NOT NULL
                CONSTRAINT ck_evidence_citation_copies_bytes_bounded
                CHECK (octet_length(canonical_bytes) BETWEEN 1 AND 8192),
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
CREATE TRIGGER immutable_citation_copy_update BEFORE UPDATE ON evidence_citation_copies
            FOR EACH ROW EXECUTE FUNCTION prohibit_governance_audit_update()
    """)
    op.execute("""
CREATE TRIGGER immutable_citation_copy_delete BEFORE DELETE ON evidence_citation_copies
            FOR EACH ROW EXECUTE FUNCTION prohibit_governance_audit_delete()
    """)
    op.execute("""
CREATE FUNCTION preserve_reference_citation(evidence uuid, payload bytea)
            RETURNS text LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public AS $$
        DECLARE
            m public.evidence_manifests%ROWTYPE;
            d public.contribution_drafts%ROWTYPE;
            stored bytea;
            digest_text text;
            destination_text text := 'postgres:opennosh:evidence-citations';
        BEGIN
            SELECT * INTO m FROM public.evidence_manifests WHERE id = evidence FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'citation_manifest_missing'; END IF;
            SELECT * INTO d FROM public.contribution_drafts WHERE id = m.source_draft_id;
            IF NOT FOUND OR d.draft_version != m.source_draft_version
                OR d.review_state NOT IN ('draft', 'in_review', 'changes_requested')
                OR m.public_state = 'tombstoned'
                OR EXISTS (SELECT 1 FROM public.evidence_removal_tombstones
                WHERE evidence_id = evidence)
                OR m.evidence_class != 'public_document'
                OR m.manifest_json->>'evidence_class' IS DISTINCT FROM 'public_document'
                OR m.manifest_json->>'rights_state' IS DISTINCT FROM 'reference_only'
                OR m.manifest_json->>'storage_reference' IS NOT NULL
                OR m.manifest_json->>'canonical_uri' IS DISTINCT FROM d.fields_json->>'source_uri'
                OR m.manifest_json->>'license' IS DISTINCT FROM d.fields_json->>'source_license'
                OR d.fields_json->>'evidence_type' IS DISTINCT FROM 'public_document'
                OR d.fields_json->>'rights_acknowledged' IS DISTINCT FROM 'true'
                OR octet_length(payload) NOT BETWEEN 1 AND 8192
                OR convert_from(payload, 'UTF8')::jsonb IS DISTINCT FROM m.manifest_json
                OR encode(sha256(payload), 'hex') IS DISTINCT FROM m.manifest_digest
            THEN RAISE EXCEPTION 'citation_reference_binding_invalid'
                USING ERRCODE = 'check_violation';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.evidence_durable_acknowledgements
                WHERE evidence_id = evidence AND
                    (acknowledgement_kind != 'citation_manifest' OR destination != destination_text)
            ) THEN RAISE EXCEPTION 'citation_already_preserved_elsewhere'; END IF;
            INSERT INTO public.evidence_citation_copies(evidence_id, canonical_bytes)
                VALUES(evidence, payload) ON CONFLICT DO NOTHING;
            SELECT canonical_bytes INTO stored FROM public.evidence_citation_copies
                WHERE evidence_id = evidence;
            IF stored IS DISTINCT FROM payload THEN
                RAISE EXCEPTION 'citation_copy_conflict';
            END IF;
            digest_text := encode(sha256(stored), 'hex');
            IF EXISTS (
                SELECT 1 FROM public.evidence_durable_acknowledgements
                WHERE evidence_id = evidence AND acknowledgement_kind = 'citation_manifest'
                  AND destination = destination_text
                  AND (content_digest != digest_text OR manifest_digest != digest_text

                OR external_reference != 'postgres:opennosh:evidence-citations:' || evidence::text
                    OR adapter_identity != 'opennosh.postgres-citation' OR adapter_version != '1.0'
                    OR evidence_class != 'public_document')
            ) THEN RAISE EXCEPTION 'citation_acknowledgement_conflict'; END IF;
            INSERT INTO public.evidence_durable_acknowledgements (
                schema_version, evidence_id, evidence_class, manifest_digest, acknowledgement_kind,
                destination, content_digest, external_reference, verified_at,
                adapter_identity, adapter_version
            ) VALUES ('1.0', evidence, 'public_document', digest_text, 'citation_manifest',
                destination_text, digest_text, 'postgres:opennosh:evidence-citations:'
                    || evidence::text,
                clock_timestamp(), 'opennosh.postgres-citation', '1.0') ON CONFLICT DO NOTHING;
            UPDATE public.evidence_manifests SET public_state = 'reference_only'
                WHERE id = evidence;
            RETURN digest_text;
        END $$
    """)
    op.execute("""
REVOKE ALL ON FUNCTION preserve_reference_citation(uuid, bytea) FROM PUBLIC
    """)
    op.execute("""
DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opennosh_web') THEN
                REVOKE INSERT, UPDATE, DELETE ON governance_owner_authorizations FROM opennosh_web;
                REVOKE INSERT, UPDATE, DELETE ON evidence_citation_copies FROM opennosh_web;
            END IF;
        END $$
    """)


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM foods_community WHERE source_license = 'reference-only')"
        )
    ):
        raise RuntimeError("Reference-only food source history must be preserved")
    op.drop_constraint(op.f("ck_foods_community_source_license_allowed"), "foods_community")
    op.create_check_constraint(
        op.f("ck_foods_community_source_license_allowed"),
        "foods_community",
        "source_license IN ('contributor-original', 'CC0-1.0', 'public-domain')",
    )
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM governance_decisions WHERE approval_mode = 'owner')")
    ):
        raise RuntimeError("Owner approval history must be preserved; cannot downgrade")
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM evidence_citation_copies)")):
        raise RuntimeError("Preserved citation history must be retained; cannot downgrade")
    op.execute("DROP FUNCTION preserve_reference_citation(uuid, bytea)")
    op.drop_table("evidence_citation_copies")
    definition = op.get_bind().scalar(
        sa.text(
            "SELECT pg_get_functiondef('guard_governance_resubmission_decision()'::regprocedure)"
        )
    )
    op.execute(
        definition.replace(
            "(current_draft.user_id = NEW.deciding_actor_id AND NEW.approval_mode != 'owner')",
            "current_draft.user_id = NEW.deciding_actor_id",
        )
    )
    op.execute("DROP TRIGGER guard_owner_decision_authorization ON governance_decisions")
    op.execute("DROP FUNCTION guard_owner_decision_authorization()")
    op.drop_constraint(op.f("ck_governance_decisions_approval_mode_valid"), "governance_decisions")
    op.drop_column("governance_decisions", "owner_authorization_id")
    op.drop_column("governance_decisions", "approval_mode")
    op.drop_table("governance_owner_authorizations")
