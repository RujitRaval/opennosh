from __future__ import annotations

from types import MethodType, SimpleNamespace
from uuid import uuid4

import pytest
from opennosh_api.missions.repository import MissionRepository


class _Rows:
    def __init__(self, rows: list[tuple[str | None, int]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str | None, int]]:
        return self._rows


class FakeSession:
    def __init__(self, rows: list[tuple[str | None, int]], *, binding_count: int = 0) -> None:
        self.rows = rows
        self.binding_count = binding_count
        self.execute_count = 0

    async def execute(self, _statement: object) -> _Rows:
        self.execute_count += 1
        return _Rows(self.rows if self.execute_count == 3 else [])

    async def scalar(self, _statement: object) -> int:
        return self.binding_count


def _row(
    *,
    accepted_count: int = 1,
    matched_event_count: int = 1,
    checkpoint: object | None = None,
) -> tuple[object, object, object | None]:
    definition = SimpleNamespace(id=uuid4())
    resolved_checkpoint = (
        checkpoint
        if checkpoint is not None
        else SimpleNamespace(
            id=uuid4(),
            accepted_count=accepted_count,
            matched_event_count=matched_event_count,
        )
    )
    return definition, SimpleNamespace(action="approve"), resolved_checkpoint


def _repository(
    snapshot_rows: tuple[tuple[object, object, object | None], ...],
    aggregate_rows: list[tuple[str | None, int]],
    *,
    current: bool = True,
    binding_count: int = 0,
) -> tuple[MissionRepository, FakeSession, list[int], list[int]]:
    session = FakeSession(aggregate_rows, binding_count=binding_count)
    repository = MissionRepository(session)  # type: ignore[arg-type]
    requested_limits: list[int] = []
    currentness_limits: list[int] = []

    async def public_rows(
        _self: MissionRepository, limit: int
    ) -> tuple[tuple[object, object, object | None], ...]:
        requested_limits.append(limit)
        return snapshot_rows

    async def currentness(
        _self: MissionRepository,
        items: tuple[tuple[object, object], ...],
        *,
        max_lineage_events: int | None = None,
    ) -> dict[object, bool]:
        assert items
        assert max_lineage_events is not None
        currentness_limits.append(max_lineage_events)
        return {checkpoint.id: current for _definition, checkpoint in items}

    repository._public_mission_snapshot_rows = MethodType(  # type: ignore[method-assign]
        public_rows,
        repository,
    )
    repository._progress_currentness = MethodType(  # type: ignore[method-assign]
        currentness,
        repository,
    )
    return repository, session, requested_limits, currentness_limits


@pytest.mark.asyncio
async def test_activity_repository_aggregates_only_current_proof_bound_locales() -> None:
    repository, session, limits, currentness_limits = _repository(
        (_row(), _row()),
        [("en-US", 11), ("fr-CA", 10)],
    )

    result = await repository.public_mission_activity_locales(100, 10_000, 20_000)

    assert [(item.locale, item.accepted_count) for item in result] == [
        ("en-US", 11),
        ("fr-CA", 10),
    ]
    assert limits == [101]
    assert currentness_limits == [20_000]
    assert session.execute_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_checkpoint", [True, False])
async def test_activity_repository_fails_closed_on_missing_or_stale_projection(
    missing_checkpoint: bool,
) -> None:
    snapshot = _row()
    if missing_checkpoint:
        snapshot = (snapshot[0], snapshot[1], None)
    repository, session, _limits, _currentness_limits = _repository(
        (snapshot,),
        [("en-US", 10)],
        current=missing_checkpoint,
    )

    with pytest.raises(ValueError, match="proof_unavailable"):
        await repository.public_mission_activity_locales(100, 10_000, 20_000)

    assert session.execute_count == 2


@pytest.mark.asyncio
async def test_activity_repository_fails_closed_on_missing_immutable_locale_proof() -> None:
    repository, _session, _limits, _currentness_limits = _repository(
        (_row(),),
        [(None, 1)],
    )

    with pytest.raises(ValueError, match="locale_proof_unavailable"):
        await repository.public_mission_activity_locales(100, 10_000, 20_000)


@pytest.mark.asyncio
async def test_activity_repository_refuses_partial_mission_scope_before_currentness() -> None:
    repository, session, limits, currentness_limits = _repository(
        tuple(_row() for _ in range(101)),
        [("en-US", 101)],
    )

    with pytest.raises(ValueError, match="scope_too_large"):
        await repository.public_mission_activity_locales(100, 10_000, 20_000)

    assert limits == [101]
    assert currentness_limits == []
    assert session.execute_count == 2


@pytest.mark.asyncio
async def test_activity_repository_bounds_records_bindings_and_lineage_before_expansion() -> None:
    repository, _session, _limits, currentness_limits = _repository(
        (_row(accepted_count=10_001, matched_event_count=10_001),),
        [],
    )
    with pytest.raises(ValueError, match="record_scope_too_large"):
        await repository.public_mission_activity_locales(100, 10_000, 20_000)
    assert currentness_limits == []

    repository, _session, _limits, currentness_limits = _repository(
        (_row(accepted_count=1, matched_event_count=20_001),),
        [],
    )
    with pytest.raises(ValueError, match="lineage_scope_too_large"):
        await repository.public_mission_activity_locales(100, 10_000, 20_000)
    assert currentness_limits == []

    repository, _session, _limits, currentness_limits = _repository(
        (_row(),),
        [],
        binding_count=20_001,
    )
    with pytest.raises(ValueError, match="binding_scope_too_large"):
        await repository.public_mission_activity_locales(100, 10_000, 20_000)
    assert currentness_limits == []
