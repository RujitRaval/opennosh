from datetime import UTC, datetime, timedelta
from decimal import Decimal

WORKOUT_LIST_LIMIT_DEFAULT = 50
WORKOUT_LIST_LIMIT_MAX = 100
WORKOUT_LIST_OFFSET_MAX = 10_000
MAX_WORKOUT_SETS = 500
MAX_WORKOUT_NOTES_LENGTH = 5_000
MAX_REPS = 100_000
MAX_LOAD_VALUE = Decimal("1000000")
LOAD_DECIMAL_PLACES = 3
LOAD_QUANTUM = Decimal("0.001")

# asyncpg reserves the exact Python extrema for PostgreSQL infinity sentinels.
MIN_WORKOUT_PERFORMED_AT = datetime.min.replace(tzinfo=UTC) + timedelta(microseconds=1)
MAX_WORKOUT_PERFORMED_AT = datetime.max.replace(tzinfo=UTC) - timedelta(microseconds=1)
