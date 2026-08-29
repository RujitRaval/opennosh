from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from opennosh_api.federation.drills import (
    DEFAULT_DRILL_CONTRACT_PATH,
    DrillResult,
    EvidenceReference,
    FailureDrillReport,
    PublicCheck,
    ReleaseIdentity,
    canonical_digest,
    load_failure_drill_contract,
    validate_failure_drill_report,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_synthetic_report(contract_path: Path) -> FailureDrillReport:
    contract = load_failure_drill_contract(contract_path)
    baseline = ReleaseIdentity(
        release_version="synthetic-1.0.0",
        publication_id=UUID("11111111-1111-4111-8111-111111111111"),
        manifest_digest="a" * 64,
        receipt_digest="b" * 64,
        pointer_key_id="synthetic-online-v1",
        public_origin="https://opennosh.example",
    )
    origin = datetime(2026, 1, 1, tzinfo=UTC)
    drills: list[DrillResult] = []
    for case in contract.cases:
        started_at = origin + timedelta(minutes=case.sequence * 2)
        drills.append(
            DrillResult(
                sequence=case.sequence,
                case_id=case.case_id,
                started_at=started_at,
                failure_observed_at=started_at + timedelta(seconds=15),
                restoration_started_at=started_at + timedelta(seconds=30),
                recovered_at=started_at + timedelta(seconds=60),
                expected_failure_code=case.expected_failure_code,
                observed_failure_code=case.expected_failure_code,
                claims_enabled_before=False,
                claims_enabled_during=False,
                claims_enabled_after=False,
                activation_ids_present_before=False,
                activation_ids_present_during=False,
                activation_ids_present_after=False,
                false_published_count=0,
                immutable_overwrite_count=0,
                side_effect_delta=case.expected_side_effect_delta,
                restoration_verified=True,
                public_checks=tuple(
                    PublicCheck(
                        name=name,
                        status_code=200,
                        digest=_digest(f"{case.case_id}:{name}"),
                    )
                    for name in contract.required_public_checks
                ),
                release_identity_after=baseline,
                evidence=tuple(
                    EvidenceReference(
                        kind=kind,
                        identifier=f"synthetic/{case.case_id}/{kind}",
                        digest=_digest(f"{case.case_id}:{kind}"),
                    )
                    for kind in case.evidence_kinds
                ),
            )
        )
    report = FailureDrillReport(
        contract_digest=canonical_digest(contract),
        production_commit="c" * 40,
        captured_at=drills[-1].recovered_at + timedelta(seconds=1),
        baseline=baseline,
        drills=tuple(drills),
    )
    validate_failure_drill_report(report, contract)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-file", type=Path, default=DEFAULT_DRILL_CONTRACT_PATH)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = build_synthetic_report(arguments.contract_file)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
