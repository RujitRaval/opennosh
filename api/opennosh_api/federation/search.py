"""Read-only adapter from verified federation projections into food search snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ActiveFederationProjection:
    checkpoint_id: UUID
    release_set_digest: str
    stale: bool
    quarantine_cutoff: datetime | None


async def active_federation_projection(
    database: AsyncSession,
) -> ActiveFederationProjection | None:
    row = (
        (
            await database.execute(
                text(
                    """
                    WITH active AS (
                        SELECT checkpoint.id, checkpoint.release_set_digest, checkpoint.mode
                        FROM federation_projection_activations AS activation
                        JOIN federation_projection_checkpoints AS checkpoint
                          ON checkpoint.id = activation.checkpoint_id
                        ORDER BY activation.activated_at DESC,
                                 activation.created_at DESC,
                                 activation.id DESC
                        LIMIT 1
                    ),
                    eligible_ranked AS (
                        SELECT
                            verified.id AS verified_release_id,
                            row_number() OVER (
                                PARTITION BY release.repository_id, release.pack_id
                                ORDER BY release.receipt_published_at DESC, release.id
                            ) AS scope_rank
                        FROM federation_verified_releases AS verified
                        JOIN federation_releases AS release
                          ON release.id = verified.release_id
                        JOIN federation_maintainers AS maintainer
                          ON maintainer.id = release.maintainer_id
                        WHERE maintainer.state = 'active'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM federation_release_status_events AS status
                              WHERE status.release_id = release.id
                                AND status.state = 'quarantined'
                          )
                    ),
                    installed_ranked AS (
                        SELECT
                            installation.verified_release_id,
                            installation.action,
                            row_number() OVER (
                                PARTITION BY installation.repository_id, installation.pack_id
                                ORDER BY installation.generation DESC
                            ) AS scope_rank
                        FROM federation_pack_installation_events AS installation
                    ),
                    actual AS (
                        SELECT member.verified_release_id
                        FROM federation_projection_releases AS member
                        JOIN active ON active.id = member.checkpoint_id
                    ),
                    expected AS (
                        SELECT verified_release_id
                        FROM eligible_ranked, active
                        WHERE scope_rank = 1 AND active.mode = 'registry'
                        UNION ALL
                        SELECT installed.verified_release_id
                        FROM installed_ranked AS installed
                        JOIN federation_verified_releases AS verified
                          ON verified.id = installed.verified_release_id
                        JOIN federation_releases AS release
                          ON release.id = verified.release_id
                        JOIN federation_maintainers AS maintainer
                          ON maintainer.id = release.maintainer_id
                        CROSS JOIN active
                        WHERE installed.scope_rank = 1
                          AND installed.action <> 'remove'
                          AND active.mode = 'installed'
                          AND maintainer.state = 'active'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM federation_release_status_events AS status
                              WHERE status.release_id = release.id
                                AND status.state = 'quarantined'
                          )
                    )
                    SELECT
                        active.id AS checkpoint_id,
                        active.release_set_digest,
                        EXISTS (
                            (SELECT verified_release_id FROM actual
                             EXCEPT SELECT verified_release_id FROM expected)
                            UNION ALL
                            (SELECT verified_release_id FROM expected
                             EXCEPT SELECT verified_release_id FROM actual)
                        ) AS stale,
                        (
                            SELECT max(status.occurred_at)
                            FROM federation_projection_releases AS member
                            JOIN federation_verified_releases AS verified
                              ON verified.id = member.verified_release_id
                            JOIN federation_release_status_events AS status
                              ON status.release_id = verified.release_id
                             AND status.state = 'quarantined'
                            WHERE member.checkpoint_id = active.id
                        ) AS quarantine_cutoff
                    FROM active
                    """
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return ActiveFederationProjection(
        checkpoint_id=row["checkpoint_id"],
        release_set_digest=str(row["release_set_digest"]),
        stale=bool(row["stale"]),
        quarantine_cutoff=row["quarantine_cutoff"],
    )


async def append_federation_projection(
    database: AsyncSession,
    *,
    snapshot_id: UUID,
    projection: ActiveFederationProjection,
    selected_pack_ids: tuple[str, ...],
) -> None:
    """Copy one complete active release set into an uncommitted retained snapshot."""
    await database.execute(
        text(
            """
            WITH candidates AS (
                SELECT
                    food.verified_release_id::text || ':' || food.source_record_id
                        AS source_id,
                    food.source_record_id,
                    food.name,
                    food.name_local,
                    lower(food.locale) AS locale,
                    food.category,
                    food.pack_license AS license,
                    food.source_uri,
                    food.source_license,
                    food.contributed_by,
                    food.pack_id,
                    food.pack_version,
                    food.provenance,
                    food.verified_release_id,
                    release.release_version,
                    release.statement_digest AS release_digest,
                    COALESCE(
                        food.equivalence_key,
                        'record:' || food.verified_release_id::text || ':' ||
                            food.source_record_id
                    ) AS equivalence_group_id,
                    'federation:' || food.verified_release_id::text || ':' ||
                        food.source_record_id AS variant_id,
                    food.nutrients_digest,
                    COALESCE(food.nutrients_digest, food.nutrients_json::text)
                        AS nutrients_identity
                FROM federation_projection_foods AS food
                JOIN federation_verified_releases AS verified
                  ON verified.id = food.verified_release_id
                JOIN federation_releases AS release
                  ON release.id = verified.release_id
                WHERE food.checkpoint_id = CAST(:checkpoint_id AS uuid)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM federation_release_status_events AS status
                      WHERE status.release_id = release.id
                        AND status.state = 'quarantined'
                  )
                  AND (
                      CAST(:has_pack_filter AS boolean) IS FALSE
                      OR food.pack_id = ANY(CAST(:selected_pack_ids AS text[]))
                  )
            ),
            groups AS (
                SELECT
                    equivalence_group_id,
                    count(*)::integer AS variant_count,
                    count(DISTINCT nutrients_identity) > 1 AS conflict
                FROM candidates
                GROUP BY equivalence_group_id
            )
            INSERT INTO food_search_snapshot_items (
                snapshot_id, source, source_id, source_record_id, name, name_local,
                locale, category, license, source_uri, source_license, contributed_by,
                pack_id, pack_version, provenance, verified_release_id,
                release_version, release_digest, equivalence_group_id, variant_id,
                nutrients_digest, conflict, variant_count
            )
            SELECT
                CAST(:snapshot_id AS uuid), 'federation', candidate.source_id,
                candidate.source_record_id, candidate.name, candidate.name_local,
                candidate.locale, candidate.category, candidate.license,
                candidate.source_uri, candidate.source_license,
                candidate.contributed_by, candidate.pack_id, candidate.pack_version,
                candidate.provenance, candidate.verified_release_id,
                candidate.release_version, candidate.release_digest,
                candidate.equivalence_group_id, candidate.variant_id,
                candidate.nutrients_digest, grouping.conflict, grouping.variant_count
            FROM candidates AS candidate
            JOIN groups AS grouping USING (equivalence_group_id)
            ORDER BY candidate.pack_id, candidate.source_record_id
            """
        ),
        {
            "checkpoint_id": projection.checkpoint_id,
            "has_pack_filter": bool(selected_pack_ids),
            "selected_pack_ids": list(selected_pack_ids),
            "snapshot_id": snapshot_id,
        },
    )


async def federation_food_detail(
    database: AsyncSession,
    source_id: str,
) -> RowMapping | None:
    release_text, separator, source_record_id = source_id.partition(":")
    if not separator or not source_record_id:
        return None
    try:
        verified_release_id = UUID(release_text)
    except ValueError:
        return None
    return (
        (
            await database.execute(
                text(
                    """
                    SELECT
                        'federation' AS source,
                        food.verified_release_id::text || ':' || food.source_record_id
                            AS source_id,
                        food.source_record_id,
                        food.name,
                        food.name_local,
                        food.category,
                        food.pack_license AS license,
                        food.source_uri,
                        food.source_license,
                        food.contributed_by,
                        food.pack_id,
                        food.pack_version,
                        food.provenance,
                        release.release_version,
                        release.statement_digest AS release_digest,
                        COALESCE(
                            food.equivalence_key,
                            'record:' || food.verified_release_id::text || ':' ||
                                food.source_record_id
                        ) AS equivalence_group_id,
                        'federation:' || food.verified_release_id::text || ':' ||
                            food.source_record_id AS variant_id,
                        CASE WHEN food.equivalence_key IS NULL THEN false ELSE (
                            SELECT count(DISTINCT COALESCE(
                                sibling.nutrients_digest,
                                sibling.nutrients_json::text
                            )) > 1
                            FROM federation_projection_foods AS sibling
                            WHERE sibling.checkpoint_id = food.checkpoint_id
                              AND sibling.equivalence_key = food.equivalence_key
                        ) END AS conflict,
                        CASE WHEN food.equivalence_key IS NULL THEN 1 ELSE (
                            SELECT count(*)::integer
                            FROM federation_projection_foods AS sibling
                            WHERE sibling.checkpoint_id = food.checkpoint_id
                              AND sibling.equivalence_key = food.equivalence_key
                        ) END AS variant_count,
                        food.nutrients_json,
                        food.portions_json
                    FROM federation_projection_foods AS food
                    JOIN federation_verified_releases AS verified
                      ON verified.id = food.verified_release_id
                    JOIN federation_releases AS release
                      ON release.id = verified.release_id
                    WHERE food.verified_release_id = CAST(:verified_release_id AS uuid)
                      AND food.source_record_id = :source_record_id
                    ORDER BY food.created_at DESC, food.id DESC
                    LIMIT 1
                    """
                ),
                {
                    "source_record_id": source_record_id,
                    "verified_release_id": verified_release_id,
                },
            )
        )
        .mappings()
        .first()
    )
