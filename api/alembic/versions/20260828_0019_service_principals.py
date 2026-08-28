"""Add non-login service principals.

Revision ID: 20260828_0019
Revises: 20260826_0018
Create Date: 2026-08-28 17:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0019"
down_revision: str | Sequence[str] | None = "20260826_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "actor_kind",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'person'"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("login_disabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_users_actor_kind_allowed"),
        "users",
        "actor_kind IN ('person', 'service')",
    )
    op.create_check_constraint(
        op.f("ck_users_service_login_disabled"),
        "users",
        "(actor_kind = 'person' AND login_disabled_at IS NULL) OR "
        "(actor_kind = 'service' AND login_disabled_at IS NOT NULL "
        "AND recovery_token_hash IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_users_service_login_disabled"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_actor_kind_allowed"), "users", type_="check")
    op.drop_column("users", "login_disabled_at")
    op.drop_column("users", "actor_kind")
