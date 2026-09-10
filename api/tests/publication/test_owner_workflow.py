from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from opennosh_api.jobs.worker import PublicationActivationStatus
from opennosh_api.publication.owner_workflow import (
    OwnerPublicationSelection,
    OwnerPublicationSelectionError,
    OwnerPublicationTimeoutError,
    _report,
    owner_activation_settings,
    run_owner_publication,
    select_owner_publication,
)
from opennosh_api.publication.state import PublicationState
from opennosh_api.settings import Settings


class _Acquire:
    def __init__(self, connection: object) -> None:
        self._connection = connection

    async def __aenter__(self) -> object:
        return self._connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _SelectionConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.arguments: tuple[object, ...] | None = None
        self.query = ""

    async def fetch(self, query: str, *arguments: object) -> list[dict[str, object]]:
        self.query = query
        self.arguments = arguments
        return self.rows


class _SelectionPool:
    def __init__(self, connection: _SelectionConnection) -> None:
        self.connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


class _Driver:
    def __init__(self, statuses: list[PublicationActivationStatus]) -> None:
        self.statuses = statuses
        self.stopped = asyncio.Event()
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def drain(self) -> None:
        await self.stopped.wait()

    def stop_claiming(self) -> None:
        self.stopped.set()

    async def close(self) -> None:
        self.closed = True
        self.stopped.set()

    async def publication_status(self, _publication_id: UUID) -> PublicationActivationStatus:
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "app_environment": "test",
            "process_role": "publication",
            "publication_database_url": "postgresql+asyncpg://worker:test@db/opennosh",
            "database_capacity_manifest_path": Path("config/database-capacity.v1.yaml"),
        }
    )


@pytest.mark.asyncio
async def test_owner_selection_binds_complete_same_actor_lineage() -> None:
    publication_id = uuid4()
    decision_id = uuid4()
    authorization_id = uuid4()
    actor_id = uuid4()
    connection = _SelectionConnection(
        [
            {
                "publication_id": publication_id,
                "decision_id": decision_id,
                "owner_authorization_id": authorization_id,
                "pack_id": "indian-sweets",
                "record_id": "orange-burfee",
            }
        ]
    )

    selected = await select_owner_publication(
        _SelectionPool(connection),  # type: ignore[arg-type]
        actor_id=actor_id,
        pack_id="indian-sweets",
    )

    assert selected.publication_id == publication_id
    assert connection.arguments == (
        "indian-sweets",
        actor_id,
        ["pending", "running", "retrying", "committed", "signed", "publish_retrying"],
    )
    assert "draft.user_id = $2" in connection.query
    assert "d.contributor_actor_id = $2" in connection.query
    assert "d.deciding_actor_id = $2" in connection.query
    assert "owner_auth.revoked_at IS NULL" in connection.query
    assert "LIMIT 2" in connection.query


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [{"publication_id": uuid4()}] * 2])
async def test_owner_selection_requires_exactly_one_candidate(
    rows: list[dict[str, object]],
) -> None:
    with pytest.raises(OwnerPublicationSelectionError):
        await select_owner_publication(
            _SelectionPool(_SelectionConnection(rows)),  # type: ignore[arg-type]
            actor_id=uuid4(),
            pack_id="indian-sweets",
        )


def test_owner_activation_is_exact_and_does_not_enable_continuous_claims() -> None:
    publication_id = uuid4()

    activated = owner_activation_settings(_settings(), publication_id)

    assert activated.publication_claims_enabled is True
    assert activated.publication_continuous_claims_enabled is False
    assert activated.publication_activation_ids == str(publication_id)


@pytest.mark.asyncio
async def test_owner_workflow_waits_for_terminal_result_and_closes_driver() -> None:
    publication_id = uuid4()
    decision_id = uuid4()
    authorization_id = uuid4()
    actor_id = uuid4()
    selection = OwnerPublicationSelection(
        publication_id=publication_id,
        decision_id=decision_id,
        owner_authorization_id=authorization_id,
        pack_id="indian-sweets",
        record_id="orange-burfee",
    )
    now = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
    driver = _Driver(
        [
            PublicationActivationStatus(
                publication_id=publication_id,
                state=PublicationState.RUNNING,
                pack_id="indian-sweets",
                record_id="orange-burfee",
                published_at=None,
                receipt_digest=None,
                receipt_reference=None,
            ),
            PublicationActivationStatus(
                publication_id=publication_id,
                state=PublicationState.PUBLISHED,
                pack_id="indian-sweets",
                record_id="orange-burfee",
                published_at=now,
                receipt_digest="a" * 64,
                receipt_reference="receipts/v1/example.json",
            ),
        ]
    )

    async def discover(
        _settings: Settings, *, actor_id: UUID, pack_id: str
    ) -> OwnerPublicationSelection:
        assert actor_id
        assert pack_id == "indian-sweets"
        return selection

    async def driver_factory(**options: object) -> Any:
        activated = options["settings"]
        assert isinstance(activated, Settings)
        assert activated.publication_activation_id == publication_id
        return driver

    report = await run_owner_publication(
        _settings(),
        actor_id=actor_id,
        pack_id="indian-sweets",
        timeout_seconds=1,
        poll_seconds=0.001,
        discover=discover,
        driver_factory=driver_factory,
    )

    assert report.state == "published"
    assert report.publication_intent_id == str(publication_id)
    assert report.governance_decision_id == str(decision_id)
    assert report.owner_authorization_id == str(authorization_id)
    assert report.receipt_digest == "a" * 64
    assert report.published_at == now.isoformat()
    assert driver.started is True
    assert driver.closed is True


@pytest.mark.asyncio
async def test_owner_workflow_timeout_still_closes_driver() -> None:
    selection = OwnerPublicationSelection(
        publication_id=uuid4(),
        decision_id=uuid4(),
        owner_authorization_id=uuid4(),
        pack_id="indian-sweets",
        record_id="orange-burfee",
    )
    driver = _Driver(
        [
            PublicationActivationStatus(
                publication_id=selection.publication_id,
                state=PublicationState.RUNNING,
                pack_id=selection.pack_id,
                record_id=selection.record_id,
                published_at=None,
                receipt_digest=None,
                receipt_reference=None,
            )
        ]
    )

    async def discover(
        _settings: Settings, *, actor_id: UUID, pack_id: str
    ) -> OwnerPublicationSelection:
        assert actor_id
        assert pack_id == selection.pack_id
        return selection

    async def driver_factory(**_options: object) -> Any:
        return driver

    with pytest.raises(OwnerPublicationTimeoutError):
        await run_owner_publication(
            _settings(),
            actor_id=uuid4(),
            pack_id=selection.pack_id,
            timeout_seconds=0.001,
            poll_seconds=0.001,
            discover=discover,
            driver_factory=driver_factory,
        )

    assert driver.started is True
    assert driver.closed is True


@pytest.mark.asyncio
async def test_owner_workflow_timeout_bounds_stalled_discovery() -> None:
    async def stalled_discovery(
        _settings: Settings, *, actor_id: UUID, pack_id: str
    ) -> OwnerPublicationSelection:
        assert actor_id
        assert pack_id == "indian-sweets"
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    with pytest.raises(OwnerPublicationTimeoutError):
        await asyncio.wait_for(
            run_owner_publication(
                _settings(),
                actor_id=uuid4(),
                pack_id="indian-sweets",
                timeout_seconds=0.001,
                poll_seconds=0.001,
                discover=stalled_discovery,
            ),
            timeout=0.1,
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_owner_workflow_rejects_non_finite_timeouts(value: float) -> None:
    with pytest.raises(ValueError, match="timeouts must be positive"):
        asyncio.run(
            run_owner_publication(
                _settings(),
                actor_id=uuid4(),
                pack_id="indian-sweets",
                timeout_seconds=value,
            )
        )


def test_published_owner_report_requires_complete_signed_receipt() -> None:
    selection = OwnerPublicationSelection(
        publication_id=uuid4(),
        decision_id=uuid4(),
        owner_authorization_id=uuid4(),
        pack_id="indian-sweets",
        record_id="orange-burfee",
    )
    status = PublicationActivationStatus(
        publication_id=selection.publication_id,
        state=PublicationState.PUBLISHED,
        pack_id=selection.pack_id,
        record_id=selection.record_id,
        published_at=datetime(2026, 9, 10, 18, 0, tzinfo=UTC),
        receipt_digest=None,
        receipt_reference="receipts/v1/example.json",
    )

    with pytest.raises(RuntimeError, match="missing its signed receipt"):
        _report(selection, status)


def test_owner_report_rejects_changed_terminal_identity() -> None:
    selection = OwnerPublicationSelection(
        publication_id=uuid4(),
        decision_id=uuid4(),
        owner_authorization_id=uuid4(),
        pack_id="indian-sweets",
        record_id="orange-burfee",
    )
    status = PublicationActivationStatus(
        publication_id=selection.publication_id,
        state=PublicationState.BLOCKED,
        pack_id=selection.pack_id,
        record_id="different-record",
        published_at=None,
        receipt_digest=None,
        receipt_reference=None,
    )

    with pytest.raises(RuntimeError, match="changed identity"):
        _report(selection, status)
