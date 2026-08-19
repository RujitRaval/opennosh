from collections.abc import Mapping
from typing import TypeVar, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.models import Base

TenantModel = TypeVar("TenantModel", bound=Base)
_PROTECTED_FIELDS = {"id", "user_id", "created_at"}


def _identity_columns(
    model_type: type[TenantModel],
) -> tuple[InstrumentedAttribute[UUID], InstrumentedAttribute[UUID]]:
    return (
        cast(InstrumentedAttribute[UUID], vars(model_type)["id"]),
        cast(InstrumentedAttribute[UUID], vars(model_type)["user_id"]),
    )


async def get_owned_resource(
    session: AsyncSession,
    model_type: type[TenantModel],
    *,
    resource_id: UUID,
    current: CurrentSession,
) -> TenantModel | None:
    id_column, owner_column = _identity_columns(model_type)
    return cast(
        TenantModel | None,
        await session.scalar(
            select(model_type).where(id_column == resource_id, owner_column == current.user_id)
        ),
    )


async def update_owned_resource(
    session: AsyncSession,
    model_type: type[TenantModel],
    *,
    resource_id: UUID,
    current: CurrentSession,
    changes: Mapping[str, object],
) -> TenantModel | None:
    resource = await get_owned_resource(
        session, model_type, resource_id=resource_id, current=current
    )
    if resource is None:
        return None
    for field, value in changes.items():
        if field in _PROTECTED_FIELDS:
            raise ValueError(f"Cannot update protected tenant field: {field}")
        setattr(resource, field, value)
    await session.flush()
    return resource


async def delete_owned_resource(
    session: AsyncSession,
    model_type: type[TenantModel],
    *,
    resource_id: UUID,
    current: CurrentSession,
) -> bool:
    resource = await get_owned_resource(
        session, model_type, resource_id=resource_id, current=current
    )
    if resource is None:
        return False
    await session.delete(resource)
    await session.flush()
    return True
