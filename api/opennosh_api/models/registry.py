from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, cast

from sqlalchemy import MetaData, Table
from sqlalchemy.orm import DeclarativeBase

from opennosh_api.contributions.models import ContributionDraft, ContributionDraftOperation
from opennosh_api.evidence.models import (
    EvidenceDurableAcknowledgement,
    EvidenceManifestRecord,
    EvidenceRemovalTombstone,
    EvidenceUploadSession,
)
from opennosh_api.federation.models import (
    FederationAuditEvent,
    FederationInvitation,
    FederationMaintainer,
    FederationPackInstallationEvent,
    FederationProjectionActivation,
    FederationProjectionCheckpoint,
    FederationProjectionFood,
    FederationProjectionRelease,
    FederationRelease,
    FederationReleaseStatusEvent,
    FederationRoleKey,
    FederationVerifiedRelease,
)
from opennosh_api.governance.models import (
    GovernanceAppeal,
    GovernanceDecision,
    GovernanceDispute,
    GovernanceMergeAuthorization,
    GovernancePublicationIntervention,
    GovernancePublicationPause,
    GovernanceRecusal,
    GovernanceReviewCase,
    GovernanceReviewEvent,
    GovernanceReviewPrivateNote,
    GovernanceRoleAssignment,
)
from opennosh_api.impact.models import ImpactSnapshot
from opennosh_api.missions.models import (
    MissionContributionBinding,
    MissionDefinition,
    MissionLifecycleEvent,
    MissionProgressActivation,
    MissionProgressCheckpoint,
    MissionProgressRecord,
)
from opennosh_api.models.auth import AuthRateLimit, AuthSession, User
from opennosh_api.models.base import Base
from opennosh_api.models.tables import (
    BodyMetric,
    Exercise,
    FoodCommunity,
    FoodCustom,
    FoodOdbl,
    FoodReference,
    FoodSearchSnapshot,
    FoodSearchSnapshotItem,
    LogEntry,
    Recipe,
    RecipeIngredient,
    Target,
    Workout,
    WorkoutSet,
)
from opennosh_api.publication.models import (
    AcceptedEvent,
    DurableAcknowledgement,
    PublicationIntent,
    PublicationReceiptRecord,
    PublicationStep,
)
from opennosh_api.reuse.models import ReuseDeclaration, ReuseDeclarationEvent, ReuseDependency

ModelClass = type[DeclarativeBase]

# This tuple is the single registration boundary for model-owning modules. New or
# relocated tables must be added here before Alembic can see them.
REGISTERED_MODELS: Final[tuple[ModelClass, ...]] = (
    User,
    AuthSession,
    AuthRateLimit,
    FoodReference,
    FoodCommunity,
    FoodSearchSnapshot,
    FoodSearchSnapshotItem,
    FoodOdbl,
    FoodCustom,
    Recipe,
    RecipeIngredient,
    LogEntry,
    BodyMetric,
    Workout,
    Exercise,
    WorkoutSet,
    Target,
    ContributionDraft,
    ContributionDraftOperation,
    EvidenceManifestRecord,
    EvidenceDurableAcknowledgement,
    EvidenceRemovalTombstone,
    EvidenceUploadSession,
    GovernanceRoleAssignment,
    GovernanceRecusal,
    GovernanceReviewCase,
    GovernanceReviewEvent,
    GovernanceReviewPrivateNote,
    GovernanceDecision,
    GovernanceDispute,
    GovernanceAppeal,
    GovernanceMergeAuthorization,
    GovernancePublicationPause,
    GovernancePublicationIntervention,
    MissionDefinition,
    MissionLifecycleEvent,
    MissionContributionBinding,
    MissionProgressCheckpoint,
    MissionProgressRecord,
    MissionProgressActivation,
    FederationInvitation,
    FederationMaintainer,
    FederationPackInstallationEvent,
    FederationRoleKey,
    FederationAuditEvent,
    FederationRelease,
    FederationVerifiedRelease,
    FederationReleaseStatusEvent,
    FederationProjectionCheckpoint,
    FederationProjectionRelease,
    FederationProjectionFood,
    FederationProjectionActivation,
    ReuseDeclaration,
    ReuseDeclarationEvent,
    ReuseDependency,
    ImpactSnapshot,
    PublicationIntent,
    PublicationStep,
    DurableAcknowledgement,
    PublicationReceiptRecord,
    AcceptedEvent,
)


def _build_table_model_owners(
    models: tuple[ModelClass, ...],
) -> Mapping[str, ModelClass]:
    owners: dict[str, ModelClass] = {}
    for model in models:
        table_name = cast(Table, model.__table__).name
        if table_name in owners:
            first_owner = owners[table_name]
            raise RuntimeError(
                f"Table {table_name!r} has multiple model owners: "
                f"{first_owner.__module__}.{first_owner.__name__} and "
                f"{model.__module__}.{model.__name__}"
            )
        owners[table_name] = model

    metadata_tables = set(Base.metadata.tables)
    registered_tables = set(owners)
    if metadata_tables != registered_tables:
        missing = sorted(metadata_tables - registered_tables)
        unknown = sorted(registered_tables - metadata_tables)
        raise RuntimeError(
            "Model registry and SQLAlchemy metadata disagree: "
            f"unregistered={missing}, missing_from_metadata={unknown}"
        )

    return MappingProxyType(owners)


TABLE_MODEL_OWNERS: Final[Mapping[str, ModelClass]] = _build_table_model_owners(REGISTERED_MODELS)
metadata: Final[MetaData] = Base.metadata

__all__ = [
    "AuthRateLimit",
    "AuthSession",
    "AcceptedEvent",
    "BodyMetric",
    "ContributionDraft",
    "ContributionDraftOperation",
    "DurableAcknowledgement",
    "EvidenceDurableAcknowledgement",
    "EvidenceManifestRecord",
    "EvidenceRemovalTombstone",
    "EvidenceUploadSession",
    "FederationAuditEvent",
    "FederationInvitation",
    "FederationMaintainer",
    "FederationPackInstallationEvent",
    "FederationProjectionActivation",
    "FederationProjectionCheckpoint",
    "FederationProjectionFood",
    "FederationProjectionRelease",
    "FederationRelease",
    "FederationReleaseStatusEvent",
    "FederationRoleKey",
    "FederationVerifiedRelease",
    "Exercise",
    "FoodCommunity",
    "FoodCustom",
    "FoodOdbl",
    "FoodReference",
    "FoodSearchSnapshot",
    "FoodSearchSnapshotItem",
    "GovernanceDecision",
    "GovernanceMergeAuthorization",
    "GovernancePublicationIntervention",
    "GovernancePublicationPause",
    "GovernanceRecusal",
    "GovernanceRoleAssignment",
    "ImpactSnapshot",
    "MissionContributionBinding",
    "MissionDefinition",
    "MissionLifecycleEvent",
    "MissionProgressActivation",
    "MissionProgressCheckpoint",
    "MissionProgressRecord",
    "LogEntry",
    "REGISTERED_MODELS",
    "PublicationIntent",
    "PublicationReceiptRecord",
    "PublicationStep",
    "ReuseDeclaration",
    "ReuseDeclarationEvent",
    "ReuseDependency",
    "Recipe",
    "RecipeIngredient",
    "TABLE_MODEL_OWNERS",
    "Target",
    "User",
    "Workout",
    "WorkoutSet",
    "metadata",
]
