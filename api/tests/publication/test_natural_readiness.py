from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import opennosh_api.publication.natural_readiness as natural_readiness
import pytest
import yaml
from opennosh_api.capacity import ProcessRole
from opennosh_api.publication.natural_readiness import (
    build_natural_publication_readiness,
    collect_natural_publication_readiness,
    default_natural_activation_contract_path,
    load_natural_activation_contract,
    natural_readiness_digest,
)
from opennosh_api.settings import Settings

ROOT = Path(__file__).resolve().parents[3]
OBSERVED_AT = datetime(2026, 9, 1, 23, 30, tzinfo=UTC)
COMMIT = "0" * 40


def settings() -> Settings:
    return Settings.model_construct(
        app_environment="production",
        process_role=ProcessRole.PUBLICATION,
        publication_claims_enabled=False,
        publication_continuous_claims_enabled=False,
        publication_claim_concurrency=1,
        publication_preactivation_smoke_enabled=False,
        federation_ingestion_enabled=False,
        federation_projection_enabled=False,
        federation_search_enabled=False,
        publication_activation_ids="",
        latest_refresh_enabled=True,
        render_git_commit=COMMIT,
        database_capacity_manifest_path=ROOT / "config/database-capacity.v1.json",
    )


def claims_report() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": "ready",
        "readiness_sha256": "a" * 64,
        "runtime": {"deployed_commit": COMMIT},
        "queue": {"active": 0, "picked": 0, "claimable": 0},
    }


def test_disabled_readiness_is_deterministic_and_binds_activation_contract() -> None:
    contract = load_natural_activation_contract(
        ROOT / "config/natural-publication-proof-activation.v1.json"
    )

    first = build_natural_publication_readiness(
        settings(),
        claims_report(),
        blueprint_path=ROOT / "render.yaml",
        contract=contract,
        observed_at=OBSERVED_AT,
    )
    second = build_natural_publication_readiness(
        settings(),
        claims_report(),
        blueprint_path=ROOT / "render.yaml",
        contract=contract,
        observed_at=OBSERVED_AT,
    )

    assert first == second
    assert first["status"] == "ready"
    assert first["failures"] == []
    assert first["readiness_sha256"] == natural_readiness_digest(first)
    assert set(first["disabled_flags"].values()) == {False}
    assert first["capacity"] == {
        "evidence_replicas": 0,
        "publication_replicas": 1,
        "publication_claim_concurrency": 1,
    }
    rendered = json.dumps(first, sort_keys=True)
    assert "DATABASE_URL" not in rendered
    assert "secret" not in rendered.casefold()


@pytest.mark.parametrize(
    ("service", "key", "failure"),
    [
        ("opennosh-api", "EVIDENCE_UPLOADS_ENABLED", "evidence_uploads_enabled_already_enabled"),
        (
            "opennosh-api",
            "GOVERNANCE_MUTATIONS_ENABLED",
            "governance_mutations_enabled_already_enabled",
        ),
        (
            "opennosh-web",
            "PUBLIC_ARTIFACT_READS_ENABLED",
            "public_artifact_reads_enabled_already_enabled",
        ),
        (
            "opennosh-publication",
            "PUBLICATION_CLAIMS_ENABLED",
            "publication_claims_enabled_already_enabled",
        ),
        (
            "opennosh-publication",
            "FEDERATION_INGESTION_ENABLED",
            "federation_ingestion_enabled_already_enabled",
        ),
        (
            "opennosh-publication",
            "FEDERATION_PROJECTION_ENABLED",
            "federation_projection_enabled_already_enabled",
        ),
        (
            "opennosh-publication",
            "FEDERATION_SEARCH_ENABLED",
            "federation_search_enabled_already_enabled",
        ),
        (
            "opennosh-publication",
            "FEDERATION_INSTALLATION_ENABLED",
            "federation_installation_enabled_already_enabled",
        ),
        (
            "opennosh-publication",
            "FEDERATION_PUBLIC_DISCOVERY_ENABLED",
            "federation_public_discovery_enabled_already_enabled",
        ),
    ],
)
def test_readiness_blocks_if_any_production_surface_is_enabled(
    tmp_path: Path,
    service: str,
    key: str,
    failure: str,
) -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    target = next(item for item in blueprint["services"] if item["name"] == service)
    variable = next(item for item in target["envVars"] if item.get("key") == key)
    variable["value"] = "true"
    path = tmp_path / "render.yaml"
    path.write_text(yaml.safe_dump(blueprint), encoding="utf-8")

    report = build_natural_publication_readiness(
        settings(),
        claims_report(),
        blueprint_path=path,
        contract=load_natural_activation_contract(
            ROOT / "config/natural-publication-proof-activation.v1.json"
        ),
        observed_at=OBSERVED_AT,
    )

    assert report["status"] == "blocked"
    assert failure in report["failures"]


def test_readiness_blocks_non_idle_queue_and_claims_probe() -> None:
    claims = claims_report()
    claims["status"] = "blocked"
    claims["queue"] = {"active": 1, "picked": 1, "claimable": 1}

    report = build_natural_publication_readiness(
        settings(),
        claims,
        blueprint_path=ROOT / "render.yaml",
        contract=load_natural_activation_contract(
            ROOT / "config/natural-publication-proof-activation.v1.json"
        ),
        observed_at=OBSERVED_AT,
    )

    assert report["status"] == "blocked"
    assert report["failures"] == [
        "production_claims_readiness_not_ready",
        "publication_queue_not_idle",
    ]


def test_activation_contract_rejects_drift(tmp_path: Path) -> None:
    payload = json.loads((ROOT / "config/natural-publication-proof-activation.v1.json").read_text())
    payload["activation"]["publication_claim_concurrency"] = 2
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_natural_activation_contract(path)


def test_default_contract_path_is_packaged_and_loadable() -> None:
    path = default_natural_activation_contract_path()

    assert path.is_file()
    assert load_natural_activation_contract().schema_version == "1.0"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"services": []},
        {"services": [{"name": "opennosh-api", "envVars": []}]},
    ],
)
def test_blueprint_shape_errors_fail_closed(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "render.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Blueprint"):
        build_natural_publication_readiness(
            settings(),
            claims_report(),
            blueprint_path=path,
            contract=load_natural_activation_contract(
                ROOT / "config/natural-publication-proof-activation.v1.json"
            ),
            observed_at=OBSERVED_AT,
        )


def test_readiness_rejects_naive_time_and_runtime_drift() -> None:
    contract = load_natural_activation_contract(
        ROOT / "config/natural-publication-proof-activation.v1.json"
    )
    with pytest.raises(ValueError, match="timezone"):
        build_natural_publication_readiness(
            settings(),
            claims_report(),
            blueprint_path=ROOT / "render.yaml",
            contract=contract,
            observed_at=datetime(2026, 9, 1),
        )

    unsafe_settings = settings().model_copy(
        update={"render_git_commit": "f" * 40, "publication_claim_concurrency": 2}
    )
    report = build_natural_publication_readiness(
        unsafe_settings,
        claims_report(),
        blueprint_path=ROOT / "render.yaml",
        contract=contract,
        observed_at=OBSERVED_AT,
    )
    assert {"deployed_commit_mismatch", "publication_concurrency_not_one"}.issubset(
        set(report["failures"])
    )


@pytest.mark.asyncio
async def test_readiness_collector_reuses_one_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[datetime] = []

    async def collect(_settings: Settings, *, observed_at: datetime) -> dict[str, object]:
        observed.append(observed_at)
        return claims_report()

    monkeypatch.setattr(
        "opennosh_api.publication.natural_readiness.collect_production_claims_readiness",
        collect,
    )
    report = await collect_natural_publication_readiness(
        settings(), blueprint_path=ROOT / "render.yaml", observed_at=OBSERVED_AT
    )

    assert report["status"] == "ready"
    assert observed == [OBSERVED_AT]


def test_default_contract_prefers_packaged_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packaged = tmp_path / "natural-publication-proof-activation.v1.json"
    packaged.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(natural_readiness.resources, "files", lambda _package: tmp_path)

    assert default_natural_activation_contract_path() == packaged


def test_blueprint_rejects_non_boolean_flag(tmp_path: Path) -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    target = next(item for item in blueprint["services"] if item["name"] == "opennosh-api")
    variable = next(
        item for item in target["envVars"] if item.get("key") == "EVIDENCE_UPLOADS_ENABLED"
    )
    variable["value"] = "yes"
    path = tmp_path / "render.yaml"
    path.write_text(yaml.safe_dump(blueprint), encoding="utf-8")

    with pytest.raises(ValueError, match="explicit boolean"):
        natural_readiness._blueprint_flags(path)


def test_readiness_blocks_capacity_drift(tmp_path: Path) -> None:
    payload = json.loads((ROOT / "config/database-capacity.v1.json").read_text())
    payload["roles"]["evidence"]["replicas"] = 1
    payload["roles"]["publication"]["replicas"] = 2
    path = tmp_path / "capacity.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    unsafe = settings().model_copy(update={"database_capacity_manifest_path": path})

    report = build_natural_publication_readiness(
        unsafe,
        claims_report(),
        blueprint_path=ROOT / "render.yaml",
        contract=load_natural_activation_contract(
            ROOT / "config/natural-publication-proof-activation.v1.json"
        ),
        observed_at=OBSERVED_AT,
    )

    assert {"evidence_capacity_not_zero", "publication_replica_count_not_one"}.issubset(
        set(report["failures"])
    )
