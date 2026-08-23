from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from opennosh_api.capacity import ProcessRole


@dataclass(frozen=True)
class RoleComposition:
    role: ProcessRole
    lanes: frozenset[str]
    adapters: frozenset[str]
    database_url_environment: str
    liveness_contract: str = "process_running"
    readiness_contract: str = "capacity_valid_and_database_connected"


ROLE_COMPOSITIONS: dict[ProcessRole, RoleComposition] = {
    ProcessRole.WEB: RoleComposition(
        role=ProcessRole.WEB,
        lanes=frozenset(),
        adapters=frozenset({"http", "food_sources"}),
        database_url_environment="WEB_DATABASE_URL",
    ),
    ProcessRole.PUBLICATION: RoleComposition(
        role=ProcessRole.PUBLICATION,
        lanes=frozenset({"publication"}),
        adapters=frozenset({"job_queue", "forge", "signer"}),
        database_url_environment="PUBLICATION_DATABASE_URL",
    ),
    ProcessRole.EVIDENCE: RoleComposition(
        role=ProcessRole.EVIDENCE,
        lanes=frozenset({"evidence_processing"}),
        adapters=frozenset({"job_queue", "evidence_store"}),
        database_url_environment="EVIDENCE_DATABASE_URL",
    ),
    ProcessRole.PROJECTION: RoleComposition(
        role=ProcessRole.PROJECTION,
        lanes=frozenset({"pack_ingestion", "projection"}),
        adapters=frozenset({"job_queue", "search_projection"}),
        database_url_environment="PROJECTION_DATABASE_URL",
    ),
    ProcessRole.RECONCILER: RoleComposition(
        role=ProcessRole.RECONCILER,
        lanes=frozenset({"reconciliation"}),
        adapters=frozenset({"job_queue", "forge", "evidence_store"}),
        database_url_environment="RECONCILER_DATABASE_URL",
    ),
    ProcessRole.SCHEDULER: RoleComposition(
        role=ProcessRole.SCHEDULER,
        lanes=frozenset({"notification", "scheduling"}),
        adapters=frozenset({"job_queue", "clock"}),
        database_url_environment="SCHEDULER_DATABASE_URL",
    ),
}


class DrainableRoleDriver(Protocol):
    async def start(self) -> None: ...

    def stop_claiming(self) -> None: ...

    async def drain(self) -> None: ...

    async def close(self) -> None: ...


async def supervise_role(
    driver: DrainableRoleDriver,
    shutdown_requested: asyncio.Event,
    *,
    drain_timeout_seconds: float,
) -> None:
    try:
        await driver.start()
        await shutdown_requested.wait()
        driver.stop_claiming()
        async with asyncio.timeout(drain_timeout_seconds):
            await driver.drain()
    finally:
        await driver.close()


def role_accepts_lane(role: ProcessRole, lane: str) -> bool:
    return lane in ROLE_COMPOSITIONS[role].lanes
