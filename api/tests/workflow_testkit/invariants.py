from __future__ import annotations

from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
from opennosh_api.publication.state import PublicationStepName

from api.tests.workflow_testkit.external import PersistentExternalState


async def assert_publication_trust_invariants(
    pool: asyncpg.Pool,
    publication_id: UUID,
    external_state: PersistentExternalState,
) -> None:
    row = await pool.fetchrow(
        """
        SELECT intent.state,
               (SELECT count(*) FROM publication_steps
                WHERE publication_intent_id = intent.id) AS step_count,
               (SELECT count(*) FROM publication_steps
                WHERE publication_intent_id = intent.id AND state = 'verified') AS verified_count,
               (SELECT count(*) FROM publication_durable_acknowledgements
                WHERE publication_intent_id = intent.id) AS acknowledgement_count,
               (SELECT count(*) FROM accepted_events
                WHERE publication_intent_id = intent.id) AS accepted_count
        FROM publication_intents AS intent
        WHERE intent.id = $1
        """,
        publication_id,
    )
    if row is None:
        raise AssertionError("Publication intent disappeared during scenario")
    if int(row["step_count"]) != len(PublicationStepName):
        raise AssertionError("Publication step registry is incomplete")
    if int(row["accepted_count"]) > 1:
        raise AssertionError("More than one canonical accepted release exists")

    step_rows = await pool.fetch(
        """
        SELECT ordinal, step_name, state
        FROM publication_steps
        WHERE publication_intent_id = $1
        ORDER BY ordinal
        """,
        publication_id,
    )
    if tuple(int(step["ordinal"]) for step in step_rows) != tuple(range(len(PublicationStepName))):
        raise AssertionError("Publication protocol ordinals are not contiguous")
    if tuple(str(step["step_name"]) for step in step_rows) != tuple(
        step.value for step in PublicationStepName
    ):
        raise AssertionError("Publication protocol order diverged from the registry")
    first_unverified = next(
        (index for index, step in enumerate(step_rows) if str(step["state"]) != "verified"),
        len(step_rows),
    )
    if any(str(step["state"]) == "verified" for step in step_rows[first_unverified:]):
        raise AssertionError("Publication crossed an unsupported out-of-order transition")

    acknowledgement_steps = {
        str(value["acknowledgement_kind"])
        for value in await pool.fetch(
            """
            SELECT acknowledgement_kind
            FROM publication_durable_acknowledgements
            WHERE publication_intent_id = $1
            """,
            publication_id,
        )
    }
    verified_steps = {
        str(step["step_name"]) for step in step_rows if str(step["state"]) == "verified"
    }
    if acknowledgement_steps != verified_steps:
        raise AssertionError("Verified steps and durable evidence do not agree")

    for count in external_state.snapshot().apply_counts.values():
        if count > 1:
            raise AssertionError("A governed external effect was applied more than once")

    receipt_rows = await pool.fetch(
        """
        SELECT acknowledgement_kind, content_digest
        FROM publication_durable_acknowledgements
        WHERE publication_intent_id = $1
          AND acknowledgement_kind IN (
              'sign_receipt', 'publish_receipt_registry', 'copy_receipt'
          )
        """,
        publication_id,
    )
    if receipt_rows and len({str(receipt["content_digest"]) for receipt in receipt_rows}) != 1:
        raise AssertionError("Receipt acknowledgement lineage has divergent digests")

    if str(row["state"]) == "published":
        if int(row["verified_count"]) != len(PublicationStepName):
            raise AssertionError("Publication is falsely PUBLISHED before every step verifies")
        if int(row["acknowledgement_count"]) != len(PublicationStepName):
            raise AssertionError("Published release lacks complete required evidence")
        if len(receipt_rows) != 3:
            raise AssertionError("Published release lacks one valid receipt lineage")
        durable_copy_count = await pool.fetchval(
            """
            SELECT count(*) FROM publication_durable_acknowledgements
            WHERE publication_intent_id = $1
              AND acknowledgement_kind IN (
                  'copy_commit', 'copy_evidence', 'copy_release', 'copy_receipt'
              )
            """,
            publication_id,
        )
        if int(durable_copy_count or 0) != 4:
            raise AssertionError("Published release lacks verified durable copies")
