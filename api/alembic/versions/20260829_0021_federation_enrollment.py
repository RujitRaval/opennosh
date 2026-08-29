"""add invitation-only federation enrollment

Revision ID: 20260829_0021
Revises: 20260829_0020
Create Date: 2026-08-29 09:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_0021"
down_revision: str | Sequence[str] | None = "20260829_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "federation_invitations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("github_account_id", sa.BigInteger(), nullable=False),
        sa.Column("github_login", sa.String(length=100), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("repository", sa.String(length=201), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("inviter_actor_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32",
            name=op.f("ck_federation_invitations_token_hash_sha256"),
        ),
        sa.CheckConstraint(
            "github_account_id > 0",
            name=op.f("ck_federation_invitations_github_account_id_positive"),
        ),
        sa.CheckConstraint(
            "repository_id > 0",
            name=op.f("ck_federation_invitations_repository_id_positive"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name=op.f("ck_federation_invitations_expiry_after_creation"),
        ),
        sa.CheckConstraint(
            "expires_at <= created_at + interval '24 hours'",
            name=op.f("ck_federation_invitations_expiry_within_24_hours"),
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name=op.f("ck_federation_invitations_consumption_after_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["inviter_actor_id"],
            ["users.id"],
            name=op.f("fk_federation_invitations_inviter_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_invitations")),
        sa.UniqueConstraint("token_hash", name="uq_federation_invitation_token_hash"),
    )
    op.create_index(
        "ix_federation_invitations_scope",
        "federation_invitations",
        ["repository_id", "pack_id"],
    )
    op.create_index(
        "uq_federation_single_invitation",
        "federation_invitations",
        [sa.text("(true)")],
        unique=True,
    )

    op.create_table(
        "federation_maintainers",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("github_account_id", sa.BigInteger(), nullable=False),
        sa.Column("github_login", sa.String(length=100), nullable=False),
        sa.Column("github_app_installation_id", sa.BigInteger(), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("repository", sa.String(length=201), nullable=False),
        sa.Column("pack_id", sa.String(length=160), nullable=False),
        sa.Column("current_role_key_id", sa.String(length=64), nullable=False),
        sa.Column("current_role_key_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("inviter_actor_id", sa.Uuid(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "github_account_id > 0",
            name=op.f("ck_federation_maintainers_github_account_id_positive"),
        ),
        sa.CheckConstraint(
            "github_app_installation_id > 0",
            name=op.f("ck_federation_maintainers_installation_id_positive"),
        ),
        sa.CheckConstraint(
            "repository_id > 0",
            name=op.f("ck_federation_maintainers_repository_id_positive"),
        ),
        sa.CheckConstraint(
            "state IN ('requested','verified','active','quarantined','revoked')",
            name=op.f("ck_federation_maintainers_state_allowed"),
        ),
        sa.CheckConstraint(
            "current_role_key_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_maintainers_current_key_fingerprint_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["inviter_actor_id"],
            ["users.id"],
            name=op.f("fk_federation_maintainers_inviter_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_maintainers")),
        sa.UniqueConstraint(
            "github_account_id",
            "repository_id",
            "pack_id",
            name="uq_federation_maintainer_identity_scope",
        ),
    )
    op.create_index(
        "ix_federation_maintainers_scope_state",
        "federation_maintainers",
        ["repository_id", "pack_id", "state"],
    )
    op.create_index(
        "uq_federation_active_repository_pack",
        "federation_maintainers",
        ["repository_id", "pack_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )

    op.create_table(
        "federation_role_keys",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("maintainer_id", sa.Uuid(), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("public_key", sa.String(length=64), nullable=False),
        sa.Column("public_key_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_by_actor_id", sa.Uuid(), nullable=False),
        sa.Column("prior_key_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "public_key_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_role_keys_public_key_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "retired_at IS NULL OR retired_at >= activated_at",
            name=op.f("ck_federation_role_keys_retirement_after_activation"),
        ),
        sa.CheckConstraint(
            "prior_key_id IS NULL OR prior_key_id != id",
            name=op.f("ck_federation_role_keys_prior_key_not_self"),
        ),
        sa.ForeignKeyConstraint(
            ["maintainer_id"],
            ["federation_maintainers.id"],
            name=op.f("fk_federation_role_keys_maintainer_id_federation_maintainers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rotated_by_actor_id"],
            ["users.id"],
            name=op.f("fk_federation_role_keys_rotated_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prior_key_id"],
            ["federation_role_keys.id"],
            name=op.f("fk_federation_role_keys_prior_key_id_federation_role_keys"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_role_keys")),
        sa.UniqueConstraint("key_id", name="uq_federation_role_key_id"),
        sa.UniqueConstraint("public_key_fingerprint", name="uq_federation_role_key_fingerprint"),
    )
    op.create_index(
        "uq_federation_unretired_maintainer_key",
        "federation_role_keys",
        ["maintainer_id"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )

    op.create_table(
        "federation_audit_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("maintainer_id", sa.Uuid(), nullable=True),
        sa.Column("invitation_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_federation_audit_events_payload_digest_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["maintainer_id"],
            ["federation_maintainers.id"],
            name=op.f("fk_federation_audit_events_maintainer_id_federation_maintainers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["federation_invitations.id"],
            name=op.f("fk_federation_audit_events_invitation_id_federation_invitations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_federation_audit_events_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_federation_audit_events")),
    )
    op.create_index(
        "ix_federation_audit_maintainer_created",
        "federation_audit_events",
        ["maintainer_id", "created_at"],
    )
    op.create_index(
        "ix_federation_audit_invitation_created",
        "federation_audit_events",
        ["invitation_id", "created_at"],
    )

    op.execute(
        """
        CREATE FUNCTION guard_federation_invitation_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR NEW.token_hash IS DISTINCT FROM OLD.token_hash
                OR NEW.github_account_id IS DISTINCT FROM OLD.github_account_id
                OR NEW.github_login IS DISTINCT FROM OLD.github_login
                OR NEW.repository_id IS DISTINCT FROM OLD.repository_id
                OR NEW.repository IS DISTINCT FROM OLD.repository
                OR NEW.pack_id IS DISTINCT FROM OLD.pack_id
                OR NEW.inviter_actor_id IS DISTINCT FROM OLD.inviter_actor_id
                OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
                OR OLD.consumed_at IS NOT NULL
                OR NEW.consumed_at IS NULL
            THEN
                RAISE EXCEPTION 'federation_invitation_is_immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_federation_invitation_update "
        "BEFORE UPDATE ON federation_invitations FOR EACH ROW "
        "EXECUTE FUNCTION guard_federation_invitation_update()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_federation_maintainer_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR NEW.github_account_id IS DISTINCT FROM OLD.github_account_id
                OR NEW.github_login IS DISTINCT FROM OLD.github_login
                OR NEW.github_app_installation_id IS DISTINCT FROM OLD.github_app_installation_id
                OR NEW.repository_id IS DISTINCT FROM OLD.repository_id
                OR NEW.repository IS DISTINCT FROM OLD.repository
                OR NEW.pack_id IS DISTINCT FROM OLD.pack_id
                OR NEW.inviter_actor_id IS DISTINCT FROM OLD.inviter_actor_id
                OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
                OR NOT (
                    (OLD.state = 'requested' AND NEW.state = 'verified'
                        AND OLD.verified_at IS NULL
                        AND NEW.verified_at IS NOT NULL
                        AND NEW.activated_at IS NOT DISTINCT FROM OLD.activated_at
                        AND NEW.quarantined_at IS NOT DISTINCT FROM OLD.quarantined_at
                        AND NEW.revoked_at IS NOT DISTINCT FROM OLD.revoked_at
                        AND NEW.current_role_key_id IS NOT DISTINCT FROM OLD.current_role_key_id
                        AND NEW.current_role_key_fingerprint
                            IS NOT DISTINCT FROM OLD.current_role_key_fingerprint)
                    OR (OLD.state = 'verified' AND NEW.state = 'active'
                        AND NEW.verified_at IS NOT DISTINCT FROM OLD.verified_at
                        AND OLD.activated_at IS NULL
                        AND NEW.activated_at IS NOT NULL
                        AND NEW.quarantined_at IS NOT DISTINCT FROM OLD.quarantined_at
                        AND NEW.revoked_at IS NOT DISTINCT FROM OLD.revoked_at
                        AND NEW.current_role_key_id IS NOT DISTINCT FROM OLD.current_role_key_id
                        AND NEW.current_role_key_fingerprint
                            IS NOT DISTINCT FROM OLD.current_role_key_fingerprint)
                    OR (OLD.state = 'active' AND NEW.state = 'active'
                        AND NEW.verified_at IS NOT DISTINCT FROM OLD.verified_at
                        AND NEW.activated_at IS NOT DISTINCT FROM OLD.activated_at
                        AND NEW.quarantined_at IS NOT DISTINCT FROM OLD.quarantined_at
                        AND NEW.revoked_at IS NOT DISTINCT FROM OLD.revoked_at
                        AND NEW.current_role_key_id IS DISTINCT FROM OLD.current_role_key_id
                        AND NEW.current_role_key_fingerprint
                            IS DISTINCT FROM OLD.current_role_key_fingerprint)
                    OR (OLD.state = 'active' AND NEW.state = 'quarantined'
                        AND NEW.verified_at IS NOT DISTINCT FROM OLD.verified_at
                        AND NEW.activated_at IS NOT DISTINCT FROM OLD.activated_at
                        AND OLD.quarantined_at IS NULL
                        AND NEW.quarantined_at IS NOT NULL
                        AND NEW.revoked_at IS NOT DISTINCT FROM OLD.revoked_at
                        AND NEW.current_role_key_id IS NOT DISTINCT FROM OLD.current_role_key_id
                        AND NEW.current_role_key_fingerprint
                            IS NOT DISTINCT FROM OLD.current_role_key_fingerprint)
                    OR (OLD.state = 'active' AND NEW.state = 'revoked'
                        AND NEW.verified_at IS NOT DISTINCT FROM OLD.verified_at
                        AND NEW.activated_at IS NOT DISTINCT FROM OLD.activated_at
                        AND NEW.quarantined_at IS NOT DISTINCT FROM OLD.quarantined_at
                        AND OLD.revoked_at IS NULL
                        AND NEW.revoked_at IS NOT NULL
                        AND NEW.current_role_key_id IS NOT DISTINCT FROM OLD.current_role_key_id
                        AND NEW.current_role_key_fingerprint
                            IS NOT DISTINCT FROM OLD.current_role_key_fingerprint)
                )
            THEN
                RAISE EXCEPTION 'federation_maintainer_transition_invalid'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_federation_maintainer_update "
        "BEFORE UPDATE ON federation_maintainers FOR EACH ROW "
        "EXECUTE FUNCTION guard_federation_maintainer_update()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_federation_role_key_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR NEW.maintainer_id IS DISTINCT FROM OLD.maintainer_id
                OR NEW.key_id IS DISTINCT FROM OLD.key_id
                OR NEW.public_key IS DISTINCT FROM OLD.public_key
                OR NEW.public_key_fingerprint IS DISTINCT FROM OLD.public_key_fingerprint
                OR NEW.activated_at IS DISTINCT FROM OLD.activated_at
                OR NEW.rotated_by_actor_id IS DISTINCT FROM OLD.rotated_by_actor_id
                OR NEW.prior_key_id IS DISTINCT FROM OLD.prior_key_id
                OR OLD.retired_at IS NOT NULL
                OR NEW.retired_at IS NULL
            THEN
                RAISE EXCEPTION 'federation_role_key_is_immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_federation_role_key_update "
        "BEFORE UPDATE ON federation_role_keys FOR EACH ROW "
        "EXECUTE FUNCTION guard_federation_role_key_update()"
    )
    op.execute(
        """
        CREATE FUNCTION guard_federation_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'federation_audit_rows_are_append_only'
                USING ERRCODE = 'check_violation';
        END;
        $$
        """
    )
    for table in (
        "federation_invitations",
        "federation_maintainers",
        "federation_role_keys",
        "federation_audit_events",
    ):
        op.execute(
            f"CREATE TRIGGER guard_{table}_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION guard_federation_append_only()"
        )
    op.execute(
        "CREATE TRIGGER guard_federation_audit_event_update "
        "BEFORE UPDATE ON federation_audit_events FOR EACH ROW "
        "EXECUTE FUNCTION guard_federation_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION guard_federation_append_only() CASCADE")
    op.execute("DROP FUNCTION guard_federation_role_key_update() CASCADE")
    op.execute("DROP FUNCTION guard_federation_maintainer_update() CASCADE")
    op.execute("DROP FUNCTION guard_federation_invitation_update() CASCADE")
    op.drop_table("federation_audit_events")
    op.drop_table("federation_role_keys")
    op.drop_table("federation_maintainers")
    op.drop_table("federation_invitations")
