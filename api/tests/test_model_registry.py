import pytest
from opennosh_api.models import User
from opennosh_api.models.registry import (
    REGISTERED_MODELS,
    TABLE_MODEL_OWNERS,
    _build_table_model_owners,
    metadata,
)

EXPECTED_TABLE_OWNERS = {
    "accepted_events": "opennosh_api.publication.models.AcceptedEvent",
    "auth_rate_limits": "opennosh_api.models.auth.AuthRateLimit",
    "auth_sessions": "opennosh_api.models.auth.AuthSession",
    "body_metrics": "opennosh_api.models.tables.BodyMetric",
    "contribution_draft_operations": (
        "opennosh_api.contributions.models.ContributionDraftOperation"
    ),
    "contribution_drafts": "opennosh_api.contributions.models.ContributionDraft",
    "evidence_durable_acknowledgements": (
        "opennosh_api.evidence.models.EvidenceDurableAcknowledgement"
    ),
    "evidence_manifests": "opennosh_api.evidence.models.EvidenceManifestRecord",
    "evidence_removal_tombstones": ("opennosh_api.evidence.models.EvidenceRemovalTombstone"),
    "evidence_upload_sessions": "opennosh_api.evidence.models.EvidenceUploadSession",
    "federation_audit_events": "opennosh_api.federation.models.FederationAuditEvent",
    "federation_invitations": "opennosh_api.federation.models.FederationInvitation",
    "federation_maintainers": "opennosh_api.federation.models.FederationMaintainer",
    "federation_releases": "opennosh_api.federation.models.FederationRelease",
    "federation_role_keys": "opennosh_api.federation.models.FederationRoleKey",
    "governance_decisions": "opennosh_api.governance.models.GovernanceDecision",
    "governance_disputes": "opennosh_api.governance.models.GovernanceDispute",
    "governance_appeals": "opennosh_api.governance.models.GovernanceAppeal",
    "governance_merge_authorizations": (
        "opennosh_api.governance.models.GovernanceMergeAuthorization"
    ),
    "governance_publication_interventions": (
        "opennosh_api.governance.models.GovernancePublicationIntervention"
    ),
    "governance_publication_pauses": ("opennosh_api.governance.models.GovernancePublicationPause"),
    "governance_recusals": "opennosh_api.governance.models.GovernanceRecusal",
    "governance_review_cases": "opennosh_api.governance.models.GovernanceReviewCase",
    "governance_review_events": "opennosh_api.governance.models.GovernanceReviewEvent",
    "governance_review_private_notes": (
        "opennosh_api.governance.models.GovernanceReviewPrivateNote"
    ),
    "governance_role_assignments": ("opennosh_api.governance.models.GovernanceRoleAssignment"),
    "publication_durable_acknowledgements": (
        "opennosh_api.publication.models.DurableAcknowledgement"
    ),
    "publication_receipts": "opennosh_api.publication.models.PublicationReceiptRecord",
    "publication_intents": "opennosh_api.publication.models.PublicationIntent",
    "publication_steps": "opennosh_api.publication.models.PublicationStep",
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
    assert len({model.__table__.name for model in REGISTERED_MODELS}) == len(REGISTERED_MODELS)


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
        "evidence_manifests",
        "evidence_durable_acknowledgements",
        "evidence_removal_tombstones",
        "evidence_upload_sessions",
        "governance_role_assignments",
        "governance_recusals",
        "governance_review_cases",
        "governance_review_events",
        "governance_review_private_notes",
        "governance_decisions",
        "governance_disputes",
        "governance_appeals",
        "governance_merge_authorizations",
        "governance_publication_pauses",
        "governance_publication_interventions",
        "federation_invitations",
        "federation_maintainers",
        "federation_role_keys",
        "federation_audit_events",
        "federation_releases",
        "publication_intents",
        "publication_steps",
        "publication_durable_acknowledgements",
        "publication_receipts",
        "accepted_events",
    )


def test_registry_rejects_duplicate_table_ownership() -> None:
    with pytest.raises(RuntimeError, match="has multiple model owners"):
        _build_table_model_owners((User, User))


def test_registry_rejects_incomplete_registration() -> None:
    with pytest.raises(
        RuntimeError,
        match="unregistered=\\['accepted_events'\\], missing_from_metadata=\\[\\]",
    ):
        _build_table_model_owners(REGISTERED_MODELS[:-1])
