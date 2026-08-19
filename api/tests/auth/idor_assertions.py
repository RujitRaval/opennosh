from collections.abc import Mapping
from typing import TypeVar
from uuid import UUID

from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.auth.tenant import (
    delete_owned_resource,
    get_owned_resource,
    update_owned_resource,
)
from opennosh_api.models import Base
from sqlalchemy.ext.asyncio import AsyncSession

TenantModel = TypeVar("TenantModel", bound=Base)


async def assert_cross_user_access_is_denied(
    session: AsyncSession,
    model_type: type[TenantModel],
    *,
    resource_id: UUID,
    attacker: CurrentSession,
    changes: Mapping[str, object],
) -> None:
    assert (
        await get_owned_resource(
            session,
            model_type,
            resource_id=resource_id,
            current=attacker,
        )
        is None
    )
    assert (
        await update_owned_resource(
            session,
            model_type,
            resource_id=resource_id,
            current=attacker,
            changes=changes,
        )
        is None
    )
    assert not await delete_owned_resource(
        session,
        model_type,
        resource_id=resource_id,
        current=attacker,
    )
