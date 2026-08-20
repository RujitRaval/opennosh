from datetime import date
from decimal import Decimal

import pytest
from opennosh_api.body_metrics.schemas import BodyMetricResponse, BodyMetricWrite
from opennosh_api.body_metrics.service import BodyMetricInputError, utc_date_bounds
from opennosh_api.main import create_app
from opennosh_api.models import BodyMetricType, BodyMetricUnit
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("metric_type", "unit"),
    [
        ("body_weight", "kg"),
        ("body_weight", "lb"),
        ("body_fat_percentage", "percent"),
        ("height", "cm"),
        ("height", "in"),
        ("waist_circumference", "cm"),
        ("hip_circumference", "in"),
        ("chest_circumference", "cm"),
        ("neck_circumference", "in"),
        ("upper_arm_circumference", "cm"),
        ("thigh_circumference", "in"),
    ],
)
def test_body_metric_type_unit_pairs_are_explicit(metric_type: str, unit: str) -> None:
    metric = BodyMetricWrite.model_validate(
        {
            "recorded_at": "2026-08-20T12:00:00Z",
            "metric_type": metric_type,
            "value": "80.1250",
            "unit": unit,
        }
    )

    assert metric.metric_type is BodyMetricType(metric_type)
    assert metric.unit is BodyMetricUnit(unit)


@pytest.mark.parametrize(
    ("metric_type", "unit"),
    [
        ("body_weight", "percent"),
        ("body_fat_percentage", "kg"),
        ("height", "lb"),
        ("waist_circumference", "percent"),
    ],
)
def test_body_metric_rejects_mismatched_units(metric_type: str, unit: str) -> None:
    with pytest.raises(ValidationError, match=f"unit for {metric_type}"):
        BodyMetricWrite.model_validate(
            {
                "recorded_at": "2026-08-20T12:00:00Z",
                "metric_type": metric_type,
                "value": "80",
                "unit": unit,
            }
        )


@pytest.mark.parametrize("value", ["0", "-1", "1000000.0001", "NaN", "Infinity"])
def test_body_metric_value_is_positive_finite_and_bounded(value: str) -> None:
    with pytest.raises(ValidationError):
        BodyMetricWrite.model_validate(
            {
                "recorded_at": "2026-08-20T12:00:00Z",
                "metric_type": "body_weight",
                "value": value,
                "unit": "kg",
            }
        )


def test_body_metric_rejects_nonzero_precision_beyond_four_places() -> None:
    with pytest.raises(ValidationError, match="at most four decimal places"):
        BodyMetricWrite.model_validate(
            {
                "recorded_at": "2026-08-20T12:00:00Z",
                "metric_type": "body_weight",
                "value": "80.12345",
                "unit": "kg",
            }
        )

    accepted = BodyMetricWrite.model_validate(
        {
            "recorded_at": "2026-08-20T12:00:00Z",
            "metric_type": "body_weight",
            "value": "80.123400",
            "unit": "kg",
        }
    )
    assert accepted.value == Decimal("80.123400")


def test_body_metric_requires_an_aware_timestamp_and_forbids_extra_fields() -> None:
    payload = {
        "recorded_at": "2026-08-20T12:00:00",
        "metric_type": "body_weight",
        "value": "80",
        "unit": "kg",
    }
    with pytest.raises(ValidationError, match="UTC offset"):
        BodyMetricWrite.model_validate(payload)

    payload["recorded_at"] = "2026-08-20T12:00:00Z"
    payload["interpretation"] = "healthy"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        BodyMetricWrite.model_validate(payload)

    payload.pop("interpretation")
    payload["recorded_at"] = "9999-12-31T23:59:59.999999Z"
    with pytest.raises(ValidationError, match="supported UTC range"):
        BodyMetricWrite.model_validate(payload)

    payload["recorded_at"] = "0001-01-01T00:00:00Z"
    with pytest.raises(ValidationError, match="supported UTC range"):
        BodyMetricWrite.model_validate(payload)


def test_response_json_is_stable_for_personal_export_reuse() -> None:
    response = BodyMetricResponse.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000015",
            "recorded_at": "2026-08-20T12:00:00Z",
            "metric_type": "body_weight",
            "value": "80.1",
            "unit": "kg",
        }
    )

    assert response.model_dump(mode="json") == {
        "id": "00000000-0000-0000-0000-000000000015",
        "recorded_at": "2026-08-20T12:00:00Z",
        "metric_type": "body_weight",
        "value": "80.1000",
        "unit": "kg",
    }


def test_utc_date_bounds_are_inclusive_and_validate_order() -> None:
    start, end = utc_date_bounds(date(2026, 8, 20), date(2026, 8, 21))
    assert start.isoformat() == "2026-08-20T00:00:00+00:00"
    assert end.isoformat() == "2026-08-22T00:00:00+00:00"

    with pytest.raises(BodyMetricInputError, match="from must be on or before to"):
        utc_date_bounds(date(2026, 8, 21), date(2026, 8, 20))
    maximum_start, maximum_end = utc_date_bounds(date.max, date.max)
    assert maximum_start.isoformat() == "9999-12-31T00:00:00+00:00"
    assert maximum_end is None


def test_openapi_exposes_metric_type_and_unit_enums() -> None:
    schemas = create_app().openapi()["components"]["schemas"]

    assert schemas["BodyMetricType"]["enum"] == [member.value for member in BodyMetricType]
    assert schemas["BodyMetricUnit"]["enum"] == [member.value for member in BodyMetricUnit]
