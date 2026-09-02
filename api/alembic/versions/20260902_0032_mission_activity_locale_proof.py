"""bind mission activity locale proof into immutable progress records

Revision ID: 20260902_0032
Revises: 20260902_0031
Create Date: 2026-09-02 23:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0032"
down_revision: str | Sequence[str] | None = "20260902_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mission_progress_records",
        sa.Column("activity_locale", sa.String(length=35), nullable=True),
    )
    op.add_column(
        "mission_progress_records",
        sa.Column("activity_pack_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "mission_progress_records",
        sa.Column("activity_source_digest", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_mission_progress_records_activity_proof_complete"),
        "mission_progress_records",
        "(activity_locale IS NULL AND activity_pack_version IS NULL "
        "AND activity_source_digest IS NULL) OR "
        "(activity_locale IS NOT NULL AND activity_pack_version IS NOT NULL "
        "AND activity_source_digest IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_mission_progress_records_activity_source_digest_sha256"),
        "mission_progress_records",
        "activity_source_digest IS NULL OR activity_source_digest ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_mission_progress_records_activity_source_digest_sha256"),
        "mission_progress_records",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_mission_progress_records_activity_proof_complete"),
        "mission_progress_records",
        type_="check",
    )
    op.drop_column("mission_progress_records", "activity_source_digest")
    op.drop_column("mission_progress_records", "activity_pack_version")
    op.drop_column("mission_progress_records", "activity_locale")
