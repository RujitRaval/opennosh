from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from opennosh_api.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from opennosh_api.models.enums import (
    BodyMetricType,
    BodyMetricUnit,
    FoodSourceTable,
    LoadUnit,
    Provenance,
    TargetDayType,
)
from opennosh_api.targets.constants import (
    DEFAULT_TARGET_KCAL_FLOOR,
    MAX_KCAL,
    MAX_MACRO_GRAMS,
)

INGREDIENT_SOURCE_TABLE_VALUES = ", ".join(
    repr(value.value) for value in FoodSourceTable if value is not FoodSourceTable.RECIPE
)
LOG_SOURCE_TABLE_VALUES = ", ".join(repr(value.value) for value in FoodSourceTable)
PROVENANCE_VALUES = ", ".join(repr(value.value) for value in Provenance)
SOURCE_LICENSE_VALUES = "'contributor-original', 'CC0-1.0', 'public-domain'"
LOAD_UNIT_VALUES = ", ".join(repr(value.value) for value in LoadUnit)
TARGET_DAY_TYPE_VALUES = ", ".join(repr(value.value) for value in TargetDayType)
BODY_METRIC_TYPE_VALUES = ", ".join(repr(value.value) for value in BodyMetricType)
BODY_METRIC_UNIT_VALUES = ", ".join(repr(value.value) for value in BodyMetricUnit)


class FoodReference(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "foods_reference"
    __table_args__ = (
        CheckConstraint("source = 'usda'", name="source_usda"),
        CheckConstraint("license = 'CC0'", name="license_cc0"),
        Index(
            "ix_foods_reference_search_tsv",
            text("to_tsvector('simple'::regconfig, coalesce(description, ''))"),
            postgresql_using="gin",
        ),
        Index(
            "ix_foods_reference_description_trgm",
            "description",
            postgresql_using="gin",
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
    )

    fdc_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    food_category: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default="usda")
    license: Mapped[str] = mapped_column(String(32), nullable=False, server_default="CC0")
    nutrients_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    portions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FoodCommunity(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "foods_community"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_foods_community_slug"),
        CheckConstraint(f"provenance IN ({PROVENANCE_VALUES})", name="provenance_allowed"),
        CheckConstraint(
            f"source_license IN ({SOURCE_LICENSE_VALUES})", name="source_license_allowed"
        ),
        CheckConstraint("pack_license = 'CC0-1.0'", name="pack_license_cc0"),
        Index(
            "ix_foods_community_search_tsv",
            text(
                "to_tsvector('simple'::regconfig, ((((("
                "coalesce(slug, ''::character varying)::text || ' '::text) || "
                "coalesce(name, ''::character varying)::text) || ' '::text) || "
                "coalesce(name_local, ''::character varying)::text) || ' '::text) || "
                "coalesce(category, ''::character varying)::text)"
            ),
            postgresql_using="gin",
        ),
        Index(
            "ix_foods_community_slug_trgm",
            "slug",
            postgresql_using="gin",
            postgresql_ops={"slug": "gin_trgm_ops"},
        ),
        Index(
            "ix_foods_community_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index(
            "ix_foods_community_name_local_trgm",
            "name_local",
            postgresql_using="gin",
            postgresql_ops={"name_local": "gin_trgm_ops"},
        ),
    )

    pack_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    pack_version: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_local: Mapped[str | None] = mapped_column(String(255))
    locale: Mapped[str | None] = mapped_column(String(35))
    category: Mapped[str] = mapped_column(String(160), nullable=False)
    provenance: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2048))
    source_license: Mapped[str] = mapped_column(String(64), nullable=False)
    source_note: Mapped[str | None] = mapped_column(Text)
    nutrients_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    portions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    pack_license: Mapped[str] = mapped_column(String(32), nullable=False, server_default="CC0-1.0")
    contributed_by: Mapped[str] = mapped_column(String(100), nullable=False)


class FoodSearchSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "food_search_snapshots"
    __table_args__ = (
        CheckConstraint("ranking_version > 0", name="ranking_version_positive"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint(
            "(federation_checkpoint_id IS NULL) = (release_set_digest IS NULL)",
            name="release_set_binding_complete",
        ),
        CheckConstraint(
            "release_set_digest IS NULL OR release_set_digest ~ '^[0-9a-f]{64}$'",
            name="release_set_digest_sha256",
        ),
        CheckConstraint(
            "jsonb_typeof(selected_pack_ids) = 'array' AND "
            "jsonb_array_length(selected_pack_ids) <= 20",
            name="selected_pack_ids_array",
        ),
        Index("ix_food_search_snapshots_created_at", "created_at"),
        Index(
            "ix_food_search_snapshots_release_set",
            "ranking_version",
            "federation_checkpoint_id",
            "created_at",
        ),
    )

    ranking_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    federation_checkpoint_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("federation_projection_checkpoints.id", ondelete="RESTRICT")
    )
    release_set_digest: Mapped[str | None] = mapped_column(String(64))
    selected_pack_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class FoodSearchSnapshotItem(Base):
    __tablename__ = "food_search_snapshot_items"
    __table_args__ = (
        CheckConstraint(
            "source IN ('usda', 'community', 'federation')", name="source_allowed"
        ),
        CheckConstraint("variant_count > 0", name="variant_count_positive"),
        CheckConstraint(
            "source <> 'federation' OR (source_record_id IS NOT NULL AND "
            "verified_release_id IS NOT NULL AND release_version IS NOT NULL AND "
            "release_digest IS NOT NULL AND equivalence_group_id IS NOT NULL AND "
            "variant_id IS NOT NULL)",
            name="federation_binding_complete",
        ),
        CheckConstraint(
            "release_digest IS NULL OR release_digest ~ '^[0-9a-f]{64}$'",
            name="release_digest_sha256",
        ),
        Index("ix_food_search_snapshot_items_pack", "snapshot_id", "pack_id"),
        Index(
            "ix_food_search_snapshot_items_equivalence",
            "snapshot_id",
            "equivalence_group_id",
        ),
        Index(
            "ix_food_search_snapshot_items_search_tsv",
            text(
                "to_tsvector('simple'::regconfig, (((("
                "(coalesce(source_id, ''::character varying)::text || ' '::text) || "
                "coalesce(name, ''::character varying)::text) || ' '::text) || "
                "coalesce(name_local, ''::character varying)::text) || ' '::text) || "
                "coalesce(category, ''::character varying)::text)"
            ),
            postgresql_using="gin",
        ),
        Index(
            "ix_food_search_snapshot_items_source_id_trgm",
            "source_id",
            postgresql_using="gin",
            postgresql_ops={"source_id": "gin_trgm_ops"},
        ),
        Index(
            "ix_food_search_snapshot_items_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index(
            "ix_food_search_snapshot_items_name_local_trgm",
            "name_local",
            postgresql_using="gin",
            postgresql_ops={"name_local": "gin_trgm_ops"},
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("food_search_snapshots.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    source_record_id: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    name_local: Mapped[str | None] = mapped_column(String(255))
    locale: Mapped[str | None] = mapped_column(String(35))
    category: Mapped[str | None] = mapped_column(String(255))
    license: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2048))
    source_license: Mapped[str | None] = mapped_column(String(255))
    contributed_by: Mapped[str | None] = mapped_column(String(100))
    pack_id: Mapped[str | None] = mapped_column(String(160))
    pack_version: Mapped[str | None] = mapped_column(String(64))
    provenance: Mapped[str | None] = mapped_column(String(64))
    verified_release_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("federation_verified_releases.id", ondelete="RESTRICT")
    )
    release_version: Mapped[str | None] = mapped_column(String(255))
    release_digest: Mapped[str | None] = mapped_column(String(64))
    equivalence_group_id: Mapped[str | None] = mapped_column(String(200))
    variant_id: Mapped[str | None] = mapped_column(String(200))
    nutrients_digest: Mapped[str | None] = mapped_column(String(64))
    conflict: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    variant_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class FoodOdbl(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "foods_odbl"
    __table_args__ = (
        CheckConstraint("source = 'openfoodfacts'", name="source_openfoodfacts"),
        CheckConstraint("database_license = 'ODbL-1.0'", name="database_license_odbl"),
        CheckConstraint("contents_license = 'DbCL-1.0'", name="contents_license_dbcl"),
    )

    barcode: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    product_name: Mapped[str] = mapped_column(String(500), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255))
    nutrients_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default="openfoodfacts")
    database_license: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="ODbL-1.0"
    )
    contents_license: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="DbCL-1.0"
    )
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    attribution_text: Mapped[str] = mapped_column(Text, nullable=False)


class FoodCustom(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "foods_custom"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    nutrients_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    portions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)


class Recipe(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "recipes"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_recipes_user_id_id"),
        CheckConstraint("yield_grams > 0", name="yield_grams_positive"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    yield_grams: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class RecipeIngredient(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "recipe_ingredients"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["user_id", "recipe_id"],
            ["recipes.user_id", "recipes.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"food_source_table IN ({INGREDIENT_SOURCE_TABLE_VALUES})",
            name="food_source_allowed",
        ),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint("grams > 0", name="grams_positive"),
        UniqueConstraint("recipe_id", "position", name="uq_recipe_ingredients_position"),
    )

    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    recipe_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    food_source_table: Mapped[str] = mapped_column(String(32), nullable=False)
    food_source_id: Mapped[UUID] = mapped_column(nullable=False)
    food_source_key: Mapped[str] = mapped_column(String(160), nullable=False)
    food_name: Mapped[str] = mapped_column(String(500), nullable=False)
    grams: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    computed_nutrients_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class LogEntry(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "log_entries"
    __table_args__ = (
        CheckConstraint(
            f"food_source_table IN ({LOG_SOURCE_TABLE_VALUES})", name="food_source_allowed"
        ),
        CheckConstraint("grams > 0", name="grams_positive"),
        CheckConstraint("quantity_amount > 0", name="quantity_amount_positive"),
        CheckConstraint(
            "quantity_unit IN ('g', 'ml', 'portion')", name="quantity_unit_allowed"
        ),
        CheckConstraint(
            "(quantity_unit = 'portion' AND portion_name IS NOT NULL) OR "
            "(quantity_unit <> 'portion' AND portion_name IS NULL)",
            name="portion_name_matches_unit",
        ),
        Index("ix_log_entries_user_id_logged_at", "user_id", "logged_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meal_slot: Mapped[str] = mapped_column(String(64), nullable=False)
    food_source_table: Mapped[str] = mapped_column(String(32), nullable=False)
    food_source_id: Mapped[UUID] = mapped_column(nullable=False)
    food_source_key: Mapped[str] = mapped_column(String(160), nullable=False)
    food_name: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity_amount: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    quantity_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    portion_name: Mapped[str | None] = mapped_column(String(80))
    grams: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    computed_nutrients_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class BodyMetric(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "body_metrics"
    __table_args__ = (
        CheckConstraint(
            f"metric_type IN ({BODY_METRIC_TYPE_VALUES})",
            name="metric_type_allowed",
        ),
        CheckConstraint(f"unit IN ({BODY_METRIC_UNIT_VALUES})", name="unit_allowed"),
        CheckConstraint(
            "(metric_type = 'body_weight' AND unit IN ('kg', 'lb')) OR "
            "(metric_type = 'body_fat_percentage' AND unit = 'percent') OR "
            "(metric_type IN ('height', 'waist_circumference', "
            "'hip_circumference', 'chest_circumference', 'neck_circumference', "
            "'upper_arm_circumference', 'thigh_circumference') "
            "AND unit IN ('cm', 'in'))",
            name="type_unit_valid",
        ),
        CheckConstraint("value > 0 AND value <= 1000000", name="value_bounded"),
        CheckConstraint(
            "recorded_at >= TIMESTAMPTZ '0001-01-01 00:00:00.000001+00' AND "
            "recorded_at <= TIMESTAMPTZ '9999-12-31 23:59:59.999998+00'",
            name="recorded_at_supported",
        ),
        Index("ix_body_metrics_user_id_recorded_at", "user_id", "recorded_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)


class Workout(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workouts"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_workouts_user_id_id"),
        CheckConstraint(
            "performed_at >= TIMESTAMPTZ '0001-01-01 00:00:00.000001+00' AND "
            "performed_at <= TIMESTAMPTZ '9999-12-31 23:59:59.999998+00'",
            name="performed_at_supported",
        ),
        CheckConstraint("notes IS NULL OR length(notes) <= 5000", name="notes_bounded"),
        Index("ix_workouts_user_id_performed_at", "user_id", "performed_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class Exercise(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "exercises"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_exercises_source_id"),
        CheckConstraint("length(slug) BETWEEN 1 AND 160", name="slug_bounded"),
        CheckConstraint("length(name) BETWEEN 1 AND 255", name="name_bounded"),
        CheckConstraint("slug !~ '[<>[:cntrl:]]'", name="slug_plain"),
        CheckConstraint("name !~ '[<>[:cntrl:]]'", name="name_plain"),
        CheckConstraint("length(search_text) <= 50000", name="search_text_bounded"),
        CheckConstraint(
            "source_updated_at IS NULL OR ("
            "source_updated_at >= TIMESTAMPTZ '0001-01-01 00:00:00.000001+00' AND "
            "source_updated_at <= TIMESTAMPTZ '9999-12-31 23:59:59.999998+00')",
            name="source_updated_at_supported",
        ),
        CheckConstraint("jsonb_typeof(muscle_groups) = 'array'", name="muscles_array"),
        CheckConstraint("jsonb_typeof(equipment) = 'array'", name="equipment_array"),
        CheckConstraint("jsonb_typeof(translations_json) = 'array'", name="translations_array"),
        CheckConstraint(
            "jsonb_typeof(translation_attribution_json) = 'array'",
            name="translation_attribution_array",
        ),
        CheckConstraint(
            "NOT jsonb_path_exists(muscle_groups, "
            "'$[*] ? (@.type() != \"string\")')",
            name="muscles_strings",
        ),
        CheckConstraint("muscle_groups::text !~ '[<>]'", name="muscles_plain"),
        CheckConstraint(
            "NOT jsonb_path_exists(equipment, '$[*] ? (@.type() != \"string\")')",
            name="equipment_strings",
        ),
        CheckConstraint("equipment::text !~ '[<>]'", name="equipment_plain"),
        CheckConstraint(
            "NOT jsonb_path_exists(translations_json, "
            "'$[*] ? (@.type() != \"object\")')",
            name="translations_objects",
        ),
        CheckConstraint(
            "NOT jsonb_path_exists(translation_attribution_json, "
            "'$[*] ? (@.type() != \"object\")')",
            name="translation_attribution_objects",
        ),
        CheckConstraint(
            "source <> 'wger' OR (license_spdx = 'CC-BY-SA-3.0' "
            "AND license_url = 'https://creativecommons.org/licenses/by-sa/3.0/')",
            name="wger_license_allowed",
        ),
        CheckConstraint(
            "source_url ~ '^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?"
            "(:([1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|"
            "65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5]))?"
            "(/[^[:space:]<>\"''\\]*)?$'",
            name="source_url_http",
        ),
        CheckConstraint(
            "derivative_source_url IS NULL OR derivative_source_url ~ '^https?://"
            "[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?"
            "(:([1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|"
            "65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5]))?"
            "(/[^[:space:]<>\"''\\]*)?$'",
            name="derivative_source_url_http",
        ),
        CheckConstraint(
            "author_url IS NULL OR author_url ~ '^https?://"
            "[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?"
            "(:([1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|"
            "65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5]))?"
            "(/[^[:space:]<>\"''\\]*)?$'",
            name="author_url_http",
        ),
        Index("ix_exercises_muscle_groups_gin", "muscle_groups", postgresql_using="gin"),
        Index("ix_exercises_equipment_gin", "equipment", postgresql_using="gin"),
        Index(
            "ix_exercises_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index(
            "ix_exercises_search_tsv",
            text(
                "to_tsvector('simple'::regconfig, "
                "(name::text || ' '::text) || search_text)"
            ),
            postgresql_using="gin",
        ),
    )

    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    muscle_groups: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    equipment: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    search_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    derivative_source_url: Mapped[str | None] = mapped_column(String(2048))
    license_spdx: Mapped[str] = mapped_column(String(64), nullable=False)
    license_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    author: Mapped[str | None] = mapped_column(String(255))
    author_url: Mapped[str | None] = mapped_column(String(2048))
    attribution_text: Mapped[str] = mapped_column(Text, nullable=False)
    translations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    translation_attribution_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkoutSet(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workout_sets"
    __table_args__ = (
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["user_id", "workout_id"],
            ["workouts.user_id", "workouts.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("set_index >= 0", name="set_index_nonnegative"),
        CheckConstraint("set_index < 500", name="set_index_bounded"),
        CheckConstraint("reps > 0", name="reps_positive"),
        CheckConstraint("reps <= 100000", name="reps_bounded"),
        CheckConstraint("load_value IS NULL OR load_value >= 0", name="load_value_nonnegative"),
        CheckConstraint(
            "load_value IS NULL OR load_value <= 1000000",
            name="load_value_bounded",
        ),
        CheckConstraint(f"load_unit IN ({LOAD_UNIT_VALUES})", name="load_unit_allowed"),
        CheckConstraint(
            "(load_unit IN ('kg', 'lb', 'machine_units') "
            "AND load_value IS NOT NULL) OR "
            "(load_unit IN ('bodyweight', 'band') AND load_value IS NULL) OR "
            "(load_unit = 'rpe_only' AND load_value IS NOT NULL "
            "AND load_value BETWEEN 1 AND 10)",
            name="load_contract_valid",
        ),
        UniqueConstraint("workout_id", "set_index", name="uq_workout_sets_position"),
    )

    user_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    workout_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    exercise_id: Mapped[UUID] = mapped_column(
        ForeignKey("exercises.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    set_index: Mapped[int] = mapped_column(Integer, nullable=False)
    reps: Mapped[int] = mapped_column(Integer, nullable=False)
    load_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    load_unit: Mapped[str] = mapped_column(String(32), nullable=False)


class Target(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "targets"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_type: Mapped[str] = mapped_column(String(64), nullable=False)
    kcal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    protein_g: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    carb_g: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    fat_g: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    active_from: Mapped[date] = mapped_column(Date, nullable=False)
    active_until: Mapped[date | None] = mapped_column(Date)
    below_floor_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    safety_review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    safety_floor_kcal: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        server_default=str(DEFAULT_TARGET_KCAL_FLOOR),
    )

    __table_args__ = (
        CheckConstraint(
            f"day_type IN ({TARGET_DAY_TYPE_VALUES})", name="day_type_allowed"
        ),
        CheckConstraint(f"kcal >= 0 AND kcal <= {MAX_KCAL}", name="kcal_bounded"),
        CheckConstraint(
            f"protein_g >= 0 AND protein_g <= {MAX_MACRO_GRAMS}",
            name="protein_bounded",
        ),
        CheckConstraint(
            f"carb_g >= 0 AND carb_g <= {MAX_MACRO_GRAMS}", name="carb_bounded"
        ),
        CheckConstraint(
            f"fat_g >= 0 AND fat_g <= {MAX_MACRO_GRAMS}", name="fat_bounded"
        ),
        CheckConstraint("safety_floor_kcal > 0", name="safety_floor_positive"),
        CheckConstraint(
            "(safety_review_required AND NOT below_floor_confirmed) OR "
            "(NOT safety_review_required AND "
            "((kcal >= safety_floor_kcal AND NOT below_floor_confirmed) OR "
            "(kcal < safety_floor_kcal AND below_floor_confirmed)))",
            name="safety_state_valid",
        ),
        CheckConstraint(
            "active_until IS NULL OR active_until >= active_from",
            name="active_range_ordered",
        ),
        UniqueConstraint("user_id", "day_type", "active_from", name="uq_targets_schedule"),
        ExcludeConstraint(
            ("user_id", "="),
            ("day_type", "="),
            (func.daterange(active_from, active_until, "[]"), "&&"),
            name="excl_targets_active_range",
            using="gist",
        ),
    )
