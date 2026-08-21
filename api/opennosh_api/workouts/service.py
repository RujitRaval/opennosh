from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.auth.tenant import delete_owned_resource
from opennosh_api.models import Exercise, LoadUnit, Workout, WorkoutSet
from opennosh_api.nutrition.models import deterministic_add, deterministic_multiply
from opennosh_api.workouts.constants import (
    MAX_WORKOUT_PERFORMED_AT,
    MAX_WORKOUT_SETS,
    WORKOUT_TREND_RANGE_DAYS_MAX,
)
from opennosh_api.workouts.schemas import (
    WorkoutCreate,
    WorkoutExerciseResponse,
    WorkoutListResponse,
    WorkoutResponse,
    WorkoutSetResponse,
    WorkoutSetWrite,
    WorkoutTrendPoint,
    WorkoutTrendResponse,
    WorkoutUpdate,
    WorkoutVolumeGroup,
    WorkoutVolumeResponse,
)

_VOLUME_UNITS = {LoadUnit.KG, LoadUnit.LB, LoadUnit.MACHINE_UNITS}


class WorkoutInputError(ValueError):
    """A safe, user-actionable workout validation error."""


class WorkoutNotFound(LookupError):
    """A workout or nested set is absent or inaccessible to the current owner."""


class ExerciseNotFound(LookupError):
    """An exercise reference is absent from the attributed catalogue."""


def utc_date_bounds(from_date: date, to_date: date) -> tuple[datetime, datetime | None]:
    if from_date > to_date:
        raise WorkoutInputError("from must be on or before to")
    try:
        start = datetime.combine(from_date, time.min, tzinfo=UTC)
        end = (
            None
            if to_date == date.max
            else datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=UTC)
        )
        return start, end
    except OverflowError as error:
        raise WorkoutInputError("date range is outside the supported UTC range") from error


def set_volume(*, reps: int, load_value: Decimal | None, load_unit: LoadUnit) -> Decimal | None:
    if load_unit not in _VOLUME_UNITS or load_value is None:
        return None
    return deterministic_multiply(Decimal(reps), load_value)


def _exercise_response(exercise: Exercise) -> WorkoutExerciseResponse:
    return WorkoutExerciseResponse(
        id=exercise.id,
        slug=exercise.slug,
        name=exercise.name,
        source=exercise.source,
        source_id=exercise.source_id,
        source_url=exercise.source_url,
        derivative_source_url=exercise.derivative_source_url,
        license_spdx=exercise.license_spdx,
        license_url=exercise.license_url,
        author=exercise.author,
        author_url=exercise.author_url,
        attribution_text=exercise.attribution_text,
        translation_attribution=exercise.translation_attribution_json,
    )


def _set_response(item: WorkoutSet, exercise: Exercise) -> WorkoutSetResponse:
    unit = LoadUnit(item.load_unit)
    return WorkoutSetResponse(
        id=item.id,
        position=item.set_index,
        exercise=_exercise_response(exercise),
        reps=item.reps,
        load_value=item.load_value,
        load_unit=unit,
        volume=set_volume(reps=item.reps, load_value=item.load_value, load_unit=unit),
    )


def _volume_groups(
    rows: list[tuple[WorkoutSet, Exercise]],
) -> list[WorkoutVolumeGroup]:
    totals: dict[tuple[UUID, LoadUnit], Decimal] = {}
    for item, exercise in rows:
        unit = LoadUnit(item.load_unit)
        volume = set_volume(reps=item.reps, load_value=item.load_value, load_unit=unit)
        if volume is None:
            continue
        key = (exercise.id, unit)
        totals[key] = deterministic_add(totals.get(key, Decimal(0)), volume)
    return [
        WorkoutVolumeGroup(exercise_id=exercise_id, load_unit=unit, volume=volume)
        for (exercise_id, unit), volume in sorted(
            totals.items(), key=lambda item: (str(item[0][0]), item[0][1].value)
        )
    ]


def workout_response(workout: Workout, rows: list[tuple[WorkoutSet, Exercise]]) -> WorkoutResponse:
    return WorkoutResponse(
        id=workout.id,
        performed_at=workout.performed_at.astimezone(UTC),
        notes=workout.notes,
        sets=[_set_response(item, exercise) for item, exercise in rows],
        volume_groups=_volume_groups(rows),
    )


async def _resolve_exercises(
    database: AsyncSession, exercise_ids: list[UUID]
) -> dict[UUID, Exercise]:
    unique_ids = set(exercise_ids)
    if not unique_ids:
        return {}
    exercises = list(
        (await database.scalars(select(Exercise).where(Exercise.id.in_(unique_ids)))).all()
    )
    resolved = {exercise.id: exercise for exercise in exercises}
    if resolved.keys() != unique_ids:
        raise ExerciseNotFound("Exercise not found")
    return resolved


def _new_set(
    *,
    current: CurrentSession,
    workout_id: UUID,
    position: int,
    payload: WorkoutSetWrite,
) -> WorkoutSet:
    return WorkoutSet(
        user_id=current.user_id,
        workout_id=workout_id,
        exercise_id=payload.exercise_id,
        set_index=position,
        reps=payload.reps,
        load_value=payload.load_value,
        load_unit=payload.load_unit.value,
    )


async def create_workout(
    database: AsyncSession, payload: WorkoutCreate, current: CurrentSession
) -> WorkoutResponse:
    exercises = await _resolve_exercises(database, [item.exercise_id for item in payload.sets])
    workout = Workout(
        user_id=current.user_id,
        performed_at=payload.performed_at,
        notes=payload.notes,
    )
    database.add(workout)
    await database.flush()
    items = [
        _new_set(
            current=current,
            workout_id=workout.id,
            position=position,
            payload=item,
        )
        for position, item in enumerate(payload.sets)
    ]
    database.add_all(items)
    await database.commit()
    return workout_response(workout, [(item, exercises[item.exercise_id]) for item in items])


async def _resolve_owned_workout(
    database: AsyncSession, workout_id: UUID, current: CurrentSession
) -> tuple[Workout, list[tuple[WorkoutSet, Exercise]]] | None:
    rows = (
        await database.execute(
            select(Workout, WorkoutSet, Exercise)
            .outerjoin(
                WorkoutSet,
                and_(
                    WorkoutSet.workout_id == Workout.id,
                    WorkoutSet.user_id == Workout.user_id,
                ),
            )
            .outerjoin(Exercise, Exercise.id == WorkoutSet.exercise_id)
            .where(Workout.id == workout_id, Workout.user_id == current.user_id)
            .order_by(WorkoutSet.set_index, WorkoutSet.id)
        )
    ).all()
    if not rows:
        return None
    workout = rows[0][0]
    items = [
        (item, exercise) for _, item, exercise in rows if item is not None and exercise is not None
    ]
    return workout, items


async def get_workout(
    database: AsyncSession, workout_id: UUID, current: CurrentSession
) -> WorkoutResponse | None:
    resolved = await _resolve_owned_workout(database, workout_id, current)
    return None if resolved is None else workout_response(*resolved)


async def list_workouts(
    database: AsyncSession,
    *,
    from_date: date,
    to_date: date,
    current: CurrentSession,
    limit: int,
    offset: int,
) -> WorkoutListResponse:
    start, end = utc_date_bounds(from_date, to_date)
    time_conditions = [
        Workout.performed_at >= start,
        (
            Workout.performed_at <= MAX_WORKOUT_PERFORMED_AT
            if end is None
            else Workout.performed_at < end
        ),
    ]
    page = (
        select(Workout.id)
        .where(Workout.user_id == current.user_id, *time_conditions)
        .order_by(Workout.performed_at.desc(), Workout.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .subquery()
    )
    rows = (
        await database.execute(
            select(Workout, WorkoutSet, Exercise)
            .join(page, Workout.id == page.c.id)
            .outerjoin(
                WorkoutSet,
                and_(
                    WorkoutSet.workout_id == Workout.id,
                    WorkoutSet.user_id == Workout.user_id,
                ),
            )
            .outerjoin(Exercise, Exercise.id == WorkoutSet.exercise_id)
            .order_by(
                Workout.performed_at.desc(),
                Workout.id.desc(),
                WorkoutSet.set_index,
                WorkoutSet.id,
            )
        )
    ).all()
    workouts: dict[UUID, Workout] = {}
    sets: dict[UUID, list[tuple[WorkoutSet, Exercise]]] = {}
    for workout, item, exercise in rows:
        workouts.setdefault(workout.id, workout)
        sets.setdefault(workout.id, [])
        if item is not None and exercise is not None:
            sets[workout.id].append((item, exercise))
    page_workouts = list(workouts.values())
    visible = page_workouts[:limit]
    return WorkoutListResponse(
        from_date=from_date,
        to_date=to_date,
        items=[workout_response(workout, sets[workout.id]) for workout in visible],
        limit=limit,
        offset=offset,
        has_more=len(page_workouts) > limit,
    )


async def workout_trends(
    database: AsyncSession,
    *,
    from_date: date,
    to_date: date,
    current: CurrentSession,
) -> WorkoutTrendResponse:
    start, end = utc_date_bounds(from_date, to_date)
    if (to_date - from_date).days + 1 > WORKOUT_TREND_RANGE_DAYS_MAX:
        raise WorkoutInputError(
            f"date range must contain at most {WORKOUT_TREND_RANGE_DAYS_MAX} days"
        )
    time_conditions = [
        Workout.performed_at >= start,
        (
            Workout.performed_at <= MAX_WORKOUT_PERFORMED_AT
            if end is None
            else Workout.performed_at < end
        ),
    ]
    utc_day = func.date(func.timezone("UTC", Workout.performed_at)).label("day")
    volume = func.sum(WorkoutSet.reps * WorkoutSet.load_value).label("volume")
    rows = (
        await database.execute(
            select(
                utc_day,
                Exercise.id,
                Exercise.name,
                WorkoutSet.load_unit,
                volume,
            )
            .join(
                WorkoutSet,
                and_(
                    WorkoutSet.workout_id == Workout.id,
                    WorkoutSet.user_id == Workout.user_id,
                ),
            )
            .join(Exercise, Exercise.id == WorkoutSet.exercise_id)
            .where(
                Workout.user_id == current.user_id,
                *time_conditions,
                WorkoutSet.load_unit.in_(unit.value for unit in _VOLUME_UNITS),
                WorkoutSet.load_value.is_not(None),
            )
            .group_by(utc_day, Exercise.id, Exercise.name, WorkoutSet.load_unit)
            .order_by(utc_day, Exercise.name, Exercise.id, WorkoutSet.load_unit)
        )
    ).all()
    return WorkoutTrendResponse(
        from_date=from_date,
        to_date=to_date,
        items=[
            WorkoutTrendPoint(
                day=day,
                exercise_id=exercise_id,
                exercise_name=exercise_name,
                load_unit=LoadUnit(load_unit),
                volume=group_volume,
            )
            for day, exercise_id, exercise_name, load_unit, group_volume in rows
        ],
    )


async def update_workout(
    database: AsyncSession,
    workout_id: UUID,
    payload: WorkoutUpdate,
    current: CurrentSession,
) -> WorkoutResponse | None:
    workout = await database.scalar(
        select(Workout)
        .where(Workout.id == workout_id, Workout.user_id == current.user_id)
        .with_for_update()
    )
    if workout is None:
        return None
    workout.performed_at = payload.performed_at
    workout.notes = payload.notes
    await database.commit()
    resolved = await _resolve_owned_workout(database, workout.id, current)
    if resolved is None:  # pragma: no cover - the locked row remains present
        raise RuntimeError("Updated workout disappeared")
    return workout_response(*resolved)


async def delete_workout(database: AsyncSession, workout_id: UUID, current: CurrentSession) -> bool:
    deleted = await delete_owned_resource(
        database, Workout, resource_id=workout_id, current=current
    )
    if deleted:
        await database.commit()
    return deleted


async def add_workout_set(
    database: AsyncSession,
    workout_id: UUID,
    payload: WorkoutSetWrite,
    current: CurrentSession,
) -> WorkoutResponse:
    workout = await database.scalar(
        select(Workout)
        .where(Workout.id == workout_id, Workout.user_id == current.user_id)
        .with_for_update()
    )
    if workout is None:
        raise WorkoutNotFound("Workout or set not found")
    await _resolve_exercises(database, [payload.exercise_id])
    last_position = await database.scalar(
        select(func.max(WorkoutSet.set_index)).where(
            WorkoutSet.workout_id == workout.id,
            WorkoutSet.user_id == current.user_id,
        )
    )
    position = 0 if last_position is None else int(last_position) + 1
    if position >= MAX_WORKOUT_SETS:
        raise WorkoutInputError(f"A workout may contain at most {MAX_WORKOUT_SETS} sets")
    item = _new_set(
        current=current,
        workout_id=workout.id,
        position=position,
        payload=payload,
    )
    database.add(item)
    await database.commit()
    resolved = await _resolve_owned_workout(database, workout.id, current)
    if resolved is None:  # pragma: no cover - the locked row remains present
        raise RuntimeError("Updated workout disappeared")
    return workout_response(*resolved)


async def update_workout_set(
    database: AsyncSession,
    workout_id: UUID,
    set_id: UUID,
    payload: WorkoutSetWrite,
    current: CurrentSession,
) -> WorkoutResponse:
    workout = await database.scalar(
        select(Workout)
        .where(Workout.id == workout_id, Workout.user_id == current.user_id)
        .with_for_update()
    )
    if workout is None:
        raise WorkoutNotFound("Workout or set not found")
    item = await database.scalar(
        select(WorkoutSet).where(
            WorkoutSet.id == set_id,
            WorkoutSet.workout_id == workout.id,
            WorkoutSet.user_id == current.user_id,
        )
    )
    if item is None:
        raise WorkoutNotFound("Workout or set not found")
    await _resolve_exercises(database, [payload.exercise_id])
    item.exercise_id = payload.exercise_id
    item.reps = payload.reps
    item.load_value = payload.load_value
    item.load_unit = payload.load_unit.value
    await database.commit()
    resolved = await _resolve_owned_workout(database, workout.id, current)
    if resolved is None:  # pragma: no cover - the locked row remains present
        raise RuntimeError("Updated workout disappeared")
    return workout_response(*resolved)


async def delete_workout_set(
    database: AsyncSession,
    workout_id: UUID,
    set_id: UUID,
    current: CurrentSession,
) -> WorkoutResponse:
    workout = await database.scalar(
        select(Workout)
        .where(Workout.id == workout_id, Workout.user_id == current.user_id)
        .with_for_update()
    )
    if workout is None:
        raise WorkoutNotFound("Workout or set not found")
    item = await database.scalar(
        select(WorkoutSet).where(
            WorkoutSet.id == set_id,
            WorkoutSet.workout_id == workout.id,
            WorkoutSet.user_id == current.user_id,
        )
    )
    if item is None:
        raise WorkoutNotFound("Workout or set not found")
    removed_position = item.set_index
    await database.execute(
        delete(WorkoutSet).where(
            WorkoutSet.id == item.id,
            WorkoutSet.user_id == current.user_id,
        )
    )
    later_sets = (
        await database.execute(
            select(WorkoutSet.id, WorkoutSet.set_index)
            .where(
                WorkoutSet.workout_id == workout.id,
                WorkoutSet.user_id == current.user_id,
                WorkoutSet.set_index > removed_position,
            )
            .order_by(WorkoutSet.set_index)
            .with_for_update()
        )
    ).all()
    # The position constraint is immediate, so compact the now-free slots in
    # ascending order instead of relying on PostgreSQL's bulk-update row order.
    for later_id, later_position in later_sets:
        await database.execute(
            update(WorkoutSet)
            .where(
                WorkoutSet.id == later_id,
                WorkoutSet.workout_id == workout.id,
                WorkoutSet.user_id == current.user_id,
            )
            .values(set_index=later_position - 1)
        )
    await database.commit()
    resolved = await _resolve_owned_workout(database, workout.id, current)
    if resolved is None:  # pragma: no cover - the locked row remains present
        raise RuntimeError("Updated workout disappeared")
    return workout_response(*resolved)


async def workout_volume(
    database: AsyncSession,
    *,
    from_date: date,
    to_date: date,
    exercise_id: UUID,
    requested_unit: LoadUnit | None,
    current: CurrentSession,
) -> WorkoutVolumeResponse:
    start, end = utc_date_bounds(from_date, to_date)
    if requested_unit is not None and requested_unit not in _VOLUME_UNITS:
        raise WorkoutInputError(f"Volume is not defined for {requested_unit.value}")
    conditions = [
        Workout.user_id == current.user_id,
        WorkoutSet.user_id == current.user_id,
        WorkoutSet.exercise_id == exercise_id,
        WorkoutSet.load_unit.in_(tuple(unit.value for unit in _VOLUME_UNITS)),
        Workout.performed_at >= start,
        (
            Workout.performed_at <= MAX_WORKOUT_PERFORMED_AT
            if end is None
            else Workout.performed_at < end
        ),
    ]
    if requested_unit is not None:
        conditions.append(WorkoutSet.load_unit == requested_unit.value)
    rows = (
        await database.execute(
            select(
                WorkoutSet.load_unit,
                func.sum(WorkoutSet.reps * WorkoutSet.load_value),
                func.count(),
            )
            .join(
                Workout,
                and_(
                    Workout.id == WorkoutSet.workout_id,
                    Workout.user_id == WorkoutSet.user_id,
                ),
            )
            .where(*conditions)
            .group_by(WorkoutSet.load_unit)
        )
    ).all()
    if requested_unit is not None:
        selected_unit: LoadUnit | None = requested_unit
    else:
        if len(rows) > 1:
            raise WorkoutInputError(
                "Cannot aggregate workout volume across incompatible load units; "
                "request one load_unit"
            )
        selected_unit = None if not rows else LoadUnit(rows[0][0])
    total = None if not rows else rows[0][1]
    qualifying_sets = 0 if not rows else int(rows[0][2])
    return WorkoutVolumeResponse(
        from_date=from_date,
        to_date=to_date,
        exercise_id=exercise_id,
        load_unit=selected_unit,
        volume=total,
        qualifying_sets=qualifying_sets,
    )
