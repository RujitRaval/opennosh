from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from opennosh_api.reuse.contracts import (
    ReuseDeclarationState,
    ReuseEventType,
    ReuseEvidenceStatus,
    ReuseVerificationEvidence,
)
from opennosh_api.reuse.models import ReuseDeclaration, ReuseDeclarationEvent
from opennosh_api.reuse.service import (
    ReuseRegistryError,
    list_reviewable_declarations,
    review_declaration,
)

OWNER = UUID("10000000-0000-4000-8000-000000000001")
STEWARD = UUID("10000000-0000-4000-8000-000000000002")
DECLARATION_ID = UUID("20000000-0000-4000-8000-000000000001")
ROLE_ID = UUID("50000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 4, 2, tzinfo=UTC)


class FakeSession:
    def __init__(
        self,
        scalar_results: list[object | None] | None = None,
        scalar_rows: list[object] | None = None,
    ) -> None:
        self.scalar_results = list(scalar_results or [])
        self.scalar_rows = list(scalar_rows or [])
        self.added: list[object] = []
        self.statements: list[object] = []
        self.flushes = 0

    async def scalar(self, statement: object) -> object | None:
        self.statements.append(statement)
        return self.scalar_results.pop(0) if self.scalar_results else None

    async def scalars(self, statement: object) -> list[object]:
        self.statements.append(statement)
        return self.scalar_rows

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1


def _declaration(**changes: Any) -> ReuseDeclaration:
    values: dict[str, object] = {
        "id": DECLARATION_ID,
        "owner_actor_id": OWNER,
        "organization_name": "Community Kitchen",
        "organization_key": "community kitchen",
        "project_name": "Meal Commons",
        "project_key": "meal commons",
        "project_url": "https://example.test/reuse",
        "use_case": "Uses verified records in a menu.",
        "region_level": "country",
        "region_code": "US",
        "state": ReuseDeclarationState.VERIFICATION_PENDING.value,
        "revision": 2,
        "created_at": NOW - timedelta(days=2),
        "updated_at": NOW - timedelta(days=1),
        "withdrawn_at": None,
    }
    values.update(changes)
    return ReuseDeclaration(**values)  # type: ignore[arg-type]


def _evidence(**changes: Any) -> ReuseVerificationEvidence:
    values: dict[str, object] = {
        "source_url": "https://evidence.example.test/adoption",
        "observed_at": NOW - timedelta(days=1),
        "content_sha256": "a" * 64,
        "status": ReuseEvidenceStatus.ACCESSIBLE,
    }
    values.update(changes)
    return ReuseVerificationEvidence.model_validate(values)


@pytest.mark.asyncio
async def test_review_queue_requires_active_non_recused_registry_steward() -> None:
    candidate = _declaration()
    session = FakeSession([ROLE_ID, None], [candidate])
    rows = await list_reviewable_declarations(
        session,  # type: ignore[arg-type]
        steward_actor_id=STEWARD,
        now=NOW,
        limit=100,
    )
    assert rows == (candidate,)
    statement = str(session.statements[-1])
    assert "reuse_declarations.state" in statement
    assert "reuse_declarations.owner_actor_id !=" in statement

    with pytest.raises(ReuseRegistryError, match="reuse_steward_role_not_active"):
        await list_reviewable_declarations(
            FakeSession([None]),  # type: ignore[arg-type]
            steward_actor_id=STEWARD,
            now=NOW,
            limit=100,
        )
    with pytest.raises(ReuseRegistryError, match="reuse_steward_recused"):
        await list_reviewable_declarations(
            FakeSession([ROLE_ID, UUID(int=9)]),  # type: ignore[arg-type]
            steward_actor_id=STEWARD,
            now=NOW,
            limit=100,
        )


@pytest.mark.asyncio
async def test_verify_records_accessible_fresh_digest_bound_evidence() -> None:
    declaration = _declaration()
    evidence = _evidence()
    session = FakeSession([ROLE_ID, None, None, declaration])
    result = await review_declaration(
        session,  # type: ignore[arg-type]
        declaration_id=DECLARATION_ID,
        steward_actor_id=STEWARD,
        expected_revision=2,
        action=ReuseEventType.VERIFIED,
        idempotency_key=UUID("30000000-0000-4000-8000-000000000001"),
        reason="Independent adoption evidence reviewed.",
        evidence=evidence,
        now=NOW,
    )
    assert result.state == ReuseDeclarationState.VERIFIED.value
    assert result.revision == 3
    event = next(value for value in session.added if isinstance(value, ReuseDeclarationEvent))
    assert event.event_type == ReuseEventType.VERIFIED.value
    assert event.evidence_json == evidence.model_dump(mode="json")
    assert event.declaration_revision == 3
    assert session.flushes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("evidence", "code"),
    [
        (None, "reuse_verification_evidence_unavailable"),
        (
            _evidence(status=ReuseEvidenceStatus.INACCESSIBLE),
            "reuse_verification_evidence_inaccessible",
        ),
        (
            _evidence(status=ReuseEvidenceStatus.UNAVAILABLE),
            "reuse_verification_evidence_inaccessible",
        ),
        (
            _evidence(observed_at=NOW - timedelta(days=31)),
            "reuse_verification_evidence_stale",
        ),
        (
            _evidence(observed_at=NOW + timedelta(seconds=1)),
            "reuse_verification_evidence_from_future",
        ),
    ],
)
async def test_unavailable_inaccessible_stale_or_future_evidence_never_verifies(
    evidence: ReuseVerificationEvidence | None,
    code: str,
) -> None:
    declaration = _declaration()
    session = FakeSession([ROLE_ID, None, None, declaration])
    with pytest.raises(ReuseRegistryError, match=code):
        await review_declaration(
            session,  # type: ignore[arg-type]
            declaration_id=DECLARATION_ID,
            steward_actor_id=STEWARD,
            expected_revision=2,
            action=ReuseEventType.VERIFIED,
            idempotency_key=UUID("30000000-0000-4000-8000-000000000002"),
            reason="Evidence reviewed.",
            evidence=evidence,
            now=NOW,
        )
    assert declaration.state == ReuseDeclarationState.VERIFICATION_PENDING.value
    assert session.added == []


@pytest.mark.asyncio
async def test_self_review_stale_revision_and_nonpending_state_fail_closed() -> None:
    cases = (
        (_declaration(owner_actor_id=STEWARD), 2, "reuse_self_review_prohibited"),
        (_declaration(), 1, "reuse_revision_conflict"),
        (
            _declaration(state=ReuseDeclarationState.COMMUNITY_DECLARED.value),
            2,
            "reuse_review_transition_not_allowed",
        ),
    )
    for offset, (declaration, revision, code) in enumerate(cases, start=10):
        with pytest.raises(ReuseRegistryError, match=code):
            await review_declaration(
                FakeSession([ROLE_ID, None, None, declaration]),  # type: ignore[arg-type]
                declaration_id=DECLARATION_ID,
                steward_actor_id=STEWARD,
                expected_revision=revision,
                action=ReuseEventType.VERIFIED,
                idempotency_key=UUID(f"30000000-0000-4000-8000-{offset:012d}"),
                reason="Evidence reviewed.",
                evidence=_evidence(),
                now=NOW,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [ReuseEventType.CHANGES_REQUESTED, ReuseEventType.REJECTED],
)
async def test_nonapproval_returns_declaration_to_community_state(
    action: ReuseEventType,
) -> None:
    declaration = _declaration()
    session = FakeSession([ROLE_ID, None, None, declaration])
    result = await review_declaration(
        session,  # type: ignore[arg-type]
        declaration_id=DECLARATION_ID,
        steward_actor_id=STEWARD,
        expected_revision=2,
        action=action,
        idempotency_key=UUID(
            "30000000-0000-4000-8000-000000000020"
            if action is ReuseEventType.CHANGES_REQUESTED
            else "30000000-0000-4000-8000-000000000021"
        ),
        reason="Declaration needs correction.",
        evidence=None,
        now=NOW,
    )
    assert result.state == ReuseDeclarationState.COMMUNITY_DECLARED.value
    event = next(value for value in session.added if isinstance(value, ReuseDeclarationEvent))
    assert event.event_type == action.value
    assert event.evidence_json == {}


@pytest.mark.asyncio
async def test_review_retry_is_idempotent_and_payload_bound() -> None:
    declaration = _declaration()
    key = UUID("30000000-0000-4000-8000-000000000030")
    first = FakeSession([ROLE_ID, None, None, declaration])
    await review_declaration(
        first,  # type: ignore[arg-type]
        declaration_id=DECLARATION_ID,
        steward_actor_id=STEWARD,
        expected_revision=2,
        action=ReuseEventType.VERIFIED,
        idempotency_key=key,
        reason="Evidence reviewed.",
        evidence=_evidence(),
        now=NOW,
    )
    event = next(value for value in first.added if isinstance(value, ReuseDeclarationEvent))

    replay = FakeSession([ROLE_ID, None, event, declaration])
    result = await review_declaration(
        replay,  # type: ignore[arg-type]
        declaration_id=DECLARATION_ID,
        steward_actor_id=STEWARD,
        expected_revision=2,
        action=ReuseEventType.VERIFIED,
        idempotency_key=key,
        reason="Evidence reviewed.",
        evidence=_evidence(),
        now=NOW + timedelta(minutes=1),
    )
    assert result is declaration
    assert replay.added == []

    with pytest.raises(ReuseRegistryError, match="reuse_idempotency_payload_mismatch"):
        await review_declaration(
            FakeSession([ROLE_ID, None, event]),  # type: ignore[arg-type]
            declaration_id=DECLARATION_ID,
            steward_actor_id=STEWARD,
            expected_revision=2,
            action=ReuseEventType.VERIFIED,
            idempotency_key=key,
            reason="Different reason.",
            evidence=_evidence(),
            now=NOW + timedelta(minutes=1),
        )
