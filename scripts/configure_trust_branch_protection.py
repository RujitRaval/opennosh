"""Apply or verify the versioned OpenNosh main-branch trust protection."""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

from scripts.check_trust_gates import load_contract, validate_contract


def protection_payload(contract: dict[str, Any]) -> dict[str, Any]:
    report = validate_contract(contract)
    protection = contract["branch_protection"]
    return {
        "required_status_checks": {
            "strict": protection["strict"],
            "checks": [
                {
                    "context": context,
                    "app_id": protection["required_check_app_id"],
                }
                for context in report["required_status_checks"]
            ],
        },
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 0,
            "require_last_push_approval": False,
        },
        "restrictions": None,
        "required_linear_history": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "block_creations": False,
        "required_conversation_resolution": True,
        "lock_branch": False,
        "allow_fork_syncing": True,
    }


def _gh_json(arguments: list[str], *, input_payload: dict[str, Any] | None = None) -> Any:
    result = subprocess.run(
        ["gh", *arguments],
        input=None if input_payload is None else json.dumps(input_payload),
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def repository_name() -> str:
    value = _gh_json(["repo", "view", "--json", "nameWithOwner"])
    return str(value["nameWithOwner"])


def current_checks(repository: str, branch: str) -> list[tuple[str, int]]:
    value = _gh_json(
        ["api", f"repos/{repository}/branches/{branch}/protection"],
    )
    required = value.get("required_status_checks") or {}
    return sorted(
        (str(check["context"]), int(check["app_id"])) for check in required.get("checks", [])
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--repository")
    arguments = parser.parse_args()

    contract = load_contract()
    payload = protection_payload(contract)
    branch = str(contract["branch_protection"]["branch"])
    repository = arguments.repository or repository_name()
    expected = sorted(
        (str(check["context"]), int(check["app_id"]))
        for check in payload["required_status_checks"]["checks"]
    )

    if arguments.apply:
        _gh_json(
            [
                "api",
                "--method",
                "PUT",
                f"repos/{repository}/branches/{branch}/protection",
                "--input",
                "-",
            ],
            input_payload=payload,
        )

    actual = current_checks(repository, branch)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        stale = sorted(set(actual) - set(expected))
        parser.exit(
            1,
            f"Branch protection drift: missing={missing}, stale={stale}\n",
        )
    print(
        json.dumps(
            {
                "branch": branch,
                "required_status_checks": [
                    {"context": context, "app_id": app_id} for context, app_id in actual
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
