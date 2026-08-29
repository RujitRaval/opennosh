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
    "food_search_snapshots",
    "food_search_snapshot_items",
    "recipes",
    "recipe_ingredients",
    "log_entries",
    "body_metrics",
    "workouts",
    "workout_sets",
    "exercises",
    "targets",
    "contribution_drafts",
    "contribution_draft_operations",
    "evidence_manifests",
    "evidence_durable_acknowledgements",
    "evidence_removal_tombstones",
    "federation_invitations",
    "federation_maintainers",
    "federation_role_keys",
    "federation_audit_events",
    "governance_role_assignments",
    "governance_recusals",
    "governance_decisions",
    "governance_merge_authorizations",
    "governance_publication_interventions",
    "governance_publication_pauses",
    "publication_intents",
    "publication_steps",
    "publication_durable_acknowledgements",
    "publication_receipts",
    "accepted_events",
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
    "contribution_drafts",
}


def test_metadata_contains_the_complete_license_separated_schema() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert {
        "foods_reference",
        "foods_community",
        "foods_odbl",
        "foods_custom",
    }.issubset(Base.metadata.tables)


def test_governance_intervention_audit_fields_are_all_or_none() -> None:
    role_checks = {
        constraint.name
        for constraint in Base.metadata.tables["governance_role_assignments"].constraints
        if isinstance(constraint, CheckConstraint)
    }
    pause_checks = {
        constraint.name
        for constraint in Base.metadata.tables["governance_publication_pauses"].constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_governance_role_assignments_revocation_audit_complete" in role_checks
    assert "ck_governance_publication_pauses_resume_audit_complete" in pause_checks


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


def test_exercise_search_indexes_are_declared_in_model_metadata() -> None:
    exercise_indexes = {index.name for index in Base.metadata.tables["exercises"].indexes}

    assert {
        "ix_exercises_search_tsv",
        "ix_exercises_name_trgm",
        "ix_exercises_muscle_groups_gin",
        "ix_exercises_equipment_gin",
    }.issubset(exercise_indexes)

    check_names = {
        constraint.name
        for constraint in Base.metadata.tables["exercises"].constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_exercises_source_updated_at_supported" in check_names


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


def test_body_metrics_enforce_explicit_type_unit_and_value_contract() -> None:
    table = Base.metadata.tables["body_metrics"]
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "ck_body_metrics_metric_type_allowed",
        "ck_body_metrics_unit_allowed",
        "ck_body_metrics_type_unit_valid",
        "ck_body_metrics_value_bounded",
        "ck_body_metrics_recorded_at_supported",
    }.issubset(check_names)


def test_workouts_enforce_order_load_and_timestamp_contracts() -> None:
    workout_checks = {
        constraint.name
        for constraint in Base.metadata.tables["workouts"].constraints
        if isinstance(constraint, CheckConstraint)
    }
    set_checks = {
        constraint.name
        for constraint in Base.metadata.tables["workout_sets"].constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "ck_workouts_performed_at_supported",
        "ck_workouts_notes_bounded",
    }.issubset(workout_checks)
    assert {
        "ck_workout_sets_set_index_bounded",
        "ck_workout_sets_reps_bounded",
        "ck_workout_sets_load_value_bounded",
        "ck_workout_sets_load_unit_allowed",
        "ck_workout_sets_load_contract_valid",
    }.issubset(set_checks)


def test_exercises_enforce_attribution_json_urls_and_search_indexes() -> None:
    table = Base.metadata.tables["exercises"]
    assert {
        "search_text",
        "translations_json",
        "translation_attribution_json",
        "source_updated_at",
    }.issubset(table.columns.keys())
    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_exercises_wger_license_allowed",
        "ck_exercises_source_url_http",
        "ck_exercises_author_url_http",
        "ck_exercises_translations_array",
        "ck_exercises_translation_attribution_array",
        "ck_exercises_muscles_strings",
        "ck_exercises_muscles_plain",
        "ck_exercises_equipment_strings",
        "ck_exercises_equipment_plain",
        "ck_exercises_translations_objects",
        "ck_exercises_translation_attribution_objects",
        "ck_exercises_slug_plain",
        "ck_exercises_name_plain",
    }.issubset(check_names)
    assert {
        "ix_exercises_search_tsv",
        "ix_exercises_muscle_groups_gin",
        "ix_exercises_equipment_gin",
    }.issubset({index.name for index in table.indexes})
