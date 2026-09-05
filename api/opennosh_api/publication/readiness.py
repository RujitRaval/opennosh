from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import make_url

from opennosh_api.capacity import ProcessRole, load_capacity_manifest
from opennosh_api.jobs.pgqueuer import PUBLICATION_ENTRYPOINT, decode_message
from opennosh_api.public_operations.manifest import load_public_status_manifest
from opennosh_api.publication.credentials import validate_publication_claim_credentials
from opennosh_api.publication.state import PublicationState
from opennosh_api.settings import Settings

_SHA256 = r"^[0-9a-f]{64}$"
_GIT_COMMIT = r"^[0-9a-f]{40}([0-9a-f]{24})?$"
_QUEUE_STATES = frozenset(
    {"queued", "picked", "successful", "exception", "canceled", "deleted", "failed"}
)
_FEDERATION_STATES = frozenset({"requested", "verified", "active", "quarantined", "revoked"})
_LIVING_COMMONS_MIGRATION = "20260905_0037"
_LIVING_COMMONS_FLAGS = (
    "reuse_registry_mutations_enabled",
    "reuse_verification_enabled",
    "reuse_public_enabled",
    "impact_aggregation_enabled",
    "impact_public_enabled",
    "public_status_enabled",
)


class ReadinessConnection(Protocol):
    async def fetch(self, query: str, *arguments: object) -> list[Mapping[str, Any]]: ...


class T335Evidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    issue: Literal[130]
    proof_sha256: str = Field(pattern=_SHA256)


class ActivationBaseline(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_commit: str = Field(pattern=_GIT_COMMIT)
    public_origin: Literal["https://opennosh.org"]
    release_version: str = Field(pattern=r"^\d+\.\d+\.\d+\.\d+$")
    publication_id: UUID
    manifest_sha256: str = Field(pattern=_SHA256)
    receipt_sha256: str = Field(pattern=_SHA256)
    pointer_key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ActivationTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["continuous"]
    replicas: Literal[1]
    claim_concurrency: Literal[1]
    observation_seconds: Literal[1800]


class RollbackTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claims_enabled: Literal[False]
    continuous_claims_enabled: Literal[False]
    activation_id_present: Literal[False]
    latest_refresh_enabled: Literal[True]
    recovery_seconds: Literal[300]


class PublicationClaimsActivationContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"]
    t33_5: T335Evidence
    baseline: ActivationBaseline
    activation: ActivationTarget
    rollback: RollbackTarget


def load_activation_contract(path: str | Path) -> PublicationClaimsActivationContract:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return PublicationClaimsActivationContract.model_validate(payload)


def default_activation_contract_path() -> Path:
    packaged = Path(
        str(resources.files("opennosh_api").joinpath("publication-claims-activation.v1.json"))
    )
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[3] / "config/publication-claims-activation.v1.json"


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def readiness_digest(payload: Mapping[str, object]) -> str:
    unsigned = dict(payload)
    unsigned.pop("readiness_sha256", None)
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def default_readiness_schema_path() -> Path:
    packaged = Path(
        str(resources.files("opennosh_api").joinpath("publication-readiness.schema.json"))
    )
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[3] / "schemas/publication-readiness.schema.json"


def default_impact_metrics_path() -> Path:
    packaged = Path(str(resources.files("opennosh_api").joinpath("impact-metrics.v1.json")))
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[3] / "config/impact-metrics.v1.json"


def _json_digest(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def validate_readiness_report(report: Mapping[str, object]) -> None:
    schema = json.loads(default_readiness_schema_path().read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
    if report.get("readiness_sha256") != readiness_digest(report):
        raise ValueError("Readiness report digest does not match canonical content")


def _postgres_dsn(database_url: str) -> str:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("Production claims readiness requires PostgreSQL")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _counts(
    rows: list[Mapping[str, Any]], *, allowed: frozenset[str]
) -> tuple[dict[str, int], bool]:
    counts = {state: 0 for state in sorted(allowed)}
    unknown = False
    for row in rows:
        state = str(row["state"])
        if state not in allowed:
            unknown = True
            continue
        counts[state] = int(row["count"])
    return counts, unknown


async def build_production_claims_readiness(
    connection: ReadinessConnection,
    settings: Settings,
    contract: PublicationClaimsActivationContract,
    *,
    observed_at: datetime,
) -> dict[str, object]:
    """Build a deterministic, redacted, read-only activation readiness report."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Readiness observation time must include a timezone")
    observed_at = observed_at.astimezone(UTC)
    failures: list[str] = []
    manifest = load_capacity_manifest(settings.database_capacity_manifest_path)
    budget = manifest.active_role_budget(ProcessRole.PUBLICATION)
    if settings.app_environment != "production":
        failures.append("app_environment_not_production")
    if settings.process_role is not ProcessRole.PUBLICATION:
        failures.append("process_role_not_publication")
    if settings.publication_claims_enabled:
        failures.append("claims_already_enabled")
    if settings.publication_continuous_claims_enabled:
        failures.append("continuous_claims_already_enabled")
    if settings.publication_activation_ids:
        failures.append("activation_id_present")
    if settings.publication_preactivation_smoke_enabled:
        failures.append("preactivation_smoke_enabled")
    if settings.federation_ingestion_enabled:
        failures.append("federation_ingestion_enabled")
    if settings.federation_projection_enabled:
        failures.append("federation_projection_enabled")
    if settings.federation_search_enabled:
        failures.append("federation_search_enabled")
    if settings.federation_installation_enabled:
        failures.append("federation_installation_enabled")
    if settings.federation_public_discovery_enabled:
        failures.append("federation_public_discovery_enabled")
    if settings.mission_mutations_enabled:
        failures.append("mission_mutations_enabled")
    if settings.mission_projection_enabled:
        failures.append("mission_projection_enabled")
    if settings.mission_public_enabled:
        failures.append("mission_public_enabled")
    if settings.mission_activity_map_enabled:
        failures.append("mission_activity_map_enabled")
    if settings.mission_pack_release_enabled:
        failures.append("mission_pack_release_enabled")
    for flag in _LIVING_COMMONS_FLAGS:
        if bool(getattr(settings, flag)):
            failures.append(flag)
    if not settings.latest_refresh_enabled:
        failures.append("latest_refresh_disabled")
    if settings.publication_claim_concurrency != contract.activation.claim_concurrency:
        failures.append("claim_concurrency_not_pinned")
    if settings.publication_claim_concurrency > budget.max_in_flight_database_sections:
        failures.append("claim_concurrency_exceeds_capacity")
    if budget.replicas != contract.activation.replicas:
        failures.append("publication_replica_count_not_pinned")
    deployed_commit = settings.render_git_commit or ""
    if re.fullmatch(_GIT_COMMIT, deployed_commit) is None:
        failures.append("deployed_commit_missing_or_invalid")
    credentials_complete = True
    try:
        validate_publication_claim_credentials(settings)
    except ValueError:
        credentials_complete = False
        failures.append("claim_credentials_incomplete")

    queue_rows = await connection.fetch(
        """
        SELECT status::text AS state, count(*)::bigint AS count
        FROM opennosh_pgqueuer
        WHERE entrypoint = $1
        GROUP BY status
        ORDER BY status
        """,
        PUBLICATION_ENTRYPOINT,
    )
    queue_counts, unknown_queue = _counts(queue_rows, allowed=_QUEUE_STATES)
    if unknown_queue:
        failures.append("unknown_queue_state")

    active_rows = await connection.fetch(
        """
        SELECT status::text AS state, execute_after, heartbeat, payload
        FROM opennosh_pgqueuer
        WHERE entrypoint = $1 AND status IN ('queued', 'picked')
        ORDER BY id
        """,
        PUBLICATION_ENTRYPOINT,
    )
    claimable = 0
    picked = 0
    oldest_at: datetime | None = None
    for row in active_rows:
        state = str(row["state"])
        execute_after = row["execute_after"]
        heartbeat = row["heartbeat"]
        try:
            message = decode_message(bytes(row["payload"]))
        except (TypeError, ValueError):
            failures.append("unclassifiable_active_queue_payload")
            continue
        if message.job_type != "publication.wake":
            failures.append("unexpected_active_queue_job_type")
        eligible = state == "queued" and execute_after <= observed_at
        if state == "picked":
            picked += 1
            eligible = bool(
                execute_after <= observed_at
                and heartbeat is not None
                and heartbeat < observed_at - timedelta(seconds=30)
            )
        if eligible:
            claimable += 1
            oldest_at = execute_after if oldest_at is None else min(oldest_at, execute_after)
    if picked:
        failures.append("picked_publication_work_present")

    intent_rows = await connection.fetch(
        """
        SELECT state, count(*)::bigint AS count
        FROM publication_intents
        GROUP BY state
        ORDER BY state
        """
    )
    intent_counts, unknown_intent = _counts(
        intent_rows,
        allowed=frozenset(state.value for state in PublicationState),
    )
    if unknown_intent:
        failures.append("unknown_publication_state")

    federation_rows = await connection.fetch(
        """
        SELECT state, count(*)::bigint AS count
        FROM federation_maintainers
        GROUP BY state
        ORDER BY state
        """
    )
    federation_counts, unknown_federation = _counts(
        federation_rows,
        allowed=_FEDERATION_STATES,
    )
    if unknown_federation:
        failures.append("unknown_federation_state")
    migration_rows = await connection.fetch("SELECT version_num FROM alembic_version")
    migration_heads = sorted(str(row["version_num"]) for row in migration_rows)
    if migration_heads != [_LIVING_COMMONS_MIGRATION]:
        failures.append("living_commons_migration_not_current")
    status_manifest = load_public_status_manifest(settings.public_status_manifest_path)
    process_role = settings.process_role

    report: dict[str, object] = {
        "schema_version": "1.0",
        "status": "ready" if not failures else "blocked",
        "observed_at": observed_at.isoformat(),
        "runtime": {
            "app_environment": settings.app_environment,
            "process_role": process_role.value if process_role is not None else None,
            "claims_enabled": settings.publication_claims_enabled,
            "continuous_claims_enabled": settings.publication_continuous_claims_enabled,
            "activation_id_present": bool(settings.publication_activation_ids),
            "preactivation_smoke_enabled": settings.publication_preactivation_smoke_enabled,
            "federation_ingestion_enabled": settings.federation_ingestion_enabled,
            "federation_projection_enabled": settings.federation_projection_enabled,
            "federation_search_enabled": settings.federation_search_enabled,
            "federation_installation_enabled": settings.federation_installation_enabled,
            "federation_public_discovery_enabled": settings.federation_public_discovery_enabled,
            "mission_mutations_enabled": settings.mission_mutations_enabled,
            "mission_projection_enabled": settings.mission_projection_enabled,
            "mission_public_enabled": settings.mission_public_enabled,
            "mission_activity_map_enabled": settings.mission_activity_map_enabled,
            "mission_pack_release_enabled": settings.mission_pack_release_enabled,
            **{flag: bool(getattr(settings, flag)) for flag in _LIVING_COMMONS_FLAGS},
            "claim_concurrency": settings.publication_claim_concurrency,
            "latest_refresh_enabled": settings.latest_refresh_enabled,
            "credentials_complete": credentials_complete,
            "publication_replicas": budget.replicas,
            "capacity_max_in_flight": budget.max_in_flight_database_sections,
            "deployed_commit": deployed_commit or None,
        },
        "queue": {
            "counts": queue_counts,
            "active": len(active_rows),
            "picked": picked,
            "claimable": claimable,
            "oldest_claimable_age_seconds": (
                max(0, int((observed_at - oldest_at).total_seconds()))
                if oldest_at is not None
                else None
            ),
        },
        "publication_intents": {"counts": intent_counts},
        "federation_scopes": {"counts": federation_counts},
        "living_commons": {
            "migration_heads": migration_heads,
            "expected_migration_head": _LIVING_COMMONS_MIGRATION,
            "all_capabilities_disabled": not any(
                bool(getattr(settings, flag)) for flag in _LIVING_COMMONS_FLAGS
            ),
            "impact_metric_manifest_sha256": _json_digest(default_impact_metrics_path()),
            "public_status_manifest_sha256": status_manifest.digest,
            "public_status_component_ids": [
                component.component_id for component in status_manifest.components
            ],
        },
        "activation_candidate": contract.model_dump(mode="json"),
        "failures": sorted(set(failures)),
    }
    report["readiness_sha256"] = readiness_digest(report)
    validate_readiness_report(report)
    return report


async def collect_production_claims_readiness(
    settings: Settings,
    *,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    contract_path = settings.publication_claims_activation_contract_path
    if contract_path == Path("config/publication-claims-activation.v1.json"):
        contract_path = default_activation_contract_path()
    contract = load_activation_contract(contract_path)
    connection = await asyncpg.connect(
        _postgres_dsn(settings.process_database_url(ProcessRole.PUBLICATION))
    )
    try:
        return await build_production_claims_readiness(
            connection,
            settings,
            contract,
            observed_at=observed_at or datetime.now(UTC),
        )
    finally:
        await connection.close()
