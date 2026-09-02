from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from opennosh_api.capacity import ProcessRole, load_capacity_manifest
from opennosh_api.publication.readiness import collect_production_claims_readiness
from opennosh_api.settings import Settings


class NaturalActivationTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_uploads_enabled: Literal[True]
    evidence_sanitization_enabled: Literal[True]
    evidence_replicas: Literal[1]
    governance_steward_ui_enabled: Literal[True]
    governance_mutations_enabled: Literal[True]
    governance_public_decisions_enabled: Literal[True]
    web_governance_ui_enabled: Literal[True]
    public_artifact_reads_enabled: Literal[True]
    publication_claims_enabled: Literal[True]
    publication_continuous_claims_enabled: Literal[True]
    publication_claim_concurrency: Literal[1]
    publication_replicas: Literal[1]
    observation_seconds: Literal[1800]


class NaturalRollbackTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_uploads_enabled: Literal[False]
    evidence_sanitization_enabled: Literal[False]
    evidence_replicas: Literal[0]
    governance_steward_ui_enabled: Literal[False]
    governance_mutations_enabled: Literal[False]
    governance_public_decisions_enabled: Literal[False]
    web_governance_ui_enabled: Literal[False]
    public_artifact_reads_enabled: Literal[False]
    publication_claims_enabled: Literal[False]
    publication_continuous_claims_enabled: Literal[False]
    publication_activation_id_present: Literal[False]
    latest_refresh_enabled: Literal[True]
    recovery_seconds: Literal[300]


class NaturalPublicationActivationContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"]
    activation: NaturalActivationTarget
    rollback: NaturalRollbackTarget


def default_natural_activation_contract_path() -> Path:
    packaged = Path(
        str(
            resources.files("opennosh_api").joinpath("natural-publication-proof-activation.v1.json")
        )
    )
    if packaged.is_file():
        return packaged
    return (
        Path(__file__).resolve().parents[3] / "config/natural-publication-proof-activation.v1.json"
    )


def load_natural_activation_contract(
    path: str | Path | None = None,
) -> NaturalPublicationActivationContract:
    target = Path(path) if path is not None else default_natural_activation_contract_path()
    return NaturalPublicationActivationContract.model_validate_json(target.read_bytes())


def natural_readiness_digest(payload: Mapping[str, object]) -> str:
    unsigned = dict(payload)
    unsigned.pop("readiness_sha256", None)
    return hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _blueprint_flags(path: Path) -> dict[str, bool]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), list):
        raise ValueError("Render Blueprint services are invalid")
    services = {
        item.get("name"): item
        for item in payload["services"]
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }

    def value(service_name: str, key: str) -> bool:
        service = services.get(service_name)
        if not isinstance(service, dict) or not isinstance(service.get("envVars"), list):
            raise ValueError("Render Blueprint service is missing")
        matches = [
            item.get("value")
            for item in service["envVars"]
            if isinstance(item, dict) and item.get("key") == key
        ]
        if len(matches) != 1 or not isinstance(matches[0], str):
            raise ValueError("Render Blueprint feature flag is not an explicit boolean")
        selected = matches[0]
        if selected not in {"true", "false"}:
            raise ValueError("Render Blueprint feature flag is not an explicit boolean")
        return selected == "true"

    return {
        "evidence_uploads_enabled": value("opennosh-api", "EVIDENCE_UPLOADS_ENABLED"),
        "evidence_sanitization_enabled": value("opennosh-api", "EVIDENCE_SANITIZATION_ENABLED"),
        "governance_steward_ui_enabled": value("opennosh-api", "GOVERNANCE_STEWARD_UI_ENABLED"),
        "governance_mutations_enabled": value("opennosh-api", "GOVERNANCE_MUTATIONS_ENABLED"),
        "governance_public_decisions_enabled": value(
            "opennosh-api", "GOVERNANCE_PUBLIC_DECISIONS_ENABLED"
        ),
        "web_governance_ui_enabled": value(
            "opennosh-web", "OPENNOSH_GOVERNANCE_STEWARD_UI_ENABLED"
        ),
        "public_artifact_reads_enabled": value("opennosh-web", "PUBLIC_ARTIFACT_READS_ENABLED"),
        "publication_claims_enabled": value("opennosh-publication", "PUBLICATION_CLAIMS_ENABLED"),
        "publication_continuous_claims_enabled": value(
            "opennosh-publication", "PUBLICATION_CONTINUOUS_CLAIMS_ENABLED"
        ),
        "federation_ingestion_enabled": value(
            "opennosh-publication", "FEDERATION_INGESTION_ENABLED"
        ),
        "federation_projection_enabled": value(
            "opennosh-publication", "FEDERATION_PROJECTION_ENABLED"
        ),
        "federation_search_enabled": value("opennosh-publication", "FEDERATION_SEARCH_ENABLED"),
        "federation_installation_enabled": value(
            "opennosh-publication", "FEDERATION_INSTALLATION_ENABLED"
        ),
        "federation_public_discovery_enabled": value(
            "opennosh-publication", "FEDERATION_PUBLIC_DISCOVERY_ENABLED"
        ),
    }


def build_natural_publication_readiness(
    settings: Settings,
    claims_report: Mapping[str, Any],
    *,
    blueprint_path: Path,
    contract: NaturalPublicationActivationContract,
    observed_at: datetime,
) -> dict[str, object]:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Natural readiness observation time must include a timezone")
    flags = _blueprint_flags(blueprint_path)
    capacity = load_capacity_manifest(settings.database_capacity_manifest_path)
    publication = capacity.active_role_budget(ProcessRole.PUBLICATION)
    evidence = capacity.roles[ProcessRole.EVIDENCE]
    queue = claims_report.get("queue")
    runtime = claims_report.get("runtime")
    failures: list[str] = []
    if claims_report.get("status") != "ready":
        failures.append("production_claims_readiness_not_ready")
    if not isinstance(queue, Mapping) or any(
        queue.get(field) != 0 for field in ("active", "picked", "claimable")
    ):
        failures.append("publication_queue_not_idle")
    if (
        not isinstance(runtime, Mapping)
        or runtime.get("deployed_commit") != settings.render_git_commit
    ):
        failures.append("deployed_commit_mismatch")
    failures.extend(f"{name}_already_enabled" for name, enabled in flags.items() if enabled)
    if evidence.replicas != 0:
        failures.append("evidence_capacity_not_zero")
    if publication.replicas != 1:
        failures.append("publication_replica_count_not_one")
    if settings.publication_claim_concurrency != 1:
        failures.append("publication_concurrency_not_one")
    report: dict[str, object] = {
        "schema_version": "1.0",
        "status": "ready" if not failures else "blocked",
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "deployed_commit": settings.render_git_commit,
        "production_claims_readiness_sha256": claims_report.get("readiness_sha256"),
        "disabled_flags": flags,
        "capacity": {
            "evidence_replicas": evidence.replicas,
            "publication_replicas": publication.replicas,
            "publication_claim_concurrency": settings.publication_claim_concurrency,
        },
        "activation_candidate": contract.model_dump(mode="json"),
        "failures": sorted(set(failures)),
    }
    report["readiness_sha256"] = natural_readiness_digest(report)
    return report


async def collect_natural_publication_readiness(
    settings: Settings,
    *,
    blueprint_path: Path,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    observation = observed_at or datetime.now(UTC)
    claims_report = await collect_production_claims_readiness(
        settings,
        observed_at=observation,
    )
    return build_natural_publication_readiness(
        settings,
        claims_report,
        blueprint_path=blueprint_path,
        contract=load_natural_activation_contract(),
        observed_at=observation,
    )


__all__ = [
    "NaturalPublicationActivationContract",
    "build_natural_publication_readiness",
    "collect_natural_publication_readiness",
    "load_natural_activation_contract",
    "natural_readiness_digest",
]
