from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

from opennosh_api.capacity import ProcessRole, load_capacity_manifest
from opennosh_api.jobs.worker import (
    PUBLICATION_DRAIN_TIMEOUT_SECONDS,
    PgQueuerRoleDriver,
    PublicationActivationStatus,
    asyncpg_dsn,
    create_publication_role_driver,
    supervise_publication_claims,
)
from opennosh_api.settings import Settings

_PACK_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ACTIVE_STATES = (
    "pending",
    "running",
    "retrying",
    "committed",
    "signed",
    "publish_retrying",
)


class OwnerPublicationSelectionError(RuntimeError):
    """The owner workflow could not select exactly one authorized publication."""


class OwnerPublicationTimeoutError(TimeoutError):
    """The bounded owner workflow did not reach a terminal state in time."""


@dataclass(frozen=True, slots=True)
class OwnerPublicationSelection:
    publication_id: UUID
    decision_id: UUID
    owner_authorization_id: UUID
    pack_id: str
    record_id: str


@dataclass(frozen=True, slots=True)
class OwnerPublicationReport:
    schema_version: str
    publication_intent_id: str
    governance_decision_id: str
    owner_authorization_id: str
    pack_id: str
    record_id: str
    state: str
    published_at: str | None
    receipt_digest: str | None
    receipt_reference: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


async def select_owner_publication(
    pool: Any,
    *,
    actor_id: UUID,
    pack_id: str,
) -> OwnerPublicationSelection:
    """Select one active intent whose complete lineage belongs to an authorized owner."""

    if not _PACK_ID.fullmatch(pack_id):
        raise ValueError("Owner publication pack ID is invalid")
    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT p.id AS publication_id,
                   d.id AS decision_id,
                   owner_auth.id AS owner_authorization_id,
                   p.pack_id,
                   p.record_id
            FROM publication_intents p
            JOIN contribution_drafts draft
              ON draft.id = p.source_draft_id
             AND draft.draft_version = p.source_draft_version
            JOIN governance_decisions d
              ON d.id = p.reviewed_decision_id
             AND d.source_draft_id = p.source_draft_id
             AND d.source_draft_version = p.source_draft_version
             AND d.pack_id = p.pack_id
            JOIN governance_owner_authorizations owner_auth
              ON owner_auth.id = d.owner_authorization_id
             AND owner_auth.actor_id = d.deciding_actor_id
             AND owner_auth.pack_id = d.pack_id
            WHERE p.pack_id = $1
              AND p.approving_actor_id = $2
              AND draft.user_id = $2
              AND d.contributor_actor_id = $2
              AND d.deciding_actor_id = $2
              AND d.outcome = 'approved'
              AND d.approval_mode = 'owner'
              AND owner_auth.role = 'owner'
              AND owner_auth.revoked_at IS NULL
              AND p.state = ANY($3::text[])
            ORDER BY p.created_at, p.id
            LIMIT 2
            """,
            pack_id,
            actor_id,
            list(_ACTIVE_STATES),
        )
    if not rows:
        raise OwnerPublicationSelectionError(
            "No active owner-approved publication matches this actor and pack"
        )
    if len(rows) != 1:
        raise OwnerPublicationSelectionError(
            "More than one active owner-approved publication matches this actor and pack"
        )
    row = rows[0]
    return OwnerPublicationSelection(
        publication_id=UUID(str(row["publication_id"])),
        decision_id=UUID(str(row["decision_id"])),
        owner_authorization_id=UUID(str(row["owner_authorization_id"])),
        pack_id=str(row["pack_id"]),
        record_id=str(row["record_id"]),
    )


def owner_activation_settings(settings: Settings, publication_id: UUID) -> Settings:
    """Validate an in-process, exact-intent claim configuration."""

    if (
        settings.publication_claims_enabled
        or settings.publication_continuous_claims_enabled
        or settings.publication_activation_ids
    ):
        raise ValueError("Owner publication requires persistent claims to be disabled")
    return Settings.model_validate(
        settings.model_dump()
        | {
            "publication_claims_enabled": True,
            "publication_continuous_claims_enabled": False,
            "publication_activation_ids": str(publication_id),
        }
    )


async def discover_owner_publication(
    settings: Settings,
    *,
    actor_id: UUID,
    pack_id: str,
    pool_factory: Callable[..., Awaitable[Any]] = asyncpg.create_pool,
) -> OwnerPublicationSelection:
    manifest = load_capacity_manifest(settings.database_capacity_manifest_path)
    budget = manifest.active_role_budget(ProcessRole.PUBLICATION)
    pool = await pool_factory(
        dsn=asyncpg_dsn(settings.process_database_url(ProcessRole.PUBLICATION)),
        min_size=1,
        max_size=1,
        timeout=budget.acquisition_timeout_ms / 1000,
        server_settings={
            "application_name": (
                f"opennosh:{manifest.deployment_id}:owner-publication-selection"[:63]
            ),
            "statement_timeout": str(budget.statement_timeout_ms),
        },
    )
    if pool is None:
        raise RuntimeError("asyncpg did not create the owner publication selection pool")
    try:
        return await select_owner_publication(pool, actor_id=actor_id, pack_id=pack_id)
    finally:
        await pool.close()


async def run_owner_publication(
    settings: Settings,
    *,
    actor_id: UUID,
    pack_id: str,
    timeout_seconds: float = 900,
    poll_seconds: float = 1,
    discover: Callable[..., Awaitable[OwnerPublicationSelection]] = discover_owner_publication,
    driver_factory: Callable[..., Awaitable[PgQueuerRoleDriver]] = (create_publication_role_driver),
) -> OwnerPublicationReport:
    """Claim one authorized owner publication, wait for its result, then exit."""

    if (
        not math.isfinite(timeout_seconds)
        or not math.isfinite(poll_seconds)
        or timeout_seconds <= 0
        or poll_seconds <= 0
    ):
        raise ValueError("Owner publication timeouts must be positive")
    selection: OwnerPublicationSelection | None = None
    shutdown: asyncio.Event | None = None
    supervisor: asyncio.Task[None] | None = None
    status: PublicationActivationStatus | None = None
    timeout = asyncio.timeout(timeout_seconds)
    try:
        try:
            async with timeout:
                selection = await discover(settings, actor_id=actor_id, pack_id=pack_id)
                activated = owner_activation_settings(settings, selection.publication_id)
                driver = await driver_factory(settings=activated)
                shutdown = asyncio.Event()
                supervisor = asyncio.create_task(
                    supervise_publication_claims(
                        driver,
                        shutdown,
                        drain_timeout_seconds=PUBLICATION_DRAIN_TIMEOUT_SECONDS,
                    ),
                    name="opennosh-owner-publication",
                )
                while True:
                    if supervisor.done():
                        await supervisor
                        raise RuntimeError(
                            "Owner publication worker exited without a terminal result"
                        )
                    status = await driver.publication_status(selection.publication_id)
                    if status.terminal:
                        break
                    await asyncio.sleep(poll_seconds)
        finally:
            if shutdown is not None and supervisor is not None:
                shutdown.set()
                await supervisor
    except TimeoutError as error:
        if timeout.expired():
            raise OwnerPublicationTimeoutError(
                "Owner publication did not reach a terminal state before timeout"
            ) from error
        raise
    assert selection is not None
    assert status is not None
    return _report(selection, status)


def _report(
    selection: OwnerPublicationSelection,
    status: PublicationActivationStatus,
) -> OwnerPublicationReport:
    if status.pack_id != selection.pack_id or status.record_id != selection.record_id:
        raise RuntimeError("Owner publication terminal result changed identity")
    if status.state.value == "published" and (
        status.published_at is None
        or status.receipt_digest is None
        or status.receipt_reference is None
    ):
        raise RuntimeError("Published owner publication is missing its signed receipt")
    published_at: datetime | None = status.published_at
    return OwnerPublicationReport(
        schema_version="1.0",
        publication_intent_id=str(selection.publication_id),
        governance_decision_id=str(selection.decision_id),
        owner_authorization_id=str(selection.owner_authorization_id),
        pack_id=status.pack_id,
        record_id=status.record_id,
        state=status.state.value,
        published_at=published_at.isoformat() if published_at is not None else None,
        receipt_digest=status.receipt_digest,
        receipt_reference=status.receipt_reference,
    )
