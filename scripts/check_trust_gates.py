"""Validate and classify the versioned OpenNosh trust-gate contract."""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "config/trust-gates.v1.json"
EXPECTED_ROLES = {
    "pull_request": {"visitor", "tracker"},
    "release": {"visitor", "contributor", "steward", "tracker", "maintainer", "developer"},
    "scheduled": {"visitor", "contributor", "steward", "tracker", "maintainer", "developer"},
}


class TrustGateContractError(ValueError):
    """Raised when a required gate, coverage claim, or exception is invalid."""


def load_contract(path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TrustGateContractError("Trust-gate contract root must be an object")
    return value


def _workflow(path: Path) -> dict[str, Any]:
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if not isinstance(value, dict):
        raise TrustGateContractError(f"Workflow is not a mapping: {path.relative_to(ROOT)}")
    return value


def _test_function_exists(node_id: str, root: Path) -> bool:
    parts = node_id.split("::")
    if len(parts) != 2:
        return False
    source = root / parts[0]
    if not source.is_file():
        return False
    module = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parts[1]
        for node in module.body
    )


def classify_paths(paths: list[str], contract: dict[str, Any]) -> dict[str, bool]:
    classifiers = contract["classifiers"]
    result = {
        name: any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in patterns)
        for name, patterns in classifiers.items()
    }
    result["documentation_only"] = bool(paths) and all(
        path.endswith(".md") or path.startswith("docs/") for path in paths
    )
    return result


def validate_exceptions(contract: dict[str, Any], root: Path = ROOT) -> list[str]:
    quarantine = contract["quarantine"]
    exceptions_path = root / quarantine["exceptions_file"]
    payload = json.loads(exceptions_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1" or not isinstance(payload.get("exceptions"), list):
        raise TrustGateContractError("Trust-gate exceptions must use schema version 1")
    required = set(quarantine["required_fields"])
    forbidden = set(quarantine["forbidden_gates"])
    active: list[str] = []
    for index, exception in enumerate(payload["exceptions"]):
        if not isinstance(exception, dict):
            raise TrustGateContractError(f"Quarantine exception {index} must be an object")
        missing = sorted(required - set(exception))
        if missing:
            raise TrustGateContractError(
                f"Quarantine exception {index} is missing fields: {', '.join(missing)}"
            )
        gate = exception["gate"]
        if gate in forbidden:
            raise TrustGateContractError(f"Gate {gate} cannot be quarantined")
        try:
            expiry = date.fromisoformat(exception["expires_on"])
        except (TypeError, ValueError) as error:
            raise TrustGateContractError(
                f"Quarantine exception {index} has an invalid expires_on date"
            ) from error
        if expiry < datetime.now(UTC).date():
            raise TrustGateContractError(
                f"Quarantine exception {index} expired on {expiry.isoformat()}"
            )
        if not str(exception["issue"]).startswith("https://github.com/"):
            raise TrustGateContractError(f"Quarantine exception {index} must link a GitHub issue")
        active.append(str(gate))
    return active


def validate_contract(contract: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    if contract.get("schema_version") != "1":
        raise TrustGateContractError("Trust-gate contract must use schema version 1")
    if not str(contract.get("owner", "")).startswith("@"):
        raise TrustGateContractError("Trust-gate contract requires an @owner")

    tiers_value = contract.get("tiers")
    if not isinstance(tiers_value, dict) or set(tiers_value) != {
        "pull_request",
        "release",
        "scheduled",
    }:
        raise TrustGateContractError("Trust-gate tiers must be pull_request, release, scheduled")
    tiers: dict[str, dict[str, Any]] = tiers_value

    role_report: dict[str, list[str]] = {}
    for tier_name, tier in tiers.items():
        workflow_path = root / tier["workflow"]
        workflow = _workflow(workflow_path)
        triggers = workflow.get("on", {})
        if not isinstance(triggers, dict):
            raise TrustGateContractError(f"{tier_name} workflow triggers must be a mapping")
        missing_triggers = sorted(set(tier["triggers"]) - set(triggers))
        if missing_triggers:
            raise TrustGateContractError(
                f"{tier_name} is missing triggers: {', '.join(missing_triggers)}"
            )
        jobs = workflow.get("jobs", {})
        if not isinstance(jobs, dict):
            raise TrustGateContractError(f"{tier_name} workflow jobs must be a mapping")
        missing_jobs = sorted(set(tier["required_jobs"]) - set(jobs))
        if missing_jobs:
            raise TrustGateContractError(f"{tier_name} is missing jobs: {', '.join(missing_jobs)}")
        for job_name in tier["required_jobs"]:
            job = jobs[job_name]
            if job.get("continue-on-error") not in (None, "false", False):
                raise TrustGateContractError(
                    f"Required job {tier_name}:{job_name} cannot continue on error"
                )
            timeout = job.get("timeout-minutes")
            if timeout is None:
                raise TrustGateContractError(
                    f"Required job {tier_name}:{job_name} is missing a runtime budget"
                )
            if int(timeout) > int(tier["maximum_runtime_minutes"]):
                raise TrustGateContractError(
                    f"Job {tier_name}:{job_name} exceeds its runtime budget"
                )
        if int(tier["artifact_retention_days"]) < 1:
            raise TrustGateContractError(f"{tier_name} artifact retention must be positive")
        claimed_roles = set(tier["required_roles"])
        expected_roles = EXPECTED_ROLES[tier_name]
        if claimed_roles != expected_roles:
            missing = sorted(expected_roles - claimed_roles)
            stale = sorted(claimed_roles - expected_roles)
            raise TrustGateContractError(
                f"Role coverage drift for {tier_name}: missing={missing}, stale={stale}"
            )
        workflow_env = workflow.get("env", {})
        workflow_roles = {
            role for role in str(workflow_env.get("OPENNOSH_REQUIRED_ROLES", "")).split(",") if role
        }
        if workflow_roles != claimed_roles:
            missing = sorted(claimed_roles - workflow_roles)
            stale = sorted(workflow_roles - claimed_roles)
            raise TrustGateContractError(
                f"Workflow role inventory drift for {tier_name}: missing={missing}, stale={stale}"
            )
        role_report[tier_name] = sorted(claimed_roles)

    protection = contract["branch_protection"]
    if protection.get("branch") != "main" or protection.get("strict") is not True:
        raise TrustGateContractError("Branch protection must be strict on main")
    if int(protection.get("required_check_app_id", 0)) < 1:
        raise TrustGateContractError("Branch protection must source-pin required checks")
    pull_tier = tiers["pull_request"]
    pull_workflow = _workflow(root / pull_tier["workflow"])
    pull_jobs = pull_workflow["jobs"]
    protected_jobs = set(protection["required_check_jobs"])
    expected_protected_jobs = set(pull_tier["required_jobs"])
    if protected_jobs != expected_protected_jobs:
        missing = sorted(expected_protected_jobs - protected_jobs)
        stale = sorted(protected_jobs - expected_protected_jobs)
        raise TrustGateContractError(f"Branch protection drift: missing={missing}, stale={stale}")
    protected_contexts = [str(pull_jobs[job]["name"]) for job in protection["required_check_jobs"]]

    sys.path.insert(0, str(root / "api"))
    from opennosh_api.publication.orchestrator import (  # type: ignore[import-untyped]
        PublicationFailpoint,
    )
    from opennosh_api.publication.state import (  # type: ignore[import-untyped]
        ObservationStatus,
        PublicationStepName,
    )

    coverage = contract["coverage"]
    expected_steps = set(coverage["transition_matrix"]["steps"])
    actual_steps = {item.value for item in PublicationStepName}
    if actual_steps != expected_steps:
        missing = sorted(actual_steps - expected_steps)
        stale = sorted(expected_steps - actual_steps)
        raise TrustGateContractError(f"Transition coverage drift: missing={missing}, stale={stale}")
    expected_failpoints = set(coverage["transition_matrix"]["failpoints"])
    actual_failpoints = {item.value for item in PublicationFailpoint}
    if actual_failpoints != expected_failpoints:
        missing = sorted(actual_failpoints - expected_failpoints)
        stale = sorted(expected_failpoints - actual_failpoints)
        raise TrustGateContractError(f"Failpoint coverage drift: missing={missing}, stale={stale}")
    expected_outcomes = set(coverage["rescue_outcomes"]["values"])
    actual_outcomes = {item.value for item in ObservationStatus}
    if actual_outcomes != expected_outcomes:
        missing = sorted(actual_outcomes - expected_outcomes)
        stale = sorted(expected_outcomes - actual_outcomes)
        raise TrustGateContractError(
            f"Rescue-outcome coverage drift: missing={missing}, stale={stale}"
        )

    claims = [
        coverage["transition_matrix"]["test"],
        coverage["rescue_outcomes"]["test"],
        *(item["test"] for item in coverage["policy_branches"]),
    ]
    missing_claims = sorted(
        node_id for node_id in claims if not _test_function_exists(node_id, root)
    )
    if missing_claims:
        raise TrustGateContractError(
            f"Trust coverage claims reference missing tests: {', '.join(missing_claims)}"
        )

    for gate in contract["non_retry_gates"]:
        workflow = _workflow(root / gate["workflow"])
        jobs = workflow["jobs"]
        if gate["job"] not in jobs:
            raise TrustGateContractError(f"Non-retry gate job is missing: {gate['job']}")
        steps = jobs[gate["job"]].get("steps", [])
        by_name = {step.get("name"): step for step in steps if isinstance(step, dict)}
        primary = by_name.get(gate["primary_step"])
        if primary is None:
            raise TrustGateContractError(
                f"Non-retry primary step is missing: {gate['primary_step']}"
            )
        primary_run = str(primary.get("run", ""))
        if primary.get("continue-on-error") not in (None, "false", False):
            raise TrustGateContractError(
                f"Primary gate {gate['primary_step']} cannot continue on error"
            )
        if any(token in primary_run for token in ("set +e", "|| true", "--update-snapshots")):
            raise TrustGateContractError(
                f"Primary gate {gate['primary_step']} masks or rewrites its verdict"
            )
        for diagnostic_name in gate["diagnostic_steps"]:
            diagnostic = by_name.get(diagnostic_name)
            if diagnostic is None:
                raise TrustGateContractError(f"Diagnostic step is missing: {diagnostic_name}")
            if "failure()" not in str(diagnostic.get("if", "")):
                raise TrustGateContractError(
                    f"Diagnostic step {diagnostic_name} must run only after failure"
                )

    active_exceptions = validate_exceptions(contract, root)
    transition_count = len(expected_steps) * len(expected_failpoints)
    return {
        "transition_scenarios": transition_count,
        "rescue_outcomes": len(expected_outcomes),
        "policy_branches": len(coverage["policy_branches"]),
        "roles": role_report,
        "active_exceptions": active_exceptions,
        "required_status_checks": protected_contexts,
    }


def _changed_paths(base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def _write_github_output(path: Path, values: dict[str, bool]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        for name, value in sorted(values.items()):
            stream.write(f"{name}={str(value).lower()}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("validate")
    classify = subparsers.add_parser("classify")
    classify.add_argument("--base")
    classify.add_argument("--head", default="HEAD")
    classify.add_argument("--path", action="append", default=[])
    classify.add_argument("--github-output", type=Path)
    arguments = parser.parse_args()

    contract = load_contract(arguments.contract)
    if arguments.command == "classify":
        paths = list(arguments.path)
        if not paths:
            if not arguments.base:
                parser.error("classify requires --base or at least one --path")
            paths = _changed_paths(arguments.base, arguments.head)
        values = classify_paths(paths, contract)
        if arguments.github_output:
            _write_github_output(arguments.github_output, values)
        print(json.dumps({"paths": paths, "classifiers": values}, indent=2, sort_keys=True))
        return 0

    report = validate_contract(contract)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
