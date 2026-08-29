from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import pytest
from opennosh_api.cli import build_parser
from opennosh_api.federation.cli import run_federation_command
from opennosh_api.federation.drills import (
    DEFAULT_DRILL_CONTRACT_PATH,
    FailureDrillCase,
    FailureDrillInvariantError,
    FailureDrillSecretError,
    canonical_digest,
    exercise_controlled_failure,
    load_failure_drill_contract,
    parse_failure_drill_report,
    validate_failure_drill_report,
)
from pydantic import ValidationError

from scripts.build_federation_failure_drill_report import build_synthetic_report


def _payload() -> dict[str, object]:
    report = build_synthetic_report(DEFAULT_DRILL_CONTRACT_PATH)
    return report.model_dump(mode="json")


def _report_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def test_contract_is_the_exact_reviewed_ten_case_matrix() -> None:
    contract = load_failure_drill_contract()
    assert tuple(case.case_id for case in contract.cases) == tuple(FailureDrillCase)
    assert [case.sequence for case in contract.cases] == list(range(1, 11))
    assert contract.recovery_limit_seconds == 600
    assert contract.navigation_rollback_limit_seconds == 300


def test_complete_synthetic_report_validates_to_a_stable_digest() -> None:
    contract = load_failure_drill_contract()
    report = build_synthetic_report(DEFAULT_DRILL_CONTRACT_PATH)
    first = validate_failure_drill_report(report, contract)
    second = validate_failure_drill_report(report, contract)
    assert first == second == canonical_digest(report)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("claims_enabled_during", True),
        ("activation_ids_present_after", True),
        ("false_published_count", 1),
        ("immutable_overwrite_count", 1),
        ("restoration_verified", False),
    ],
)
def test_report_rejects_fail_open_result_fields(field: str, value: object) -> None:
    payload = _payload()
    drills = payload["drills"]
    assert isinstance(drills, list)
    drills[0][field] = value
    with pytest.raises(ValidationError):
        parse_failure_drill_report(_report_bytes(payload))


def test_report_rejects_missing_duplicate_and_out_of_order_cases() -> None:
    payload = _payload()
    drills = payload["drills"]
    assert isinstance(drills, list)
    drills.pop()
    with pytest.raises(ValidationError, match="missing_duplicate_or_out_of_order"):
        parse_failure_drill_report(_report_bytes(payload))

    payload = _payload()
    drills = payload["drills"]
    assert isinstance(drills, list)
    drills[0], drills[1] = drills[1], drills[0]
    with pytest.raises(ValidationError, match="missing_duplicate_or_out_of_order"):
        parse_failure_drill_report(_report_bytes(payload))

    payload = _payload()
    drills = payload["drills"]
    assert isinstance(drills, list)
    drills[1] = drills[0]
    with pytest.raises(ValidationError, match="missing_duplicate_or_out_of_order"):
        parse_failure_drill_report(_report_bytes(payload))


def test_report_rejects_release_identity_and_public_check_drift() -> None:
    payload = _payload()
    drills = payload["drills"]
    assert isinstance(drills, list)
    drills[0]["release_identity_after"]["manifest_digest"] = "d" * 64
    with pytest.raises(ValidationError, match="release_identity_drift"):
        parse_failure_drill_report(_report_bytes(payload))

    payload = _payload()
    drills = payload["drills"]
    assert isinstance(drills, list)
    drills[0]["public_checks"][0]["status_code"] = 503
    with pytest.raises(ValidationError):
        parse_failure_drill_report(_report_bytes(payload))


def test_report_rejects_slow_recovery_wrong_code_effects_and_evidence() -> None:
    contract = load_failure_drill_contract()

    payload = _payload()
    drills = payload["drills"]
    assert isinstance(drills, list)
    recovered = parse_failure_drill_report(_report_bytes(payload)).drills[0].started_at
    drills[0]["recovered_at"] = (recovered + timedelta(seconds=601)).isoformat()
    with pytest.raises(FailureDrillInvariantError, match="recovery_limit_exceeded"):
        validate_failure_drill_report(parse_failure_drill_report(_report_bytes(payload)), contract)

    payload = _payload()
    drills = payload["drills"]
    assert isinstance(drills, list)
    navigation_started = parse_failure_drill_report(_report_bytes(payload)).drills[-1].started_at
    drills[-1]["recovered_at"] = (navigation_started + timedelta(seconds=301)).isoformat()
    payload["captured_at"] = (navigation_started + timedelta(seconds=302)).isoformat()
    with pytest.raises(FailureDrillInvariantError, match="recovery_limit_exceeded"):
        validate_failure_drill_report(parse_failure_drill_report(_report_bytes(payload)), contract)

    for field, value, expected in (
        ("expected_failure_code", "wrong_code", "drill_expected_failure_code_mismatch"),
        ("observed_failure_code", "wrong_code", "drill_observed_failure_code_mismatch"),
        ("side_effect_delta", 1, "drill_side_effect_delta_mismatch"),
    ):
        payload = _payload()
        drills = payload["drills"]
        assert isinstance(drills, list)
        drills[0][field] = value
        with pytest.raises(FailureDrillInvariantError, match=expected):
            validate_failure_drill_report(
                parse_failure_drill_report(_report_bytes(payload)), contract
            )

    payload = _payload()
    drills = payload["drills"]
    assert isinstance(drills, list)
    drills[0]["evidence"] = drills[0]["evidence"][0:1]
    with pytest.raises(FailureDrillInvariantError, match="required_evidence_missing"):
        validate_failure_drill_report(parse_failure_drill_report(_report_bytes(payload)), contract)


@pytest.mark.parametrize(
    "secret",
    [
        b"-----BEGIN PRIVATE KEY-----",
        b"github_pat_1234567890abcdefghijkl",
        b"AKIA1234567890ABCDEF",
        b"postgresql://owner:password@example.test/db",
        b"https://operator" + b":credential@example.test/evidence",
        b"PRIVATE_KEY=hidden",
        b'{"invitation_token":"hidden"}',
        b'{"providerToken":"hidden"}',
    ],
)
def test_report_bytes_fail_closed_on_secret_patterns(secret: bytes) -> None:
    with pytest.raises(FailureDrillSecretError, match="secret_pattern_detected"):
        parse_failure_drill_report(secret)


class _UnexpectedAdapter:
    restored = False

    async def inject(self, _case: object) -> None:
        raise RuntimeError("synthetic injection failure")

    async def observe_failure(self, _case: object) -> str:
        raise AssertionError("unreachable")

    async def restore(self, _case: object) -> None:
        self.restored = True

    async def restoration_verified(self, _case: object) -> bool:
        return self.restored


def test_controlled_harness_always_attempts_restoration() -> None:
    adapter = _UnexpectedAdapter()
    case = load_failure_drill_contract().cases[0]
    with pytest.raises(RuntimeError, match="synthetic injection failure"):
        asyncio.run(exercise_controlled_failure(case, adapter))  # type: ignore[arg-type]
    assert adapter.restored is True


def test_drill_cli_plan_and_report_validation_need_no_operator_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()
    plan = parser.parse_args(["federation", "drill-plan", "--json"])
    assert run_federation_command(plan) == 0
    assert len(json.loads(capsys.readouterr().out)["cases"]) == 10

    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(_payload(), sort_keys=True),
        encoding="utf-8",
    )
    validate = parser.parse_args(
        [
            "federation",
            "validate-drill-report",
            "--report-file",
            str(report_path),
            "--json",
        ]
    )
    assert run_federation_command(validate) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "passed"
    assert summary["case_count"] == 10


def test_drill_cli_uses_stable_safe_failure_codes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()
    invalid_contract = parser.parse_args(
        ["federation", "drill-plan", "--contract-file", str(tmp_path / "missing-contract")]
    )
    assert run_federation_command(invalid_contract) == 2
    assert capsys.readouterr().err.strip().endswith("drill_contract_invalid")

    missing = parser.parse_args(
        ["federation", "validate-drill-report", "--report-file", str(tmp_path / "missing")]
    )
    assert run_federation_command(missing) == 5
    assert capsys.readouterr().err.strip().endswith("drill_report_io_failed")

    secret_path = tmp_path / "secret.json"
    secret_path.write_bytes(b'{"invitation_token":"must-not-be-echoed"}')
    secret = parser.parse_args(
        ["federation", "validate-drill-report", "--report-file", str(secret_path)]
    )
    assert run_federation_command(secret) == 2
    output = capsys.readouterr()
    assert "secret_pattern_detected" in output.err
    assert "must-not-be-echoed" not in output.err + output.out

    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text("{}", encoding="utf-8")
    malformed = parser.parse_args(
        ["federation", "validate-drill-report", "--report-file", str(malformed_path)]
    )
    assert run_federation_command(malformed) == 2
    assert capsys.readouterr().err.strip().endswith("drill_report_invalid")

    invariant_payload = _payload()
    invariant_payload["contract_digest"] = "d" * 64
    invariant_path = tmp_path / "invariant.json"
    invariant_path.write_text(json.dumps(invariant_payload), encoding="utf-8")
    invariant = parser.parse_args(
        ["federation", "validate-drill-report", "--report-file", str(invariant_path)]
    )
    assert run_federation_command(invariant) == 3
    assert capsys.readouterr().err.strip().endswith("drill_contract_digest_mismatch")
