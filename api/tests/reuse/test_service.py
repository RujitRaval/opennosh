from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from opennosh_api.reuse.contracts import (
    ReuseDeclarationCreate,
    ReuseDeclarationPatch,
    ReuseDeclarationState,
    ReuseEventType,
)
from opennosh_api.reuse.models import ReuseDeclaration, ReuseDeclarationEvent
from opennosh_api.reuse.service import (
    ReuseRegistryError,
    create_declaration,
    patch_declaration,
    transition_declaration,
)

OWNER = UUID("10000000-0000-4000-8000-000000000001")
OTHER = UUID("10000000-0000-4000-8000-000000000002")
DECLARATION_ID = UUID("20000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 3, 22, tzinfo=UTC)


class FakeSession:
    def __init__(self, scalar_results: list[object | None] | None = None) -> None:
        self.scalar_results = list(scalar_results or [])
        self.added: list[object] = []
        self.executed: list[object] = []
        self.flushes = 0

    async def scalar(self, statement: object) -> object | None:
        self.executed.append(statement)
        return self.scalar_results.pop(0) if self.scalar_results else None

    async def execute(self, statement: object, parameters: object | None = None) -> object:
        self.executed.append((statement, parameters))
        return object()

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


def _request(**changes: Any) -> ReuseDeclarationCreate:
    values: dict[str, object] = {
        "organization_name": "Community Kitchen",
        "project_name": "Meal Commons",
        "project_url": "https://example.test/reuse",
        "use_case": "Uses pack data in a public menu.",
        "region_level": "country",
        "region_code": "US",
    }
    values.update(changes)
    return ReuseDeclarationCreate.model_validate(values)


@pytest.mark.asyncio
async def test_create_persists_normalized_projection_and_append_only_event() -> None:
    session = FakeSession([None, None])
    declaration, created = await create_declaration(
        session,  # type: ignore[arg-type]
        owner_actor_id=OWNER,
        request=_request(organization_name="  COMMUNITY   Kitchen "),
        idempotency_key=UUID("30000000-0000-4000-8000-000000000001"),
        now=NOW,
        declaration_id_generator=lambda: DECLARATION_ID,
        event_id_generator=lambda: UUID("40000000-0000-4000-8000-000000000001"),
    )

    assert created
    assert declaration.id == DECLARATION_ID
    assert declaration.organization_name == "COMMUNITY Kitchen"
    assert declaration.organization_key == "community kitchen"
    assert declaration.project_key == "meal commons"
    assert declaration.state == ReuseDeclarationState.COMMUNITY_DECLARED.value
    assert declaration.revision == 1
    event = next(value for value in session.added if isinstance(value, ReuseDeclarationEvent))
    assert event.event_type == ReuseEventType.DECLARED.value
    assert event.declaration_revision == 1
    assert len(event.idempotency_key_hash) == 64
    assert len(event.request_hash) == 64
    assert session.flushes == 1


@pytest.mark.asyncio
async def test_create_replays_only_the_same_idempotent_request() -> None:
    first = FakeSession([None, None])
    declaration, _ = await create_declaration(
        first,  # type: ignore[arg-type]
        owner_actor_id=OWNER,
        request=_request(),
        idempotency_key=UUID("30000000-0000-4000-8000-000000000002"),
        now=NOW,
        declaration_id_generator=lambda: DECLARATION_ID,
    )
    event = next(value for value in first.added if isinstance(value, ReuseDeclarationEvent))

    replay = FakeSession([event, declaration])
    replayed, created = await create_declaration(
        replay,  # type: ignore[arg-type]
        owner_actor_id=OWNER,
        request=_request(),
        idempotency_key=UUID("30000000-0000-4000-8000-000000000002"),
        now=NOW + timedelta(minutes=1),
    )
    assert replayed is declaration
    assert not created
    assert replay.added == []

    conflict = FakeSession([event])
    with pytest.raises(ReuseRegistryError, match="reuse_idempotency_payload_mismatch"):
        await create_declaration(
            conflict,  # type: ignore[arg-type]
            owner_actor_id=OWNER,
            request=_request(project_name="Different project"),
            idempotency_key=UUID("30000000-0000-4000-8000-000000000002"),
            now=NOW + timedelta(minutes=2),
        )


@pytest.mark.asyncio
async def test_edit_invalidates_pending_verification_and_increments_revision() -> None:
    declaration = ReuseDeclaration(
        id=DECLARATION_ID,
        owner_actor_id=OWNER,
        organization_name="Community Kitchen",
        organization_key="community kitchen",
        project_name="Meal Commons",
        project_key="meal commons",
        project_url="https://example.test/reuse",
        use_case="Original use.",
        region_level="country",
        region_code="US",
        state=ReuseDeclarationState.VERIFICATION_PENDING.value,
        revision=2,
        created_at=NOW,
        updated_at=NOW,
        withdrawn_at=None,
    )
    session = FakeSession([None, declaration])
    result = await patch_declaration(
        session,  # type: ignore[arg-type]
        declaration_id=DECLARATION_ID,
        owner_actor_id=OWNER,
        expected_revision=2,
        request=ReuseDeclarationPatch(use_case="Corrected use."),
        idempotency_key=UUID("30000000-0000-4000-8000-000000000003"),
        now=NOW + timedelta(minutes=1),
    )
    assert result.use_case == "Corrected use."
    assert result.state == ReuseDeclarationState.COMMUNITY_DECLARED.value
    assert result.revision == 3
    event = next(value for value in session.added if isinstance(value, ReuseDeclarationEvent))
    assert event.event_type == ReuseEventType.EDITED.value
    assert event.declaration_revision == 3


@pytest.mark.asyncio
async def test_owner_lifecycle_submit_withdraw_restore_is_exact() -> None:
    declaration = ReuseDeclaration(
        id=DECLARATION_ID,
        owner_actor_id=OWNER,
        organization_name="Community Kitchen",
        organization_key="community kitchen",
        project_name="Meal Commons",
        project_key="meal commons",
        project_url=None,
        use_case="Public menu.",
        region_level=None,
        region_code=None,
        state=ReuseDeclarationState.COMMUNITY_DECLARED.value,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
        withdrawn_at=None,
    )
    for offset, action, state in (
        (1, ReuseEventType.SUBMITTED, ReuseDeclarationState.VERIFICATION_PENDING),
        (2, ReuseEventType.WITHDRAWN, ReuseDeclarationState.WITHDRAWN),
        (3, ReuseEventType.RESTORED, ReuseDeclarationState.COMMUNITY_DECLARED),
    ):
        session = FakeSession([None, declaration])
        declaration = await transition_declaration(
            session,  # type: ignore[arg-type]
            declaration_id=DECLARATION_ID,
            owner_actor_id=OWNER,
            expected_revision=declaration.revision,
            action=action,
            idempotency_key=UUID(f"30000000-0000-4000-8000-{offset:012d}"),
            reason="Owner requested this transition.",
            now=NOW + timedelta(minutes=offset),
        )
        assert declaration.state == state.value
        assert declaration.revision == offset + 1
    assert declaration.withdrawn_at is None


@pytest.mark.asyncio
async def test_stale_revision_invalid_transition_and_cross_owner_all_fail_closed() -> None:
    declaration = ReuseDeclaration(
        id=DECLARATION_ID,
        owner_actor_id=OWNER,
        organization_name="Community Kitchen",
        organization_key="community kitchen",
        project_name="Meal Commons",
        project_key="meal commons",
        project_url=None,
        use_case="Public menu.",
        region_level=None,
        region_code=None,
        state=ReuseDeclarationState.VERIFICATION_PENDING.value,
        revision=2,
        created_at=NOW,
        updated_at=NOW,
        withdrawn_at=None,
    )
    with pytest.raises(ReuseRegistryError, match="reuse_revision_conflict"):
        await transition_declaration(
            FakeSession([None, declaration]),  # type: ignore[arg-type]
            declaration_id=DECLARATION_ID,
            owner_actor_id=OWNER,
            expected_revision=1,
            action=ReuseEventType.WITHDRAWN,
            idempotency_key=UUID("30000000-0000-4000-8000-000000000010"),
            reason=None,
            now=NOW,
        )
    with pytest.raises(ReuseRegistryError, match="reuse_transition_not_allowed"):
        await transition_declaration(
            FakeSession([None, declaration]),  # type: ignore[arg-type]
            declaration_id=DECLARATION_ID,
            owner_actor_id=OWNER,
            expected_revision=2,
            action=ReuseEventType.SUBMITTED,
            idempotency_key=UUID("30000000-0000-4000-8000-000000000011"),
            reason=None,
            now=NOW,
        )
    with pytest.raises(ReuseRegistryError, match="reuse_declaration_not_found"):
        await transition_declaration(
            FakeSession([None, None]),  # type: ignore[arg-type]
            declaration_id=DECLARATION_ID,
            owner_actor_id=OTHER,
            expected_revision=2,
            action=ReuseEventType.WITHDRAWN,
            idempotency_key=UUID("30000000-0000-4000-8000-000000000012"),
            reason=None,
            now=NOW,
        )
