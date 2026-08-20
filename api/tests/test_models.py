from opennosh_api.models import Base
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql import ExcludeConstraint

EXPECTED_TABLES = {
    "auth_rate_limits",
    "auth_sessions",
    "users",
    "foods_reference",
    "foods_community",
    "foods_odbl",
    "foods_custom",
    "recipes",
    "recipe_ingredients",
    "log_entries",
    "body_metrics",
    "workouts",
    "workout_sets",
    "exercises",
    "targets",
}

OWNER_TABLES = {
    "auth_sessions",
    "foods_custom",
    "recipes",
    "recipe_ingredients",
    "log_entries",
    "body_metrics",
    "workouts",
    "workout_sets",
    "targets",
}


def test_metadata_contains_the_complete_license_separated_schema() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert {
        "foods_reference",
        "foods_community",
        "foods_odbl",
        "foods_custom",
    }.issubset(Base.metadata.tables)


def test_redistributable_stores_require_license_provenance() -> None:
    required_columns = {
        "foods_reference": {"source", "license"},
        "foods_community": {
            "provenance",
            "source_uri",
            "source_license",
            "pack_license",
            "contributed_by",
        },
        "foods_odbl": {
            "source",
            "source_url",
            "database_license",
            "contents_license",
            "attribution_text",
        },
        "exercises": {
            "source",
            "source_id",
            "source_url",
            "license_spdx",
            "license_url",
            "attribution_text",
        },
    }

    for table_name, column_names in required_columns.items():
        table = Base.metadata.tables[table_name]
        assert column_names.issubset(table.columns.keys())
        nullable_columns = {"source_uri"} if table_name == "foods_community" else set()
        non_nullable_columns = column_names - nullable_columns
        assert all(not table.columns[column_name].nullable for column_name in non_nullable_columns)


def test_community_food_pack_lookups_are_indexed() -> None:
    table = Base.metadata.tables["foods_community"]
    indexed_columns = {column.name for index in table.indexes for column in index.columns}

    assert "pack_id" in indexed_columns


def test_food_search_indexes_are_declared_in_model_metadata() -> None:
    reference_indexes = {index.name for index in Base.metadata.tables["foods_reference"].indexes}
    community_indexes = {index.name for index in Base.metadata.tables["foods_community"].indexes}

    assert {
        "ix_foods_reference_search_tsv",
        "ix_foods_reference_description_trgm",
    }.issubset(reference_indexes)
    assert {
        "ix_foods_community_search_tsv",
        "ix_foods_community_slug_trgm",
        "ix_foods_community_name_trgm",
        "ix_foods_community_name_local_trgm",
    }.issubset(community_indexes)


def test_every_user_owned_table_has_an_indexed_non_null_owner_foreign_key() -> None:
    for table_name in OWNER_TABLES:
        table = Base.metadata.tables[table_name]
        owner = table.columns["user_id"]
        indexed_columns = {column.name for index in table.indexes for column in index.columns}
        foreign_key_targets = {foreign_key.target_fullname for foreign_key in owner.foreign_keys}

        assert not owner.nullable
        assert "user_id" in indexed_columns
        assert "users.id" in foreign_key_targets


def test_polymorphic_food_references_are_allowlisted() -> None:
    for table_name in ("recipe_ingredients", "log_entries"):
        table = Base.metadata.tables[table_name]
        constraints = {
            str(constraint.sqltext)
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        assert any("foods_reference" in constraint for constraint in constraints)
        assert {"food_source_table", "food_source_id"}.issubset(table.columns.keys())


def test_log_entries_preserve_source_identity_and_original_quantity() -> None:
    table = Base.metadata.tables["log_entries"]

    assert {
        "food_source_key",
        "food_name",
        "quantity_amount",
        "quantity_unit",
        "portion_name",
        "computed_nutrients_json",
    }.issubset(table.columns.keys())
    constraints = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_log_entries_quantity_amount_positive",
        "ck_log_entries_quantity_unit_allowed",
        "ck_log_entries_portion_name_matches_unit",
    }.issubset(constraints)


def test_recipe_ingredients_preserve_order_identity_and_nutrient_snapshots() -> None:
    table = Base.metadata.tables["recipe_ingredients"]

    assert {
        "position",
        "food_source_key",
        "food_name",
        "grams",
        "computed_nutrients_json",
    }.issubset(table.columns.keys())
    constraints = {
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert any("position >= 0" in constraint for constraint in constraints)
    assert all("recipes" not in constraint for constraint in constraints)

    log_constraints = {
        str(constraint.sqltext)
        for constraint in Base.metadata.tables["log_entries"].constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert any("recipes" in constraint for constraint in log_constraints)


def test_targets_enforce_safe_non_overlapping_day_type_ranges() -> None:
    table = Base.metadata.tables["targets"]

    assert {
        "active_until",
        "below_floor_confirmed",
        "safety_review_required",
        "safety_floor_kcal",
    }.issubset(table.columns.keys())
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_targets_day_type_allowed",
        "ck_targets_kcal_bounded",
        "ck_targets_safety_state_valid",
        "ck_targets_active_range_ordered",
    }.issubset(check_names)
    assert any(
        constraint.name == "excl_targets_active_range"
        for constraint in table.constraints
        if isinstance(constraint, ExcludeConstraint)
    )
