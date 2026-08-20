from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from opennosh_api.exercises.service import (
    ExerciseExportTimeoutError,
    prepare_exercise_export,
)
from opennosh_api.exports import service as export_service
from opennosh_api.exports.service import (
    CommunityExportLimitError,
    ExportTimeoutError,
    prepare_community_export,
    prepare_private_export,
)
from opennosh_api.foods.open_food_facts import (
    OpenFoodFactsExportTimeoutError,
    prepare_cached_product_export,
)
from sqlalchemy.exc import DBAPIError


class DatabaseTimeout(Exception):
    sqlstate = "57014"


class FailingStreamDatabase:
    def __init__(self, *, owner: object | None = None) -> None:
        self.owner = owner
        self.rolled_back = False

    async def execute(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def scalar(self, *_args: object, **_kwargs: object) -> object | None:
        return self.owner

    async def stream_scalars(self, *_args: object, **_kwargs: object) -> None:
        raise DBAPIError("SELECT", {}, DatabaseTimeout())

    async def rollback(self) -> None:
        self.rolled_back = True


class AsyncRows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = iter(rows)
        self.closed = False

    def __aiter__(self) -> AsyncRows:
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._rows)
        except StopIteration as error:
            raise StopAsyncIteration from error

    async def close(self) -> None:
        self.closed = True


class StreamingDatabase:
    def __init__(self, rows: list[Any]) -> None:
        self.result = AsyncRows(rows)
        self.rolled_back = False

    async def execute(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def stream_scalars(self, *_args: object, **_kwargs: object) -> AsyncRows:
        return self.result

    async def rollback(self) -> None:
        self.rolled_back = True


def _community_row(*, source_note: str = "Measured") -> SimpleNamespace:
    return SimpleNamespace(
        pack_id="fixture-pack",
        pack_version="1.0.0",
        pack_license="CC0-1.0",
        slug="fixture-food",
        name="Fixture food",
        name_local=None,
        locale="en-US",
        category="fixture",
        contributed_by="Visible Contributor",
        provenance="own_measurement",
        source_uri="https://example.test/fixture-food",
        source_license="contributor-original",
        source_note=source_note,
        nutrients_json={
            "basis": "per_100g",
            "nutrients": {
                "energy_kcal": "100",
                "protein_g": "1",
                "carbohydrate_g": "2",
                "fat_g": "3",
            },
        },
        portions_json=[],
    )


def test_production_stream_timeouts_are_mapped_before_response_headers() -> None:
    async def exercise() -> None:
        off = FailingStreamDatabase()
        with pytest.raises(OpenFoodFactsExportTimeoutError):
            await prepare_cached_product_export(off, statement_timeout_ms=10)  # type: ignore[arg-type]
        assert off.rolled_back is True

        exercises = FailingStreamDatabase()
        with pytest.raises(ExerciseExportTimeoutError):
            await prepare_exercise_export(exercises, statement_timeout_ms=10)  # type: ignore[arg-type]
        assert exercises.rolled_back is True

        community = FailingStreamDatabase()
        with pytest.raises(ExportTimeoutError):
            await prepare_community_export(community, statement_timeout_ms=10)  # type: ignore[arg-type]
        assert community.rolled_back is True

        owner_id = uuid4()
        owner = SimpleNamespace(
            id=owner_id,
            email="owner@example.test",
            created_at=datetime.now(UTC),
            settings_json={},
        )
        private = FailingStreamDatabase(owner=owner)
        current = SimpleNamespace(user_id=owner_id)
        with pytest.raises(ExportTimeoutError):
            await prepare_private_export(  # type: ignore[arg-type]
                private,
                current=current,
                statement_timeout_ms=10,
            )
        assert private.rolled_back is True

    asyncio.run(exercise())


def test_production_community_export_enforces_row_and_serialized_byte_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(export_service, "EXPORT_ROW_LIMIT", 1)
        over_rows = StreamingDatabase([_community_row(), _community_row()])
        with pytest.raises(CommunityExportLimitError):
            await prepare_community_export(  # type: ignore[arg-type]
                over_rows, statement_timeout_ms=10
            )
        assert over_rows.result.closed is True
        assert over_rows.rolled_back is True

        monkeypatch.setattr(export_service, "EXPORT_ROW_LIMIT", 10_000)
        monkeypatch.setattr(export_service, "EXPORT_MAX_SERIALIZED_BYTES", 500)
        over_bytes = StreamingDatabase([_community_row(source_note="x" * 1_000)])
        with pytest.raises(CommunityExportLimitError):
            await prepare_community_export(  # type: ignore[arg-type]
                over_bytes, statement_timeout_ms=10
            )
        assert over_bytes.result.closed is True
        assert over_bytes.rolled_back is True

    asyncio.run(exercise())
