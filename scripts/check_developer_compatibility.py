#!/usr/bin/env python3
"""Validate the developer distribution contract against repository artifacts."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

MANIFEST_PATH = Path("config/developer-compatibility.v1.json")
SCHEMA_PATH = Path("schemas/developer-compatibility.schema.json")
OPENAPI_PATH = Path("web/lib/generated/openapi.json")
GENERATED_SDK_PATH = Path("web/lib/generated/client/sdk.gen.ts")
GENERATED_CLIENT_PATH = Path("web/lib/generated/client")
GENERATOR_MANIFEST_PATH = Path("web/lib/generated/manifest.json")
PACKAGE_PATH = Path("packages/npm/package.json")
NPM_GENERATED_TYPES_PATH = Path("packages/npm/src/generated-types.d.ts")
NPM_PROBLEM_CONTRACT_PATH = Path("packages/npm/src/generated-problem-contract.js")
NPM_OPERATION_POLICY_PATH = Path("packages/npm/src/generated-operation-policy.js")
PYTHON_OPERATION_POLICY_PATH = Path("api/opennosh_api/sdk/_generated.py")
COMPATIBILITY_FIXTURES_PATH = Path("tests/fixtures/developer-compatibility.v1.json")
OPENAPI_N_MINUS_ONE_PATH = Path("tests/fixtures/openapi-1.0.0-public.json")
OPENAPI_N_MINUS_ONE_SOURCE_COMMIT = "93d8446027ead93170ba2d9876dc41775f2ac9ad"
OPENAPI_N_MINUS_ONE_SHA256 = "d1aa430baf9987122c202ddecd0763cecec7a5dc2254d75cc80ce69da6e7e8f2"

EXPECTED_OPERATION_POLICY = {
    "/api/v1/foods/capabilities": ("application/json", 2_097_152),
    "/api/v1/foods/search": ("application/json", 2_097_152),
    "/api/v1/public/commons-snapshot": ("application/json", 24_576),
    "/api/v1/public/foods/{source}/{source_id}": ("application/json", 524_288),
    "/api/v1/public/missions": ("application/json", 2_097_152),
    "/api/v1/public/missions/activity": ("application/json", 2_097_152),
    "/api/v1/public/releases/{release_version}/foods/{source}/{source_id}": (
        "application/json",
        524_288,
    ),
    "/api/v1/public/releases/{release_version}/foods/{source}/{source_id}/provenance": (
        "text/html",
        2_097_152,
    ),
    "/api/v1/public/releases/{release_version}/manifest": (
        "application/vnd.opennosh.release+json",
        8_388_608,
    ),
    "/api/v1/public/releases/{release_version}/packs/{pack_id}/{pack_version}/download": (
        "application/zip",
        67_108_864,
    ),
}


def canonical_digest(payload: dict[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("compatibility_sha256", None)
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def package_version(release_version: str) -> str:
    parts = release_version.split(".")
    if len(parts) != 4 or any(not part.isdigit() for part in parts):
        raise ValueError("VERSION must contain four numeric components")
    return ".".join(parts[:3])


def generated_client_digest(root: Path, files: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in files:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((root / GENERATED_CLIENT_PATH / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _public_operations(openapi: dict[str, Any]) -> dict[str, str]:
    operations: dict[str, str] = {}
    for path, path_item in openapi.get("paths", {}).items():
        is_developer_food_operation = path in {
            "/api/v1/foods/capabilities",
            "/api/v1/foods/search",
        }
        if not is_developer_food_operation and not path.startswith("/api/v1/public/"):
            continue
        operation = path_item.get("get")
        if not isinstance(operation, dict):
            continue
        operation_id = operation.get("operationId")
        if isinstance(operation_id, str):
            operations[path] = operation_id
    return operations


def _expected_python_operation_policy(
    openapi: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    policies: dict[str, dict[str, Any]] = {}
    for declared in manifest["public_operations"]:
        operation = openapi["paths"][declared["path"]]["get"]
        content = operation.get("responses", {}).get("200", {}).get("content", {})
        policies[declared["path"]] = {
            "accepted_media_types": sorted(content) if isinstance(content, dict) else [],
            "media_type": declared["media_type"],
            "max_response_bytes": declared["max_response_bytes"],
            "path_parameters": {
                parameter["name"]: parameter["schema"]
                for parameter in operation.get("parameters", [])
                if parameter["in"] == "path"
            },
        }
    return policies


def _generated_python_operation_policy(path: Path) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "PUBLIC_OPERATION_POLICIES"
        ):
            return ast.literal_eval(node.value)
    return None


def _openapi_response_schema(
    openapi: dict[str, Any], operation: dict[str, Any], media_type: str
) -> dict[str, Any] | None:
    content = operation.get("responses", {}).get("200", {}).get("content", {})
    schema = content.get(media_type, {}).get("schema")
    if not isinstance(schema, dict):
        return None
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": openapi.get("components", {}).get("schemas", {}),
        **schema,
    }
    return json.loads(json.dumps(document).replace("#/components/schemas/", "#/$defs/"))


def _validate_response_fixture(
    *,
    openapi: dict[str, Any],
    operation: dict[str, Any],
    media_type: str,
    body: Any,
    label: str,
) -> list[str]:
    schema = _openapi_response_schema(openapi, operation, media_type)
    if schema is None:
        return [f"{label}: media type {media_type} is not a declared 200 response"]
    errors = Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(body)
    return [
        f"{label}: {error.message}" for error in sorted(errors, key=lambda item: list(item.path))
    ]


def validate_repository(root: Path) -> list[str]:
    issues: list[str] = []
    manifest = json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8"))
    schema = json.loads((root / SCHEMA_PATH).read_text(encoding="utf-8"))
    errors = Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest)
    for error in sorted(errors, key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in error.absolute_path) or "/"
        issues.append(f"manifest schema {location}: {error.message}")
    if issues:
        return issues

    declared_digest = manifest.get("compatibility_sha256")
    if declared_digest != canonical_digest(manifest):
        issues.append("compatibility_sha256 does not match canonical manifest bytes")

    release_version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if manifest.get("release_version") != release_version:
        issues.append("release_version must match VERSION")
    try:
        expected_package_version = package_version(release_version)
    except ValueError as error:
        issues.append(str(error))
        expected_package_version = None
    package = json.loads((root / PACKAGE_PATH).read_text(encoding="utf-8"))
    openapi = json.loads((root / OPENAPI_PATH).read_text(encoding="utf-8"))
    if expected_package_version is not None and package.get("version") != expected_package_version:
        issues.append("npm package version must match VERSION")
    if not (root / NPM_GENERATED_TYPES_PATH).is_file():
        issues.append("npm generated transport types are missing")
    elif (root / NPM_GENERATED_TYPES_PATH).read_bytes() != (
        root / GENERATED_CLIENT_PATH / "types.gen.ts"
    ).read_bytes():
        issues.append("npm generated transport types are stale")
    problem_contract_path = root / NPM_PROBLEM_CONTRACT_PATH
    if not problem_contract_path.is_file():
        issues.append("npm generated problem contract is missing")
    else:
        prefix = (
            "// Generated from web/lib/generated/openapi.json. Do not edit.\n"
            "export const PROBLEM_SCHEMAS = Object.freeze("
        )
        source = problem_contract_path.read_text(encoding="utf-8")
        try:
            generated_problem_schemas = json.loads(source.removeprefix(prefix).removesuffix(");\n"))
        except json.JSONDecodeError:
            generated_problem_schemas = None
        expected_problem_schemas = {
            name: openapi["components"]["schemas"][name]
            for name in (
                "FieldError",
                "LatestStateReference",
                "ProblemCode",
                "ProblemDetails",
                "RecoveryAction",
            )
        }
        if not source.startswith(prefix) or generated_problem_schemas != expected_problem_schemas:
            issues.append("npm generated problem contract is stale")
    operation_policy_path = root / NPM_OPERATION_POLICY_PATH
    operation_prefix = (
        "// Generated from the developer compatibility manifest and OpenAPI. Do not edit.\n"
        "export const PUBLIC_OPERATION_POLICIES = Object.freeze("
    )
    if not operation_policy_path.is_file():
        issues.append("npm generated operation policy is missing")
    else:
        source = operation_policy_path.read_text(encoding="utf-8")
        try:
            generated_operation_policy = json.loads(
                source.removeprefix(operation_prefix).removesuffix(");\n")
            )
        except json.JSONDecodeError:
            generated_operation_policy = None
        expected_operation_policy = {}
        for operation in manifest["public_operations"]:
            openapi_operation = openapi["paths"][operation["path"]]["get"]
            parameters = openapi_operation.get("parameters", [])
            expected_operation_policy[operation["path"]] = {
                "acceptedMediaTypes": sorted(
                    openapi_operation.get("responses", {}).get("200", {}).get("content", {})
                ),
                "mediaType": operation["media_type"],
                "maxResponseBytes": operation["max_response_bytes"],
                "pathParameters": {
                    parameter["name"]: parameter["schema"]
                    for parameter in parameters
                    if parameter["in"] == "path"
                },
            }
        if (
            not source.startswith(operation_prefix)
            or generated_operation_policy != expected_operation_policy
        ):
            issues.append("npm generated operation policy is stale")
    python_operation_policy_path = root / PYTHON_OPERATION_POLICY_PATH
    if not python_operation_policy_path.is_file():
        issues.append("Python generated operation policy is missing")
    else:
        try:
            python_operation_policy = _generated_python_operation_policy(
                python_operation_policy_path
            )
        except (OSError, SyntaxError, ValueError):
            python_operation_policy = None
        if python_operation_policy != _expected_python_operation_policy(openapi, manifest):
            issues.append("Python generated operation policy is stale")
    clients = manifest.get("clients", {})
    if (
        expected_package_version is not None
        and clients.get("javascript", {}).get("current") != expected_package_version
    ):
        issues.append("clients.javascript.current must match VERSION package prefix")
    for name in ("python", "cli"):
        if clients.get(name, {}).get("current") != release_version:
            issues.append(f"clients.{name}.current must match VERSION")
    discovery_expectations = {"mcp": "preview", "embed": "disabled"}
    for name, expected_status in discovery_expectations.items():
        client = clients.get(name, {})
        if (
            client.get("current") != "1.0.0"
            or client.get("contract_major") != 1
            or client.get("discovery_enabled") is not False
            or client.get("status") != expected_status
        ):
            issues.append(
                f"clients.{name} must be {expected_status} at protocol 1.0.0 "
                "with discovery disabled"
            )

    contract_version = openapi.get("info", {}).get("x-opennosh-contract-version")
    if manifest.get("openapi", {}).get("current") != contract_version:
        issues.append("openapi.current must match the generated OpenAPI contract")
    supported = manifest.get("openapi", {}).get("supported", [])
    if supported != [
        {"major": 2, "minimum": "2.0.0", "deprecation_date": None},
        {"major": 1, "minimum": "1.0.0", "deprecation_date": None},
    ]:
        issues.append("openapi.supported must pin current major 2 and N-1 major 1")

    expected_operations = _public_operations(openapi)
    missing_policies = sorted(set(expected_operations) - set(EXPECTED_OPERATION_POLICY))
    if missing_policies:
        issues.append(
            "response policy missing for public operations: " + ", ".join(missing_policies)
        )
    declared_operations = manifest.get("public_operations", [])
    declared_by_path = {
        item.get("path"): item.get("operation_id")
        for item in declared_operations
        if isinstance(item, dict)
    }
    if set(declared_by_path) != set(expected_operations):
        missing = sorted(set(expected_operations) - set(declared_by_path))
        extra = sorted(set(declared_by_path) - set(expected_operations))
        if missing:
            issues.append("public_operations missing: " + ", ".join(missing))
        if extra:
            issues.append("public_operations unexpected: " + ", ".join(extra))
    if len(declared_by_path) != len(declared_operations):
        issues.append("public_operations paths must be unique")
    for path, operation_id in expected_operations.items():
        if path in declared_by_path and declared_by_path[path] != operation_id:
            issues.append(f"public_operations operation_id mismatch: {path}")
    for item in declared_operations:
        if not isinstance(item, dict) or item.get("path") not in EXPECTED_OPERATION_POLICY:
            continue
        expected_media_type, expected_limit = EXPECTED_OPERATION_POLICY[item["path"]]
        if (item.get("media_type"), item.get("max_response_bytes")) != (
            expected_media_type,
            expected_limit,
        ):
            issues.append(f"public_operations response policy mismatch: {item['path']}")

    fixtures = json.loads((root / COMPATIBILITY_FIXTURES_PATH).read_text(encoding="utf-8"))
    compatible_contracts = fixtures.get("compatible_contracts", [])
    supported_minimums = [item["minimum"] for item in supported]
    if compatible_contracts != supported_minimums:
        issues.append("compatibility fixtures must cover current and N-1 minimum versions")
    current_contract = str(manifest["openapi"]["current"])
    n_minus_one_contract = str(supported[1]["minimum"])
    incompatible_contracts = fixtures.get("incompatible_contracts", [])
    if incompatible_contracts != ["0.9.0", "3.0.0"]:
        issues.append("compatibility fixtures must cover older and higher-major contracts")

    operation_by_id = {
        operation_id: openapi["paths"][path]["get"]
        for path, operation_id in expected_operations.items()
    }
    declared_by_id = {
        item["operation_id"]: item
        for item in declared_operations
        if isinstance(item, dict) and isinstance(item.get("operation_id"), str)
    }
    response_fixtures = fixtures.get("responses", [])
    fixture_ids = {item.get("operation_id") for item in response_fixtures if isinstance(item, dict)}
    if fixture_ids != set(operation_by_id):
        issues.append("response fixtures must cover every public operation exactly once")
    if len(fixture_ids) != len(response_fixtures):
        issues.append("response fixture operation IDs must be unique")
    for fixture in response_fixtures:
        if not isinstance(fixture, dict):
            continue
        operation_id = fixture.get("operation_id")
        media_type = fixture.get("media_type")
        operation = operation_by_id.get(operation_id)
        declared = declared_by_id.get(operation_id)
        if operation is None or not isinstance(media_type, str) or declared is None:
            continue
        if declared.get("media_type") != media_type:
            issues.append(f"response fixture media type mismatch: {operation_id}")
            continue
        issues.extend(
            _validate_response_fixture(
                openapi=openapi,
                operation=operation,
                media_type=media_type,
                body=fixture.get("body"),
                label=f"response fixture {current_contract} {operation_id}",
            )
        )

    n_minus_one_bytes = (root / OPENAPI_N_MINUS_ONE_PATH).read_bytes()
    if hashlib.sha256(n_minus_one_bytes).hexdigest() != OPENAPI_N_MINUS_ONE_SHA256:
        issues.append("N-1 OpenAPI fixture digest does not match its reviewed snapshot")
    if fixtures.get("n_minus_one_source_commit") != OPENAPI_N_MINUS_ONE_SOURCE_COMMIT:
        issues.append("N-1 OpenAPI fixture source commit is not pinned")
    n_minus_one = json.loads(n_minus_one_bytes)
    n_minus_one_version = n_minus_one.get("info", {}).get("x-opennosh-contract-version")
    if n_minus_one_version != n_minus_one_contract:
        issues.append("N-1 OpenAPI fixture version does not match the compatibility manifest")
    n_minus_one_operations = _public_operations(n_minus_one)
    n_minus_one_by_id = {
        operation_id: n_minus_one["paths"][path]["get"]
        for path, operation_id in n_minus_one_operations.items()
    }
    n_minus_one_fixtures = fixtures.get("n_minus_one_responses", [])
    n_minus_one_fixture_ids = {
        item.get("operation_id") for item in n_minus_one_fixtures if isinstance(item, dict)
    }
    if n_minus_one_fixture_ids != set(n_minus_one_by_id):
        issues.append("N-1 response fixtures must cover every retained public operation")
    if len(n_minus_one_fixture_ids) != len(n_minus_one_fixtures):
        issues.append("N-1 response fixture operation IDs must be unique")
    for fixture in n_minus_one_fixtures:
        if not isinstance(fixture, dict):
            continue
        operation_id = fixture.get("operation_id")
        operation = n_minus_one_by_id.get(operation_id)
        media_type = fixture.get("media_type")
        if operation is None or not isinstance(media_type, str):
            continue
        issues.extend(
            _validate_response_fixture(
                openapi=n_minus_one,
                operation=operation,
                media_type=media_type,
                body=fixture.get("body"),
                label=f"response fixture {n_minus_one_version} {operation_id}",
            )
        )

    problem_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": openapi.get("components", {}).get("schemas", {}),
        "$ref": "#/$defs/ProblemDetails",
    }
    problem_schema = json.loads(
        json.dumps(problem_schema).replace("#/components/schemas/", "#/$defs/")
    )
    problem_fixtures = fixtures.get("problems", [])
    if {item.get("name") for item in problem_fixtures if isinstance(item, dict)} != {
        "rfc9457_failure",
        "rate_limited",
    }:
        issues.append("problem fixtures must cover RFC 9457 and rate-limit metadata")
    for fixture in problem_fixtures:
        if not isinstance(fixture, dict):
            continue
        errors = Draft202012Validator(problem_schema, format_checker=FormatChecker()).iter_errors(
            fixture.get("body")
        )
        for error in sorted(errors, key=lambda item: list(item.path)):
            issues.append(f"problem fixture {fixture.get('name')}: {error.message}")

    state_fixtures = fixtures.get("proof_states", [])
    if {item.get("name") for item in state_fixtures if isinstance(item, dict)} != {
        "stale_verified",
        "unavailable_proof",
    }:
        issues.append("proof fixtures must cover stale verified and unavailable states")
    for fixture in state_fixtures:
        if not isinstance(fixture, dict):
            continue
        operation_id = fixture.get("operation_id")
        operation = operation_by_id.get(operation_id)
        media_type = fixture.get("media_type")
        if operation is None or not isinstance(media_type, str):
            issues.append(f"proof fixture {fixture.get('name')}: unknown operation or media type")
            continue
        issues.extend(
            _validate_response_fixture(
                openapi=openapi,
                operation=operation,
                media_type=media_type,
                body=fixture.get("body"),
                label=f"proof fixture {fixture.get('name')}",
            )
        )

    sdk = (root / GENERATED_SDK_PATH).read_text(encoding="utf-8")
    for path in expected_operations:
        if f"url: '{path}'" not in sdk:
            issues.append(f"generated SDK missing public operation path: {path}")
    if re.search(r"export const \w+.*\.post<", sdk) is None:
        issues.append("generated SDK does not contain the complete operation client")

    generator_manifest = json.loads((root / GENERATOR_MANIFEST_PATH).read_text(encoding="utf-8"))
    client_files = sorted(
        str(path.relative_to(root / GENERATED_CLIENT_PATH))
        for path in (root / GENERATED_CLIENT_PATH).rglob("*")
        if path.is_file()
    )
    if generator_manifest.get("client_files") != client_files:
        issues.append("generated client file inventory is stale")
    if generator_manifest.get("client_sha256") != generated_client_digest(root, client_files):
        issues.append("generated client digest is stale")
    operation_count = sum(
        1
        for path_item in openapi.get("paths", {}).values()
        for method in path_item
        if method in {"delete", "get", "patch", "post", "put"}
    )
    if generator_manifest.get("operation_count") != operation_count:
        issues.append("generated operation count is stale")

    return issues


def write_digest(root: Path) -> None:
    path = root / MANIFEST_PATH
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["compatibility_sha256"] = canonical_digest(manifest)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--write-digest", action="store_true")
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    if arguments.write_digest:
        write_digest(root)
    issues = validate_repository(root)
    if issues:
        for issue in issues:
            print(issue)
        return 1
    manifest = json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8"))
    print(f"developer compatibility: valid ({manifest['compatibility_sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
