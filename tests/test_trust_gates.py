from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_changed_coverage import ChangedCoverageError, check_report
from scripts.check_trust_gates import (
    TrustGateContractError,
    classify_paths,
    load_contract,
    validate_contract,
    validate_exceptions,
)
from scripts.configure_trust_branch_protection import protection_payload


class TrustGateContractTests(unittest.TestCase):
    def test_repository_contract_reports_complete_inventory(self) -> None:
        report = validate_contract(load_contract())

        self.assertEqual(report["transition_scenarios"], 60)
        self.assertEqual(report["rescue_outcomes"], 5)
        self.assertEqual(report["policy_branches"], 10)
        self.assertEqual(report["active_exceptions"], [])
        self.assertEqual(report["roles"]["pull_request"], ["tracker", "visitor"])
        self.assertEqual(
            report["roles"]["release"],
            ["contributor", "developer", "maintainer", "steward", "tracker", "visitor"],
        )

    def test_branch_protection_requires_every_pull_request_job(self) -> None:
        payload = protection_payload(load_contract())

        self.assertTrue(payload["required_status_checks"]["strict"])
        contexts = [check["context"] for check in payload["required_status_checks"]["checks"]]
        self.assertEqual(
            contexts,
            [
                "risk classification",
                "repository checks",
                "API checks",
                "trust protocol coverage",
                "web checks",
                "visual regression",
                "Real vertical acceptance",
                "Compose application boot",
            ],
        )
        self.assertEqual(
            {check["app_id"] for check in payload["required_status_checks"]["checks"]},
            {15368},
        )
        self.assertEqual(
            payload["required_pull_request_reviews"]["required_approving_review_count"],
            0,
        )
        self.assertFalse(payload["allow_force_pushes"])
        self.assertFalse(payload["allow_deletions"])

    def test_classifier_names_trust_security_and_docs_only_changes(self) -> None:
        contract = load_contract()

        trust = classify_paths(["api/opennosh_api/publication/state.py"], contract)
        docs = classify_paths(["docs/testing.md", "README.md"], contract)

        self.assertTrue(trust["trust_protocol"])
        self.assertTrue(trust["production"])
        self.assertFalse(trust["documentation_only"])
        self.assertTrue(docs["documentation_only"])
        self.assertFalse(docs["trust_protocol"])

    def test_transition_drift_reports_exact_missing_value(self) -> None:
        contract = copy.deepcopy(load_contract())
        contract["coverage"]["transition_matrix"]["steps"].remove("copy_receipt")

        with self.assertRaisesRegex(
            TrustGateContractError,
            r"Transition coverage drift: missing=\['copy_receipt'\], stale=\[\]",
        ):
            validate_contract(contract)

    def test_role_drift_reports_exact_missing_role(self) -> None:
        contract = copy.deepcopy(load_contract())
        contract["tiers"]["release"]["required_roles"].remove("steward")

        with self.assertRaisesRegex(
            TrustGateContractError,
            r"Role coverage drift for release: missing=\['steward'\], stale=\[\]",
        ):
            validate_contract(contract)

    def test_quarantine_requires_owner_reason_issue_and_expiry(self) -> None:
        contract = copy.deepcopy(load_contract())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exceptions_path = root / "config/trust-gate-exceptions.v1.json"
            exceptions_path.parent.mkdir()
            exceptions_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1",
                        "exceptions": [
                            {
                                "gate": "visual",
                                "issue": "https://github.com/open-nosh/opennosh/issues/1",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                TrustGateContractError,
                "missing fields: expires_on, owner, reason",
            ):
                validate_exceptions(contract, root)

    def test_trust_or_security_gate_cannot_be_quarantined(self) -> None:
        contract = copy.deepcopy(load_contract())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exceptions_path = root / "config/trust-gate-exceptions.v1.json"
            exceptions_path.parent.mkdir()
            exceptions_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1",
                        "exceptions": [
                            {
                                "gate": "trust_protocol",
                                "issue": "https://github.com/open-nosh/opennosh/issues/1",
                                "owner": "@owner",
                                "reason": "diagnostic",
                                "expires_on": "2099-01-01",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                TrustGateContractError,
                "Gate trust_protocol cannot be quarantined",
            ):
                validate_exceptions(contract, root)


class ChangedCoverageTests(unittest.TestCase):
    def test_changed_line_coverage_reports_exact_untested_lines(self) -> None:
        report = {
            "totals": {"percent_covered": 85},
            "files": {
                "api/opennosh_api/example.py": {
                    "executed_lines": [1, 2],
                    "missing_lines": [3],
                }
            },
        }

        with self.assertRaisesRegex(
            ChangedCoverageError,
            r"Changed-line coverage 66.67% is below 90.00%; "
            r"untested=api/opennosh_api/example.py:3",
        ):
            check_report(
                report,
                {"api/opennosh_api/example.py": {1, 2, 3}},
                changed_threshold=90,
                repository_threshold=80,
            )

    def test_non_executable_changed_lines_do_not_reduce_coverage(self) -> None:
        report = {
            "totals": {"percent_covered": 80},
            "files": {
                "api/opennosh_api/example.py": {
                    "executed_lines": [1],
                    "missing_lines": [],
                }
            },
        }

        result = check_report(
            report,
            {"api/opennosh_api/example.py": {1, 2}},
            changed_threshold=90,
            repository_threshold=80,
        )

        self.assertEqual(result["changed_percent"], 100)
        self.assertEqual(result["changed_executable_lines"], 1)


if __name__ == "__main__":
    unittest.main()
