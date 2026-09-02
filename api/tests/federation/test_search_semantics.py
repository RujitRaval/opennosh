from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from opennosh_api.federation.search import (
    active_federation_projection,
    federation_food_detail,
)
from opennosh_api.federation.service import deterministic_equivalence_key


class _MappedResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def mappings(self) -> _MappedResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.row


class _Database:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.execute_calls = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> _MappedResult:
        self.execute_calls += 1
        return _MappedResult(self.row)


def _record(**updates: object) -> dict[str, object]:
    record: dict[str, object] = {
        "category": "fruit",
        "name": "Apple, raw",
        "source_license": "public-domain",
        "source_uri": "https://fdc.nal.usda.gov/fdc-app.html#/food-details/171688",
    }
    record.update(updates)
    return record


def test_equivalence_requires_an_exact_explicit_source_identity() -> None:
    baseline = deterministic_equivalence_key(_record())

    assert baseline is not None
    assert baseline == deterministic_equivalence_key(_record(name="  APPLE,   RAW "))
    assert baseline != deterministic_equivalence_key(_record(category="juice"))
    assert baseline != deterministic_equivalence_key(
        _record(source_uri="https://fdc.nal.usda.gov/fdc-app.html#/food-details/171689")
    )
    assert deterministic_equivalence_key(_record(source_uri=None)) is None


def test_nutrient_disagreement_does_not_change_equivalence_identity() -> None:
    first = _record(nutrients_json={"energy_kcal": "52"})
    second = _record(nutrients_json={"energy_kcal": "60"})

    assert deterministic_equivalence_key(first) == deterministic_equivalence_key(second)


def test_active_projection_handles_empty_and_materialized_states() -> None:
    assert asyncio.run(active_federation_projection(_Database())) is None  # type: ignore[arg-type]

    checkpoint_id = UUID("018f7d40-7b60-7000-8000-000000000099")
    quarantined_at = datetime(2026, 9, 2, tzinfo=UTC)
    projection = asyncio.run(
        active_federation_projection(  # type: ignore[arg-type]
            _Database(
                {
                    "checkpoint_id": checkpoint_id,
                    "release_set_digest": "a" * 64,
                    "stale": True,
                    "quarantine_cutoff": quarantined_at,
                }
            )
        )
    )

    assert projection is not None
    assert projection.checkpoint_id == checkpoint_id
    assert projection.release_set_digest == "a" * 64
    assert projection.stale is True
    assert projection.quarantine_cutoff == quarantined_at


def test_federation_detail_rejects_malformed_source_identity_without_querying() -> None:
    for source_id in ("missing-separator", "not-a-uuid:record", "also-invalid:"):
        database = _Database()
        assert (
            asyncio.run(federation_food_detail(database, source_id)) is None  # type: ignore[arg-type]
        )
        assert database.execute_calls == 0
