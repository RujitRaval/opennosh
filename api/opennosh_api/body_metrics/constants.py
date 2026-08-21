from datetime import UTC, datetime, timedelta
from decimal import Decimal

BODY_METRIC_DECIMAL_PLACES = 4
BODY_METRIC_QUANTUM = Decimal("0.0001")
MAX_BODY_METRIC_VALUE = Decimal("1000000")
BODY_METRIC_LIST_LIMIT_DEFAULT = 100
BODY_METRIC_LIST_LIMIT_MAX = 100
BODY_METRIC_LIST_OFFSET_MAX = 10_000
BODY_METRIC_TREND_RANGE_DAYS_MAX = 90
# asyncpg reserves datetime.min/max as its PostgreSQL infinity sentinels.
MIN_BODY_METRIC_RECORDED_AT = datetime.min.replace(tzinfo=UTC) + timedelta(
    microseconds=1
)
MAX_BODY_METRIC_RECORDED_AT = datetime.max.replace(tzinfo=UTC) - timedelta(
    microseconds=1
)
