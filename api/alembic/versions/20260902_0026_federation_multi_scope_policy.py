"""replace the global invitation slot with per-scope policy

Revision ID: 20260902_0026
Revises: 20260902_0025
Create Date: 2026-09-02 02:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0026"
down_revision: str | Sequence[str] | None = "20260902_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_federation_single_invitation", table_name="federation_invitations")
    op.drop_index("ix_federation_invitations_scope", table_name="federation_invitations")
    op.create_unique_constraint(
        "uq_federation_invitation_scope",
        "federation_invitations",
        ["repository_id", "pack_id"],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF (SELECT count(*) FROM federation_invitations) > 1 THEN
                RAISE EXCEPTION
                    'T34.5b refuses to collapse multiple federation invitation scopes';
            END IF;
        END $$;
        """
    )
    op.drop_constraint(
        "uq_federation_invitation_scope",
        "federation_invitations",
        type_="unique",
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
