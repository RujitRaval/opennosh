from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.check_developer_trials import (
    COMPATIBILITY_PATH,
    REPORTS_PATH,
    SCHEMA_PATH,
    validate_repository,
)

ROOT = Path(__file__).resolve().parents[1]


def report(report_id: str, operator: str, reviewer: str = "maintainer-one") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "report_id": report_id,
        "observed_at": "2026-09-03T21:00:00Z",
        "endpoint_kind": "hosted",
        "operator": {
            "github_login": operator,
            "repository_collaborator": False,
            "authored_commit_in_preceding_90_days": False,
        },
        "reviewer": {
            "github_login": reviewer,
            "reviewed_at": "2026-09-03T22:00:00Z",
        },
        "clients": [
            {
                "kind": "javascript",
                "package_version": "0.88.0",
                "artifact_sha256": "a" * 64,
            }
        ],
        "operations": ["search_foods", "get_public_food"],
        "assertions": {
            "search_completed": True,
            "verified_detail": True,
            "attribution_preserved": True,
            "no_credentials": True,
        },
        "problem_codes": [],
    }


def fixture_root(directory: str, *, status: str = "preview") -> Path:
    root = Path(directory)
    (root / SCHEMA_PATH).parent.mkdir(parents=True)
    (root / COMPATIBILITY_PATH).parent.mkdir(parents=True)
    (root / REPORTS_PATH).mkdir(parents=True)
    shutil.copyfile(ROOT / SCHEMA_PATH, root / SCHEMA_PATH)
    (root / COMPATIBILITY_PATH).write_text(
        json.dumps({"status": status}), encoding="utf-8"
    )
    return root


def write_report(root: Path, payload: dict[str, object]) -> None:
    path = root / REPORTS_PATH / f"{payload['report_id']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


class DeveloperTrialTests(unittest.TestCase):
    def test_preview_accepts_zero_reports_without_claiming_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual([], validate_repository(fixture_root(directory)))

    def test_stable_requires_two_distinct_independent_operators(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = fixture_root(directory, status="stable")
            write_report(root, report("trial-one", "operator-one"))
            self.assertIn(
                "stable developer compatibility requires two independent trial operators",
                validate_repository(root),
            )
            write_report(root, report("trial-two", "operator-two"))
            self.assertEqual([], validate_repository(root))

    def test_duplicate_operator_and_self_review_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = fixture_root(directory)
            write_report(root, report("trial-one", "same-operator", "same-operator"))
            write_report(root, report("trial-two", "SAME-OPERATOR"))
            issues = validate_repository(root)
            self.assertTrue(any("operator and reviewer must differ" in issue for issue in issues))
            self.assertTrue(any("operator must be unique" in issue for issue in issues))

    def test_schema_rejects_private_or_unbounded_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = fixture_root(directory)
            payload = report("trial-private", "operator-one")
            payload["endpoint_url"] = "https://private.example"
            payload["operator"]["repository_collaborator"] = True  # type: ignore[index]
            write_report(root, payload)
            issues = validate_repository(root)
            self.assertTrue(any("Additional properties" in issue for issue in issues))
            self.assertTrue(any("False was expected" in issue for issue in issues))

    def test_timestamps_must_be_utc_and_review_must_follow_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = fixture_root(directory)
            payload = report("trial-time", "operator-one")
            payload["reviewer"]["reviewed_at"] = "2026-09-03T20:00:00Z"  # type: ignore[index]
            write_report(root, payload)
            self.assertTrue(
                any(
                    "review cannot predate observation" in issue
                    for issue in validate_repository(root)
                )
            )

            payload["observed_at"] = "2026-09-03T17:00:00-04:00"
            write_report(root, payload)
            self.assertTrue(
                any("does not match 'Z$'" in issue for issue in validate_repository(root))
            )


if __name__ == "__main__":
    unittest.main()
