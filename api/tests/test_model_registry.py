import pytest
from opennosh_api.models import User
from opennosh_api.models.registry import (
    REGISTERED_MODELS,
    TABLE_MODEL_OWNERS,
    _build_table_model_owners,
    metadata,
)

EXPECTED_TABLE_OWNERS = {
    "auth_rate_limits": "opennosh_api.models.auth.AuthRateLimit",
    "auth_sessions": "opennosh_api.models.auth.AuthSession",
    "body_metrics": "opennosh_api.models.tables.BodyMetric",
    "contribution_draft_operations": (
        "opennosh_api.contributions.models.ContributionDraftOperation"
    ),
    "contribution_drafts": "opennosh_api.contributions.models.ContributionDraft",
    "exercises": "opennosh_api.models.tables.Exercise",
    "food_search_snapshot_items": "opennosh_api.models.tables.FoodSearchSnapshotItem",
    "food_search_snapshots": "opennosh_api.models.tables.FoodSearchSnapshot",
    "foods_community": "opennosh_api.models.tables.FoodCommunity",
    "foods_custom": "opennosh_api.models.tables.FoodCustom",
    "foods_odbl": "opennosh_api.models.tables.FoodOdbl",
    "foods_reference": "opennosh_api.models.tables.FoodReference",
    "log_entries": "opennosh_api.models.tables.LogEntry",
    "recipe_ingredients": "opennosh_api.models.tables.RecipeIngredient",
    "recipes": "opennosh_api.models.tables.Recipe",
    "targets": "opennosh_api.models.tables.Target",
    "users": "opennosh_api.models.auth.User",
    "workout_sets": "opennosh_api.models.tables.WorkoutSet",
    "workouts": "opennosh_api.models.tables.Workout",
}


def test_registry_owns_every_metadata_table_exactly_once() -> None:
    owners = {
        table_name: f"{model.__module__}.{model.__name__}"
        for table_name, model in TABLE_MODEL_OWNERS.items()
    }

    assert owners == EXPECTED_TABLE_OWNERS
    assert set(metadata.tables) == set(EXPECTED_TABLE_OWNERS)
    assert len(REGISTERED_MODELS) == len(EXPECTED_TABLE_OWNERS)
    assert len({model.__table__.name for model in REGISTERED_MODELS}) == len(
        REGISTERED_MODELS
    )


def test_registry_import_order_is_deterministic() -> None:
    assert tuple(model.__table__.name for model in REGISTERED_MODELS) == (
        "users",
        "auth_sessions",
        "auth_rate_limits",
        "foods_reference",
        "foods_community",
        "food_search_snapshots",
        "food_search_snapshot_items",
        "foods_odbl",
        "foods_custom",
        "recipes",
        "recipe_ingredients",
        "log_entries",
        "body_metrics",
        "workouts",
        "exercises",
        "workout_sets",
        "targets",
        "contribution_drafts",
        "contribution_draft_operations",
    )


def test_registry_rejects_duplicate_table_ownership() -> None:
    with pytest.raises(RuntimeError, match="has multiple model owners"):
        _build_table_model_owners((User, User))


def test_registry_rejects_incomplete_registration() -> None:
    with pytest.raises(
        RuntimeError,
        match="unregistered=\\['contribution_draft_operations'\\], missing_from_metadata=\\[\\]",
    ):
        _build_table_model_owners(REGISTERED_MODELS[:-1])
