from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from opennosh_api.auth.dependencies import CurrentSession
from opennosh_api.auth.tenant import delete_owned_resource
from opennosh_api.body_metrics.constants import (
    BODY_METRIC_TREND_RANGE_DAYS_MAX,
    MAX_BODY_METRIC_RECORDED_AT,
)
from opennosh_api.body_metrics.schemas import (
    BodyMetricListResponse,
    BodyMetricResponse,
    BodyMetricTrendResponse,
    BodyMetricWrite,
)
from opennosh_api.models import BodyMetric, BodyMetricType, BodyMetricUnit


class BodyMetricInputError(ValueError):
    """A safe, user-actionable body metric query error."""


def _response(metric: BodyMetric) -> BodyMetricResponse:
    return BodyMetricResponse(
        id=metric.id,
        recorded_at=metric.recorded_at.astimezone(UTC),
        metric_type=BodyMetricType(metric.metric_type),
        value=metric.value,
        unit=BodyMetricUnit(metric.unit),
    )


def utc_date_bounds(from_date: date, to_date: date) -> tuple[datetime, datetime | None]:
    if from_date > to_date:
        raise BodyMetricInputError("from must be on or before to")
    try:
        start = datetime.combine(from_date, time.min, tzinfo=UTC)
        end = (
            None
            if to_date == date.max
            else datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=UTC)
        )
        return start, end
    except OverflowError as error:
        raise BodyMetricInputError("date range is outside the supported UTC range") from error


async def create_body_metric(
    database: AsyncSession, payload: BodyMetricWrite, current: CurrentSession
) -> BodyMetricResponse:
    metric = BodyMetric(
        user_id=current.user_id,
        recorded_at=payload.recorded_at,
        metric_type=payload.metric_type.value,
        value=payload.value,
        unit=payload.unit.value,
    )
    database.add(metric)
    await database.commit()
    return _response(metric)


async def list_body_metrics(
    database: AsyncSession,
    *,
    from_date: date,
    to_date: date,
    current: CurrentSession,
    limit: int,
    offset: int,
) -> BodyMetricListResponse:
    start, end = utc_date_bounds(from_date, to_date)
    time_conditions = [
        BodyMetric.recorded_at >= start,
        (
            BodyMetric.recorded_at <= MAX_BODY_METRIC_RECORDED_AT
            if end is None
            else BodyMetric.recorded_at < end
        ),
    ]
    rows = list(
        (
            await database.scalars(
                select(BodyMetric)
                .where(
                    BodyMetric.user_id == current.user_id,
                    *time_conditions,
                )
                .order_by(BodyMetric.recorded_at.desc(), BodyMetric.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
    )
    return BodyMetricListResponse(
        from_date=from_date,
        to_date=to_date,
        items=[_response(metric) for metric in rows[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(rows) > limit,
    )


async def body_metric_trends(
    database: AsyncSession,
    *,
    from_date: date,
    to_date: date,
    current: CurrentSession,
) -> BodyMetricTrendResponse:
    start, end = utc_date_bounds(from_date, to_date)
    if (to_date - from_date).days + 1 > BODY_METRIC_TREND_RANGE_DAYS_MAX:
        raise BodyMetricInputError(
            f"date range must contain at most {BODY_METRIC_TREND_RANGE_DAYS_MAX} days"
        )
    time_conditions = [
        BodyMetric.recorded_at >= start,
        (
            BodyMetric.recorded_at <= MAX_BODY_METRIC_RECORDED_AT
            if end is None
            else BodyMetric.recorded_at < end
        ),
    ]
    utc_day = cast(func.timezone("UTC", BodyMetric.recorded_at), Date)
    ranked = (
        select(
            BodyMetric.id.label("metric_id"),
            func.row_number()
            .over(
                partition_by=(utc_day, BodyMetric.metric_type, BodyMetric.unit),
                order_by=(BodyMetric.recorded_at.desc(), BodyMetric.id.desc()),
            )
            .label("rank"),
        )
        .where(BodyMetric.user_id == current.user_id, *time_conditions)
        .subquery()
    )
    rows = list(
        (
            await database.scalars(
                select(BodyMetric)
                .join(ranked, BodyMetric.id == ranked.c.metric_id)
                .where(ranked.c.rank == 1)
                .order_by(BodyMetric.recorded_at, BodyMetric.id)
            )
        ).all()
    )
    return BodyMetricTrendResponse(
        from_date=from_date,
        to_date=to_date,
        items=[_response(metric) for metric in rows],
    )


async def delete_body_metric(
    database: AsyncSession, metric_id: UUID, current: CurrentSession
) -> bool:
    deleted = await delete_owned_resource(
        database, BodyMetric, resource_id=metric_id, current=current
    )
    if deleted:
        await database.commit()
    return deleted
