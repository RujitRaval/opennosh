#!/usr/bin/env python3
"""Validate the pinned performance contract and JSON artifact schemas."""

from __future__ import annotations

import json

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.performance.contract import CONTRACT_DIRECTORY, load_contract


def main() -> int:
    contract = load_contract()
    contract_schema = json.loads((CONTRACT_DIRECTORY / "contract.schema.json").read_text())
    result_schema = json.loads((CONTRACT_DIRECTORY / "result.schema.json").read_text())
    Draft202012Validator.check_schema(contract_schema)
    Draft202012Validator.check_schema(result_schema)
    Draft202012Validator(contract_schema, format_checker=FormatChecker()).validate(
        contract.document
    )
    plan_readme = CONTRACT_DIRECTORY / "plans" / "README.md"
    if not plan_readme.is_file():
        raise ValueError("query-plan artifact directory is undocumented")
    print(
        f"benchmark contract: valid ({contract.document['contract_id']}, sha256={contract.sha256})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
