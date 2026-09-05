from __future__ import annotations

import json
from pathlib import Path

from opennosh_api.publication.readiness import readiness_digest

from scripts.check_publication_readiness import validate_report

ROOT = Path(__file__).resolve().parents[1]


def _report() -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": "1.0",
        "status": "ready",
        "observed_at": "2026-09-04T12:00:00+00:00",
        "runtime": {
            "claims_enabled": False,
            "continuous_claims_enabled": False,
            "claim_concurrency": 1,
            "publication_replicas": 1,
            "reuse_registry_mutations_enabled": False,
            "reuse_verification_enabled": False,
            "reuse_public_enabled": False,
            "impact_aggregation_enabled": False,
            "impact_public_enabled": False,
            "public_status_enabled": False,
            "deployed_commit": "a" * 40,
        },
        "queue": {},
        "publication_intents": {},
        "federation_scopes": {},
        "living_commons": {
            "migration_heads": ["20260905_0037"],
            "expected_migration_head": "20260905_0037",
            "all_capabilities_disabled": True,
            "impact_metric_manifest_sha256": "b" * 64,
            "public_status_manifest_sha256": "c" * 64,
            "public_status_component_ids": [
                "api",
                "contributions",
                "downloads",
                "evidence-processing",
                "publication",
                "reuse-registry",
                "search",
                "tracker",
            ],
        },
        "activation_candidate": {},
        "failures": [],
    }
    report["readiness_sha256"] = readiness_digest(report)
    return report


def test_publication_readiness_checker_validates_schema_and_digest() -> None:
    schema = json.loads((ROOT / "schemas/publication-readiness.schema.json").read_text())
    report = _report()
    assert validate_report(report, schema) == []
    report["status"] = "blocked"
    assert validate_report(report, schema) == [
        "readiness_sha256 does not match canonical report content"
    ]


def test_publication_readiness_checker_rejects_enabled_claims_type() -> None:
    schema = json.loads((ROOT / "schemas/publication-readiness.schema.json").read_text())
    report = _report()
    runtime = report["runtime"]
    assert isinstance(runtime, dict)
    runtime["claims_enabled"] = "false"
    report["readiness_sha256"] = readiness_digest(report)
    assert any(
        "schema runtime/claims_enabled" in issue for issue in validate_report(report, schema)
    )
