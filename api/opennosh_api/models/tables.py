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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opennosh_api.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from opennosh_api.models.enums import FoodSourceTable, LoadUnit, Provenance

INGREDIENT_SOURCE_TABLE_VALUES = ", ".join(
    repr(value.value) for value in FoodSourceTable if value is not FoodSourceTable.RECIPE
)
LOG_SOURCE_TABLE_VALUES = ", ".join(repr(value.value) for value in FoodSourceTable)
PROVENANCE_VALUES = ", ".join(repr(value.value) for value in Provenance)
SOURCE_LICENSE_VALUES = "'contributor-original', 'CC0-1.0', 'public-domain'"
LOAD_UNIT_VALUES = ", ".join(repr(value.value) for value in LoadUnit)


class User(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "users"
    __table_args__ = (Index("uq_users_email_normalized", text("lower(email)"), unique=True),)

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class AuthSession(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "auth_sessions"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"
    __table_args__ = (CheckConstraint("attempt_count > 0", name="attempt_count_positive"),)

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


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
    __table_args__ = (Index("ix_body_metrics_user_id_recorded_at", "user_id", "recorded_at"),)

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
        Index("ix_workouts_user_id_performed_at", "user_id", "performed_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class Exercise(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "exercises"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_exercises_source_id"),)

    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    muscle_groups: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    equipment: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    derivative_source_url: Mapped[str | None] = mapped_column(String(2048))
    license_spdx: Mapped[str] = mapped_column(String(64), nullable=False)
    license_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    author: Mapped[str | None] = mapped_column(String(255))
    author_url: Mapped[str | None] = mapped_column(String(2048))
    attribution_text: Mapped[str] = mapped_column(Text, nullable=False)
    translation_attribution_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


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
        CheckConstraint("reps > 0", name="reps_positive"),
        CheckConstraint("load_value IS NULL OR load_value >= 0", name="load_value_nonnegative"),
        CheckConstraint(f"load_unit IN ({LOAD_UNIT_VALUES})", name="load_unit_allowed"),
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
    __table_args__ = (
        CheckConstraint("kcal >= 0", name="kcal_nonnegative"),
        CheckConstraint("protein_g >= 0", name="protein_nonnegative"),
        CheckConstraint("carb_g >= 0", name="carb_nonnegative"),
        CheckConstraint("fat_g >= 0", name="fat_nonnegative"),
        UniqueConstraint("user_id", "day_type", "active_from", name="uq_targets_schedule"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_type: Mapped[str] = mapped_column(String(64), nullable=False)
    kcal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    protein_g: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    carb_g: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    fat_g: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    active_from: Mapped[date] = mapped_column(Date, nullable=False)
