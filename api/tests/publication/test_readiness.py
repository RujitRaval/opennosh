from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from opennosh_api.capacity import ProcessRole
from opennosh_api.jobs.contracts import JobLane, JobMessage
from opennosh_api.jobs.pgqueuer import encode_message
from opennosh_api.publication.readiness import (
    build_production_claims_readiness,
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
    ) -> None:
        message = JobMessage(
            lane=JobLane.PUBLICATION,
            job_type="publication.wake",
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
        self.queries: list[str] = []

    async def fetch(self, query: str, *_arguments: object) -> list[dict[str, Any]]:
        self.queries.append(query)
        if "FROM opennosh_pgqueuer" in query and "GROUP BY status" in query:
            return [{"state": self.active[0]["state"], "count": 1}]
        if "FROM opennosh_pgqueuer" in query:
            return self.active
        if "FROM publication_intents" in query:
            return [{"state": "pending", "count": 1}]
        if "FROM federation_maintainers" in query:
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
