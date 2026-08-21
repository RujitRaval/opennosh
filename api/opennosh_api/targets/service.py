from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.models import Target, TargetDayType, User
from opennosh_api.targets.constants import TARGET_QUANTUM
from opennosh_api.targets.schemas import (
    TargetResponse,
    TargetScheduleResponse,
    TargetScheduleWrite,
)

SAFETY_COPY = "Targets are values you choose for yourself; opennosh does not prescribe them."


class TargetConfirmationRequired(ValueError):
    """A below-floor target was submitted without a deliberate confirmation."""


def below_floor_confirmation_copy(target_kcal_floor: Decimal) -> str:
    """Return the neutral, settings-level confirmation instruction."""
    floor = target_kcal_floor.quantize(TARGET_QUANTUM)
    return (
        f"This value is below the configured safety floor of {floor} kcal. "
        "Confirm this specific target in settings to save the value you entered."
    )


async def _lock_owner(database: AsyncSession, user_id: UUID) -> None:
    await database.scalar(select(User.id).where(User.id == user_id).with_for_update())


def _response(
    target: Target, *, target_kcal_floor: Decimal | None = None
) -> TargetResponse:
    safety_review_required = target.safety_review_required or (
        target_kcal_floor is not None
        and target.kcal < target_kcal_floor
        and not target.below_floor_confirmed
    )
    return TargetResponse(
        id=target.id,
        day_type=TargetDayType(target.day_type),
        kcal=target.kcal,
        protein_g=target.protein_g,
        carb_g=target.carb_g,
        fat_g=target.fat_g,
        active_from=target.active_from,
        active_until=target.active_until,
        below_floor_confirmed=target.below_floor_confirmed,
        safety_review_required=safety_review_required,
        safety_floor_kcal=target.safety_floor_kcal,
    )


def _schedule_response(
    targets: list[Target], *, target_kcal_floor: Decimal
) -> TargetScheduleResponse:
    return TargetScheduleResponse(
        items=[
            _response(target, target_kcal_floor=target_kcal_floor)
            for target in targets
        ],
        target_kcal_floor=target_kcal_floor,
        safety_copy=SAFETY_COPY,
    )


async def list_targets(
    database: AsyncSession,
    *,
    current: CurrentSession,
    target_kcal_floor: Decimal,
) -> TargetScheduleResponse:
    targets = list(
        (
            await database.scalars(
                select(Target)
                .where(Target.user_id == current.user_id)
                .order_by(Target.day_type, Target.active_from, Target.id)
            )
        ).all()
    )
    return _schedule_response(targets, target_kcal_floor=target_kcal_floor)


async def replace_targets(
    database: AsyncSession,
    payload: TargetScheduleWrite,
    *,
    current: CurrentSession,
    target_kcal_floor: Decimal,
) -> TargetScheduleResponse:
    for item in payload.items:
        if item.kcal < target_kcal_floor and not item.confirm_below_floor:
            raise TargetConfirmationRequired(below_floor_confirmation_copy(target_kcal_floor))

    await _lock_owner(database, current.user_id)
    await database.execute(delete(Target).where(Target.user_id == current.user_id))
    targets = [
        Target(
            user_id=current.user_id,
            day_type=item.day_type.value,
            kcal=item.kcal,
            protein_g=item.protein_g,
            carb_g=item.carb_g,
            fat_g=item.fat_g,
            active_from=item.active_from,
            active_until=item.active_until,
            below_floor_confirmed=(item.kcal < target_kcal_floor),
            safety_review_required=False,
            safety_floor_kcal=target_kcal_floor,
        )
        for item in payload.items
    ]
    database.add_all(targets)
    await database.commit()
    ordered = sorted(targets, key=lambda item: (item.day_type, item.active_from, item.id))
    return _schedule_response(ordered, target_kcal_floor=target_kcal_floor)


async def resolve_target(
    database: AsyncSession,
    *,
    day: date,
    day_type: TargetDayType,
    current: CurrentSession,
    target_kcal_floor: Decimal,
) -> TargetResponse | None:
    target = await database.scalar(
        select(Target).where(
            Target.user_id == current.user_id,
            Target.day_type == day_type.value,
            Target.active_from <= day,
            (Target.active_until.is_(None) | (Target.active_until >= day)),
            Target.safety_review_required.is_(False),
            (
                (Target.kcal >= target_kcal_floor)
                | Target.below_floor_confirmed.is_(True)
            ),
        )
    )
    return (
        None
        if target is None
        else _response(target, target_kcal_floor=target_kcal_floor)
    )
