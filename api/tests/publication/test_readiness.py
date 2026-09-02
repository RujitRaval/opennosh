from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from opennosh_api.capacity import ProcessRole
from opennosh_api.jobs.contracts import JobLane, JobMessage
from opennosh_api.jobs.pgqueuer import encode_message
from opennosh_api.publication import readiness as readiness_module
from opennosh_api.publication.readiness import (
    build_production_claims_readiness,
    default_activation_contract_path,
    load_activation_contract,
    readiness_digest,
)
from opennosh_api.settings import Settings

ROOT = Path(__file__).resolve().parents[3]
OBSERVED_AT = datetime(2026, 8, 30, 18, tzinfo=UTC)


class FakeReadinessConnection:
    def __init__(
        self,
        *,
        picked: bool = False,
        payload: bytes | None = None,
        queue_state: str | None = None,
        job_type: str = "publication.wake",
        intent_state: str = "pending",
        federation_state: str | None = None,
    ) -> None:
        message = JobMessage(
            lane=(
                JobLane.EVIDENCE if job_type == "evidence.preserve" else JobLane.PUBLICATION
            ),
            job_type=job_type,
            subject_id=uuid4(),
            idempotency_key="readiness:test:wakeup",
            workflow_revision=0,
        )
        self.active = [
            {
                "state": queue_state or ("picked" if picked else "queued"),
                "execute_after": OBSERVED_AT - timedelta(seconds=10),
                "heartbeat": OBSERVED_AT - timedelta(seconds=60) if picked else None,
                "payload": payload if payload is not None else encode_message(message),
            }
        ]
        self.intent_state = intent_state
        self.federation_state = federation_state
        self.queries: list[str] = []

    async def fetch(self, query: str, *_arguments: object) -> list[dict[str, Any]]:
        self.queries.append(query)
        if "FROM opennosh_pgqueuer" in query and "GROUP BY status" in query:
            return [{"state": self.active[0]["state"], "count": 1}]
        if "FROM opennosh_pgqueuer" in query:
            return self.active
        if "FROM publication_intents" in query:
            return [{"state": self.intent_state, "count": 1}]
        if "FROM federation_maintainers" in query:
            if self.federation_state is not None:
                return [{"state": self.federation_state, "count": 1}]
            return [
                {"state": "active", "count": 1},
                {"state": "quarantined", "count": 1},
            ]
        raise AssertionError(query)


def _settings() -> Settings:
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
        render_git_commit="56633741ccaa6ce8f9193830c7a733fc78935fce",
        database_capacity_manifest_path=ROOT / "config/database-capacity.v1.json",
    )


@pytest.mark.asyncio
async def test_readiness_report_is_deterministic_redacted_and_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "opennosh_api.publication.readiness.validate_publication_claim_credentials",
        lambda _settings: object(),
    )
    contract = load_activation_contract(ROOT / "config/publication-claims-activation.v1.json")
    connection = FakeReadinessConnection()

    first = await build_production_claims_readiness(
        connection, _settings(), contract, observed_at=OBSERVED_AT
    )
    second = await build_production_claims_readiness(
        FakeReadinessConnection(), _settings(), contract, observed_at=OBSERVED_AT
    )

    assert first == second
    assert first["status"] == "ready"
    assert first["readiness_sha256"] == readiness_digest(first)
    assert first["queue"] == {
        "counts": {
            "canceled": 0,
            "deleted": 0,
            "exception": 0,
            "failed": 0,
            "picked": 0,
            "queued": 1,
            "successful": 0,
        },
        "active": 1,
        "picked": 0,
        "claimable": 1,
        "oldest_claimable_age_seconds": 10,
    }
    rendered = json.dumps(first, sort_keys=True)
    assert "DATABASE_URL" not in rendered
    assert "PRIVATE_KEY" not in rendered
    assert "secret" not in rendered.casefold()
    assert all(query.lstrip().startswith("SELECT") for query in connection.queries)


@pytest.mark.asyncio
async def test_readiness_blocks_when_picked_work_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "opennosh_api.publication.readiness.validate_publication_claim_credentials",
        lambda _settings: object(),
    )
    report = await build_production_claims_readiness(
        FakeReadinessConnection(picked=True),
        _settings(),
        load_activation_contract(ROOT / "config/publication-claims-activation.v1.json"),
        observed_at=OBSERVED_AT,
    )

    assert report["status"] == "blocked"
    assert "picked_publication_work_present" in report["failures"]


@pytest.mark.asyncio
async def test_readiness_blocks_while_preactivation_smoke_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "opennosh_api.publication.readiness.validate_publication_claim_credentials",
        lambda _settings: object(),
    )
    settings = _settings()
    settings.publication_preactivation_smoke_enabled = True

    report = await build_production_claims_readiness(
        FakeReadinessConnection(),
        settings,
        load_activation_contract(ROOT / "config/publication-claims-activation.v1.json"),
        observed_at=OBSERVED_AT,
    )

    assert report["status"] == "blocked"
    assert "preactivation_smoke_enabled" in report["failures"]


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("app_environment", "development", "app_environment_not_production"),
        ("process_role", ProcessRole.WEB, "process_role_not_publication"),
        ("publication_claims_enabled", True, "claims_already_enabled"),
        (
            "publication_continuous_claims_enabled",
            True,
            "continuous_claims_already_enabled",
        ),
        (
            "publication_activation_ids",
            "00000000-0000-4000-8000-000000000001",
            "activation_id_present",
        ),
        ("federation_ingestion_enabled", True, "federation_ingestion_enabled"),
        ("federation_projection_enabled", True, "federation_projection_enabled"),
        ("federation_search_enabled", True, "federation_search_enabled"),
        ("latest_refresh_enabled", False, "latest_refresh_disabled"),
        ("publication_claim_concurrency", 2, "claim_concurrency_not_pinned"),
        ("render_git_commit", None, "deployed_commit_missing_or_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_readiness_blocks_each_unsafe_runtime_mode(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    failure: str,
) -> None:
    monkeypatch.setattr(
        "opennosh_api.publication.readiness.validate_publication_claim_credentials",
        lambda _settings: object(),
    )
    settings = _settings()
    setattr(settings, field, value)

    report = await build_production_claims_readiness(
        FakeReadinessConnection(),
        settings,
        load_activation_contract(ROOT / "config/publication-claims-activation.v1.json"),
        observed_at=OBSERVED_AT,
    )

    assert report["status"] == "blocked"
    assert failure in report["failures"]


@pytest.mark.asyncio
async def test_readiness_blocks_incomplete_credentials_without_exposing_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(_settings: object) -> None:
        raise ValueError("GITHUB_FORGE_PRIVATE_KEY contains secret-value")

    monkeypatch.setattr(
        "opennosh_api.publication.readiness.validate_publication_claim_credentials",
        reject,
    )

    report = await build_production_claims_readiness(
        FakeReadinessConnection(),
        _settings(),
        load_activation_contract(ROOT / "config/publication-claims-activation.v1.json"),
        observed_at=OBSERVED_AT,
    )

    rendered = json.dumps(report, sort_keys=True)
    assert report["status"] == "blocked"
    assert "claim_credentials_incomplete" in report["failures"]
    assert "secret-value" not in rendered
    assert "GITHUB_FORGE_PRIVATE_KEY" not in rendered


@pytest.mark.parametrize(
    ("connection", "failure"),
    [
        (
            FakeReadinessConnection(payload=b"not-a-job-message"),
            "unclassifiable_active_queue_payload",
        ),
        (FakeReadinessConnection(queue_state="unknown"), "unknown_queue_state"),
    ],
)
@pytest.mark.asyncio
async def test_readiness_blocks_unclassifiable_queue_rows(
    monkeypatch: pytest.MonkeyPatch,
    connection: FakeReadinessConnection,
    failure: str,
) -> None:
    monkeypatch.setattr(
        "opennosh_api.publication.readiness.validate_publication_claim_credentials",
        lambda _settings: object(),
    )

    report = await build_production_claims_readiness(
        connection,
        _settings(),
        load_activation_contract(ROOT / "config/publication-claims-activation.v1.json"),
        observed_at=OBSERVED_AT,
    )

    assert report["status"] == "blocked"
    assert failure in report["failures"]


def test_activation_contract_rejects_malformed_t33_5_evidence(tmp_path: Path) -> None:
    payload = json.loads(
        (ROOT / "config/publication-claims-activation.v1.json").read_text(encoding="utf-8")
    )
    payload["t33_5"]["proof_sha256"] = "not-a-digest"
    path = tmp_path / "malformed-activation.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_activation_contract(path)


def test_activation_contract_pins_disabled_rollback_and_concurrency_one() -> None:
    contract = load_activation_contract(ROOT / "config/publication-claims-activation.v1.json")

    assert contract.activation.mode == "continuous"
    assert contract.activation.claim_concurrency == 1
    assert contract.rollback.claims_enabled is False
    assert contract.rollback.latest_refresh_enabled is True


def test_default_activation_contract_path_prefers_packaged_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packaged = tmp_path / "publication-claims-activation.v1.json"
    packaged.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(readiness_module.resources, "files", lambda _package: tmp_path)

    assert default_activation_contract_path() == packaged


def test_default_activation_contract_path_falls_back_to_repository_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(readiness_module.resources, "files", lambda _package: tmp_path)

    assert default_activation_contract_path() == (
        Path(readiness_module.__file__).resolve().parents[3]
        / "config/publication-claims-activation.v1.json"
    )


def test_postgres_dsn_normalizes_async_driver_and_rejects_other_backends() -> None:
    assert readiness_module._postgres_dsn(
        "postgresql+asyncpg://publication@db/opennosh"
    ) == "postgresql://publication@db/opennosh"

    with pytest.raises(ValueError, match="requires PostgreSQL"):
        readiness_module._postgres_dsn("sqlite:///tmp/opennosh.db")


@pytest.mark.asyncio
async def test_readiness_rejects_a_naive_observation_before_database_access() -> None:
    class ForbiddenConnection:
        async def fetch(self, _query: str, *_arguments: object) -> list[dict[str, Any]]:
            raise AssertionError("naive observation reached the database")

    with pytest.raises(ValueError, match="timezone"):
        await build_production_claims_readiness(
            ForbiddenConnection(),
            _settings(),
            load_activation_contract(ROOT / "config/publication-claims-activation.v1.json"),
            observed_at=datetime(2026, 8, 30, 18),
        )


@pytest.mark.asyncio
async def test_readiness_blocks_capacity_contract_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Manifest:
        def active_role_budget(self, _role: ProcessRole) -> object:
            return SimpleNamespace(max_in_flight_database_sections=0, replicas=2)

    monkeypatch.setattr(readiness_module, "load_capacity_manifest", lambda _path: Manifest())
    monkeypatch.setattr(
        readiness_module,
        "validate_publication_claim_credentials",
        lambda _settings: object(),
    )

    report = await build_production_claims_readiness(
        FakeReadinessConnection(),
        _settings(),
        load_activation_contract(ROOT / "config/publication-claims-activation.v1.json"),
        observed_at=OBSERVED_AT,
    )

    assert report["status"] == "blocked"
    assert "claim_concurrency_exceeds_capacity" in report["failures"]
    assert "publication_replica_count_not_pinned" in report["failures"]


@pytest.mark.parametrize(
    ("connection", "failure"),
    [
        (
            FakeReadinessConnection(job_type="evidence.preserve"),
            "unexpected_active_queue_job_type",
        ),
        (
            FakeReadinessConnection(intent_state="unknown"),
            "unknown_publication_state",
        ),
        (
            FakeReadinessConnection(federation_state="unknown"),
            "unknown_federation_state",
        ),
    ],
)
@pytest.mark.asyncio
async def test_readiness_blocks_unknown_active_work_and_domain_states(
    monkeypatch: pytest.MonkeyPatch,
    connection: FakeReadinessConnection,
    failure: str,
) -> None:
    monkeypatch.setattr(
        readiness_module,
        "validate_publication_claim_credentials",
        lambda _settings: object(),
    )

    report = await build_production_claims_readiness(
        connection,
        _settings(),
        load_activation_contract(ROOT / "config/publication-claims-activation.v1.json"),
        observed_at=OBSERVED_AT,
    )

    assert report["status"] == "blocked"
    assert failure in report["failures"]


@pytest.mark.parametrize("build_fails", [False, True])
@pytest.mark.asyncio
async def test_collect_readiness_uses_default_contract_and_always_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    build_fails: bool,
) -> None:
    contract = load_activation_contract(ROOT / "config/publication-claims-activation.v1.json")
    resolved_contract = tmp_path / "packaged-activation.json"
    closed = False
    observed: list[datetime] = []

    class Connection:
        async def close(self) -> None:
            nonlocal closed
            closed = True

    connection = Connection()

    async def connect(dsn: str) -> Connection:
        assert dsn == "postgresql://publication@db/opennosh"
        return connection

    async def build(
        supplied_connection: object,
        supplied_settings: object,
        supplied_contract: object,
        *,
        observed_at: datetime,
    ) -> dict[str, object]:
        assert supplied_connection is connection
        assert supplied_settings is settings
        assert supplied_contract is contract
        observed.append(observed_at)
        if build_fails:
            raise RuntimeError("readiness build failed")
        return {"status": "ready"}

    def load(path: str | Path) -> object:
        assert Path(path) == resolved_contract
        return contract

    monkeypatch.setattr(
        readiness_module,
        "default_activation_contract_path",
        lambda: resolved_contract,
    )
    monkeypatch.setattr(readiness_module, "load_activation_contract", load)
    monkeypatch.setattr(readiness_module.asyncpg, "connect", connect)
    monkeypatch.setattr(readiness_module, "build_production_claims_readiness", build)
    settings = SimpleNamespace(
        publication_claims_activation_contract_path=Path(
            "config/publication-claims-activation.v1.json"
        ),
        process_database_url=lambda role: (
            "postgresql+asyncpg://publication@db/opennosh"
            if role is ProcessRole.PUBLICATION
            else ""
        ),
    )

    if build_fails:
        with pytest.raises(RuntimeError, match="readiness build failed"):
            await readiness_module.collect_production_claims_readiness(settings)
    else:
        assert await readiness_module.collect_production_claims_readiness(settings) == {
            "status": "ready"
        }

    assert closed is True
    assert observed and observed[0].tzinfo is UTC
