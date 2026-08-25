from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, cast

from sqlalchemy import MetaData, Table
from sqlalchemy.orm import DeclarativeBase

from opennosh_api.contributions.models import ContributionDraft, ContributionDraftOperation
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
    PublicationStep,
)

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
    PublicationIntent,
    PublicationStep,
    DurableAcknowledgement,
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
    "Exercise",
    "FoodCommunity",
    "FoodCustom",
    "FoodOdbl",
    "FoodReference",
    "FoodSearchSnapshot",
    "FoodSearchSnapshotItem",
    "LogEntry",
    "REGISTERED_MODELS",
    "PublicationIntent",
    "PublicationStep",
    "Recipe",
    "RecipeIngredient",
    "TABLE_MODEL_OWNERS",
    "Target",
    "User",
    "Workout",
    "WorkoutSet",
    "metadata",
]
