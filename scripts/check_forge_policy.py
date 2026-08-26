#!/usr/bin/env python3
"""Validate the versioned protected-merge policy before it is applied to GitHub."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

EXPECTED_GOVERNANCE_CHECKS = {
    "authorization",
    "evidence",
    "license",
    "payload",
    "provenance",
    "schema",
    "self-review",
}
EXPECTED_CI_CHECKS = {
    "API checks",
    "Compose application boot",
    "repository checks",
    "visual regression",
    "web checks",
}
GOVERNANCE_ATTESTATION_CHECK = "OpenNosh governance attestation"
GOVERNANCE_ATTESTER_APP_ID_PLACEHOLDER = "$OPENNOSH_GOVERNANCE_ATTESTER_APP_ID"
GITHUB_ACTIONS_APP_ID = 15368


def load_policy(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Forge policy must be a JSON object")
    return cast(dict[str, Any], value)


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema_version") != "1.0":
        raise ValueError("Forge policy schema version must be 1.0")
    if policy.get("repository") != "RujitRaval/opennosh":
        raise ValueError("Forge policy must target the canonical repository")
    if policy.get("target_branch") != "main":
        raise ValueError("Forge policy must protect main")
    if policy.get("managed_path_prefix") != "packs/":
        raise ValueError("Forge writes must stay scoped to packs/")
    application = policy.get("forge_application")
    if not isinstance(application, dict):
        raise ValueError("Forge application policy is missing")
    if application != {
        "kind": "github_app",
        "contents": "write",
        "pull_requests": "write",
        "metadata": "read",
        "administration": "none",
        "checks": "read",
    }:
        raise ValueError("Forge application must retain its exact bounded permissions")
    attester = policy.get("governance_attester_application")
    if not isinstance(attester, dict):
        raise ValueError("Governance attester application policy is missing")
    if attester != {
        "kind": "github_app",
        "metadata": "read",
        "checks": "write",
        "contents": "none",
        "pull_requests": "none",
        "administration": "none",
    }:
        raise ValueError("Governance attester must remain checks-only")
    if set(policy.get("governance_checks", [])) != EXPECTED_GOVERNANCE_CHECKS:
        raise ValueError("Forge policy must cover every governance trust check")
    sources = policy.get("governance_check_sources")
    if not isinstance(sources, dict) or set(sources) != EXPECTED_GOVERNANCE_CHECKS:
        raise ValueError("Every governance trust check must name its enforcement source")
    if any(not isinstance(source, str) or not source.strip() for source in sources.values()):
        raise ValueError("Governance trust-check enforcement sources cannot be empty")
    ruleset = policy.get("ruleset")
    if not isinstance(ruleset, dict):
        raise ValueError("Forge branch ruleset is missing")
    if ruleset.get("enforcement") != "active" or ruleset.get("bypass_actors") != []:
        raise ValueError("Forge branch rules cannot be disabled or bypassed")
    raw_checks = ruleset.get("required_status_checks")
    if not isinstance(raw_checks, list) or any(
        not isinstance(check, dict) for check in raw_checks
    ):
        raise ValueError("Forge ruleset checks must pin their expected source application")
    checks = cast(list[dict[str, Any]], raw_checks)
    if {check.get("context") for check in checks} != EXPECTED_CI_CHECKS | {
        GOVERNANCE_ATTESTATION_CHECK
    }:
        raise ValueError("Forge ruleset required checks differ from the canonical CI jobs")
    action_checks = [
        check for check in checks if check.get("context") in EXPECTED_CI_CHECKS
    ]
    if any(check.get("integration_id") != GITHUB_ACTIONS_APP_ID for check in action_checks):
        raise ValueError("Every required check must be pinned to GitHub Actions")
    attestation = next(
        check for check in checks if check.get("context") == GOVERNANCE_ATTESTATION_CHECK
    )
    if attestation.get("integration_id") != GOVERNANCE_ATTESTER_APP_ID_PLACEHOLDER:
        raise ValueError("Governance attestation must be pinned to the separate attester App")
    required_truths = {
        "strict_required_status_checks": True,
        "require_pull_request": True,
        "block_force_push": True,
        "block_deletion": True,
    }
    for name, expected in required_truths.items():
        if ruleset.get(name) is not expected:
            raise ValueError(f"Forge ruleset must enforce {name}")
    if ruleset.get("allowed_merge_methods") != ["squash"]:
        raise ValueError("Governed publication permits squash merge only")
    if ruleset.get("required_approving_review_count") != 0:
        raise ValueError("The steward decision, not a hidden second review, authorizes publication")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    policy = load_policy(root / "config/forge-policy.v1.json")
    validate_policy(policy)
    ruleset = cast(dict[str, Any], policy["ruleset"])
    print(
        "Forge policy: protected main; no bypass actors; "
        f"{len(ruleset['required_status_checks'])} source-pinned required CI checks; "
        "squash-only merge"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
