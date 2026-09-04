from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command as alembic_command
from opennosh_api.public_operations.contracts import (
    ComponentObservationEvidence,
    PublicIncidentEventInput,
)
from opennosh_api.public_operations.manifest import load_public_status_manifest
from opennosh_api.public_operations.service import (
    append_public_incident_event,
    create_public_incident,
    current_public_status,
    list_public_incidents,
    record_component_observation,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.tests.test_migrations import migration_config

INTEGRATION_DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL")
NOW = datetime(2026, 9, 4, 6, tzinfo=UTC)


def _event(state: str, *, minute: int, resolved: bool = False) -> PublicIncidentEventInput:
    return PublicIncidentEventInput(
        state=state,
        public_summary=f"Public search incident update {minute}.",
        affected_component_ids=("api", "search"),
        affected_versions=("0.92.0.0",),
        guidance="Use previously downloaded signed artifacts until recovery is verified.",
        occurred_at=NOW + timedelta(minutes=minute),
        recovery_evidence=(
            {
                "status": "verified",
                "observed_at": NOW + timedelta(minutes=minute),
                "content_sha256": "f" * 64,
            }
            if resolved
            else None
        ),
    )


async def _exercise_public_operations(database_url: str) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    manifest = load_public_status_manifest()
    incident_id = uuid4()
    evidence_digest = uuid4().hex * 2
    try:
        evidence = ComponentObservationEvidence(
            component_id="api",
            state="operational",
            successful=True,
            observed_at=NOW,
            evidence_digest=evidence_digest,
            affected_versions=("0.92.0.0",),
        )
        async with sessions() as session, session.begin():
            first = await record_component_observation(
                session,
                manifest=manifest,
                evidence=evidence,
            )
        async with sessions() as session, session.begin():
            replay = await record_component_observation(
                session,
                manifest=manifest,
                evidence=evidence,
            )
        assert first.id == replay.id

        async with sessions() as session, session.begin():
            await create_public_incident(
                session,
                manifest=manifest,
                incident_id=incident_id,
                title="Food search interruption",
                event=_event("investigating", minute=0),
            )
        for state, minute, resolved in (
            ("identified", 1, False),
            ("monitoring", 2, False),
            ("resolved", 3, True),
        ):
            async with sessions() as session, session.begin():
                await append_public_incident_event(
                    session,
                    manifest=manifest,
                    incident_id=incident_id,
                    event=_event(state, minute=minute, resolved=resolved),
                )

        async with sessions() as session:
            status = await current_public_status(
                session,
                manifest=manifest,
                now=NOW + timedelta(seconds=60),
            )
            assert status.components[0].state == "operational"
            assert all(component.state == "unknown" for component in status.components[1:])
            incidents = await list_public_incidents(session)
            assert len(incidents.incidents) == 1
            assert incidents.incidents[0].state == "resolved"
            assert incidents.incidents[0].resolved_at == NOW + timedelta(minutes=3)
            assert incidents.incidents[0].recovery_evidence is not None
            serialized = json.dumps(incidents.model_dump(mode="json")).lower()
            for forbidden in (
                "credential",
                "provider_resource_id",
                "hostname",
                "ip_address",
                "log_excerpt",
                "private_topology",
            ):
                assert forbidden not in serialized

        for statement in (
            "UPDATE public_component_observations SET state = 'outage' WHERE id = :id",
            "DELETE FROM public_incident_events WHERE incident_id = :id",
            "UPDATE public_incidents SET title = 'rewritten' WHERE id = :id",
        ):
            async with engine.begin() as connection:
                with pytest.raises(DBAPIError, match="append-only"):
                    await connection.execute(
                        text(statement),
                        {"id": first.id if "observations" in statement else incident_id},
                    )
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    INTEGRATION_DATABASE_URL is None,
    reason="INTEGRATION_DATABASE_URL is required for PostgreSQL integration tests",
)
def test_public_status_and_incident_evidence_are_append_only_and_safe() -> None:
    assert INTEGRATION_DATABASE_URL is not None
    alembic_command.upgrade(migration_config(INTEGRATION_DATABASE_URL), "head")
    asyncio.run(_exercise_public_operations(INTEGRATION_DATABASE_URL))
