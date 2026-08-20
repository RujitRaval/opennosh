"""harden the attributed wger exercise catalogue

Revision ID: 20260820_0010
Revises: 20260820_0009
Create Date: 2026-08-20 00:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260820_0010"
down_revision: str | Sequence[str] | None = "20260820_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SAFE_URL_PATTERN = (
    r"^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?"
    r"(:([1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|"
    r"65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5]))?"
    r"(/[^[:space:]<>\"''\\]*)?$"
)


def upgrade() -> None:
    op.add_column(
        "exercises",
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "exercises",
        sa.Column(
            "translations_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "exercises",
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM exercises
                WHERE length(slug) NOT BETWEEN 1 AND 160
                   OR length(name) NOT BETWEEN 1 AND 255
                   OR slug ~ '[<>[:cntrl:]]'
                   OR name ~ '[<>[:cntrl:]]'
                   OR length(search_text) > 50000
                   OR jsonb_typeof(muscle_groups) <> 'array'
                   OR jsonb_typeof(equipment) <> 'array'
                   OR jsonb_typeof(translations_json) <> 'array'
                   OR jsonb_typeof(translation_attribution_json) <> 'array'
                   OR EXISTS (
                       SELECT 1 FROM jsonb_array_elements(muscle_groups) AS item
                       WHERE jsonb_typeof(item) <> 'string'
                          OR length(item #>> '{{}}') NOT BETWEEN 1 AND 100
                          OR item #>> '{{}}' ~ '[<>[:cntrl:]]'
                   )
                   OR EXISTS (
                       SELECT 1 FROM jsonb_array_elements(equipment) AS item
                       WHERE jsonb_typeof(item) <> 'string'
                          OR length(item #>> '{{}}') NOT BETWEEN 1 AND 100
                          OR item #>> '{{}}' ~ '[<>[:cntrl:]]'
                   )
                   OR EXISTS (
                       SELECT 1 FROM jsonb_array_elements(translations_json) AS item
                       WHERE jsonb_typeof(item) <> 'object'
                   )
                   OR EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements(translation_attribution_json) AS item
                       WHERE jsonb_typeof(item) <> 'object'
                          OR item->>'license_spdx' IS DISTINCT FROM 'CC-BY-SA-3.0'
                          OR item->>'license_url' IS DISTINCT FROM
                             'https://creativecommons.org/licenses/by-sa/3.0/'
                          OR coalesce(item->>'source_id', '') = ''
                          OR coalesce(item->>'language_id', '') = ''
                          OR coalesce(item->>'author', '') = ''
                          OR coalesce(item->>'attribution_text', '') = ''
                          OR (item ? 'source_url' AND item->>'source_url' IS NOT NULL
                              AND item->>'source_url' !~ '{_SAFE_URL_PATTERN}')
                          OR (item ? 'derivative_source_url'
                              AND item->>'derivative_source_url' IS NOT NULL
                              AND item->>'derivative_source_url' !~ '{_SAFE_URL_PATTERN}')
                          OR (item ? 'author_url' AND item->>'author_url' IS NOT NULL
                              AND item->>'author_url' !~ '{_SAFE_URL_PATTERN}')
                   )
                   OR source_url !~ '{_SAFE_URL_PATTERN}'
                   OR (derivative_source_url IS NOT NULL
                       AND derivative_source_url !~ '{_SAFE_URL_PATTERN}')
                   OR (author_url IS NOT NULL AND author_url !~ '{_SAFE_URL_PATTERN}')
                   OR (
                       source = 'wger'
                       AND NOT (
                           license_spdx = 'CC-BY-SA-3.0'
                           AND license_url =
                               'https://creativecommons.org/licenses/by-sa/3.0/'
                       )
                   )
            ) THEN
                RAISE EXCEPTION 'Cannot migrate invalid legacy exercise attribution rows';
            END IF;
        END
        $$
        """
    )
    for name, condition in (
        ("slug_bounded", "length(slug) BETWEEN 1 AND 160"),
        ("name_bounded", "length(name) BETWEEN 1 AND 255"),
        ("slug_plain", "slug !~ '[<>[:cntrl:]]'"),
        ("name_plain", "name !~ '[<>[:cntrl:]]'"),
        ("search_text_bounded", "length(search_text) <= 50000"),
        (
            "source_updated_at_supported",
            "source_updated_at IS NULL OR ("
            "source_updated_at >= TIMESTAMPTZ '0001-01-01 00:00:00.000001+00' AND "
            "source_updated_at <= TIMESTAMPTZ '9999-12-31 23:59:59.999998+00')",
        ),
        ("muscles_array", "jsonb_typeof(muscle_groups) = 'array'"),
        ("equipment_array", "jsonb_typeof(equipment) = 'array'"),
        ("translations_array", "jsonb_typeof(translations_json) = 'array'"),
        (
            "translation_attribution_array",
            "jsonb_typeof(translation_attribution_json) = 'array'",
        ),
        (
            "muscles_strings",
            "NOT jsonb_path_exists(muscle_groups, '$[*] ? (@.type() != \"string\")')",
        ),
        ("muscles_plain", "muscle_groups::text !~ '[<>]'"),
        (
            "equipment_strings",
            "NOT jsonb_path_exists(equipment, '$[*] ? (@.type() != \"string\")')",
        ),
        ("equipment_plain", "equipment::text !~ '[<>]'"),
        (
            "translations_objects",
            "NOT jsonb_path_exists(translations_json, '$[*] ? (@.type() != \"object\")')",
        ),
        (
            "translation_attribution_objects",
            "NOT jsonb_path_exists(translation_attribution_json, "
            "'$[*] ? (@.type() != \"object\")')",
        ),
        (
            "wger_license_allowed",
            "source <> 'wger' OR (license_spdx = 'CC-BY-SA-3.0' AND "
            "license_url = 'https://creativecommons.org/licenses/by-sa/3.0/')",
        ),
        (
            "source_url_http",
            f"source_url ~ '{_SAFE_URL_PATTERN}'",
        ),
        (
            "derivative_source_url_http",
            "derivative_source_url IS NULL OR "
            f"derivative_source_url ~ '{_SAFE_URL_PATTERN}'",
        ),
        (
            "author_url_http",
            f"author_url IS NULL OR author_url ~ '{_SAFE_URL_PATTERN}'",
        ),
    ):
        op.create_check_constraint(op.f(f"ck_exercises_{name}"), "exercises", condition)
    op.create_index(
        "ix_exercises_muscle_groups_gin",
        "exercises",
        ["muscle_groups"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_exercises_equipment_gin",
        "exercises",
        ["equipment"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_exercises_name_trgm",
        "exercises",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_exercises_search_tsv",
        "exercises",
        [
            sa.text(
                "to_tsvector('simple'::regconfig, "
                "(name::text || ' '::text) || search_text)"
            )
        ],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_exercises_search_tsv", table_name="exercises")
    op.drop_index("ix_exercises_name_trgm", table_name="exercises")
    op.drop_index("ix_exercises_equipment_gin", table_name="exercises")
    op.drop_index("ix_exercises_muscle_groups_gin", table_name="exercises")
    for name in (
        "author_url_http",
        "derivative_source_url_http",
        "source_url_http",
        "wger_license_allowed",
        "equipment_plain",
        "muscles_plain",
        "translation_attribution_objects",
        "translations_objects",
        "equipment_strings",
        "muscles_strings",
        "translation_attribution_array",
        "translations_array",
        "equipment_array",
        "muscles_array",
        "search_text_bounded",
        "source_updated_at_supported",
        "name_bounded",
        "name_plain",
        "slug_plain",
        "slug_bounded",
    ):
        op.drop_constraint(op.f(f"ck_exercises_{name}"), "exercises", type_="check")
    op.drop_column("exercises", "source_updated_at")
    op.drop_column("exercises", "translations_json")
    op.drop_column("exercises", "search_text")
