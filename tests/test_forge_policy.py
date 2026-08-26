from __future__ import annotations

import copy
import unittest
from pathlib import Path

from scripts.check_forge_policy import load_policy, validate_policy


class ForgePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.policy = load_policy(root / "config/forge-policy.v1.json")

    def test_canonical_policy_is_valid(self) -> None:
        validate_policy(self.policy)

    def test_bypass_actor_is_rejected(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["ruleset"]["bypass_actors"] = ["RepositoryRole:admin"]
        with self.assertRaisesRegex(ValueError, "bypassed"):
            validate_policy(policy)

    def test_hidden_second_review_is_rejected(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["ruleset"]["required_approving_review_count"] = 1
        with self.assertRaisesRegex(ValueError, "hidden second review"):
            validate_policy(policy)

    def test_missing_self_review_check_is_rejected(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["governance_checks"].remove("self-review")
        with self.assertRaisesRegex(ValueError, "every governance trust check"):
            validate_policy(policy)

    def test_required_check_from_untrusted_source_is_rejected(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["ruleset"]["required_status_checks"][0]["integration_id"] = 999
        with self.assertRaisesRegex(ValueError, "pinned to GitHub Actions"):
            validate_policy(policy)

    def test_attester_cannot_write_repository_or_pull_requests(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["governance_attester_application"]["pull_requests"] = "write"
        with self.assertRaisesRegex(ValueError, "checks-only"):
            validate_policy(policy)

    def test_forge_application_cannot_write_checks(self) -> None:
        policy = copy.deepcopy(self.policy)
        policy["forge_application"]["checks"] = "write"
        with self.assertRaisesRegex(ValueError, "exact bounded permissions"):
            validate_policy(policy)


if __name__ == "__main__":
    unittest.main()
