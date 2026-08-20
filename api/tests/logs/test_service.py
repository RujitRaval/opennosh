from datetime import date

import pytest
from opennosh_api.logs.service import FoodLogInputError, resolve_timezone, utc_day_bounds


def test_day_bounds_follow_dst_and_user_timezone_defaults() -> None:
    timezone = resolve_timezone(None, {"timezone": "America/New_York"})
    start, end = utc_day_bounds(date(2026, 3, 8), timezone)

    assert start.isoformat() == "2026-03-08T05:00:00+00:00"
    assert end.isoformat() == "2026-03-09T04:00:00+00:00"
    assert (end - start).total_seconds() == 23 * 60 * 60


def test_timezone_override_and_invalid_names() -> None:
    assert resolve_timezone("Asia/Kolkata", {"timezone": "UTC"}).key == "Asia/Kolkata"
    assert resolve_timezone(None, {}).key == "UTC"
    for name in ("", "../UTC", "Unknown/Nowhere", "UTC\x00"):
        with pytest.raises(FoodLogInputError, match="IANA timezone"):
            resolve_timezone(name, {})
