from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.check_developer_compatibility import (
    COMPATIBILITY_FIXTURES_PATH,
    MANIFEST_PATH,
    OPENAPI_N_MINUS_ONE_PATH,
    canonical_digest,
    package_version,
    validate_repository,
    write_digest,
)

ROOT = Path(__file__).resolve().parents[1]


def copy_contract(root: Path) -> None:
    for relative in (
        "VERSION",
        MANIFEST_PATH,
        "schemas/developer-compatibility.schema.json",
        "web/lib/generated/openapi.json",
        "web/lib/generated/manifest.json",
        "packages/npm/package.json",
        "packages/npm/src/generated-types.d.ts",
        "packages/npm/src/generated-problem-contract.js",
        "packages/npm/src/generated-operation-policy.js",
        "api/opennosh_api/sdk/_generated.py",
        COMPATIBILITY_FIXTURES_PATH,
        OPENAPI_N_MINUS_ONE_PATH,
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    shutil.copytree(ROOT / "web/lib/generated/client", root / "web/lib/generated/client")


def mutate_manifest(root: Path, callback: Callable[[dict[str, Any]], None]) -> None:
    path = root / MANIFEST_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    callback(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_digest(root)


class DeveloperCompatibilityTests(unittest.TestCase):
    def test_repository_contract_is_valid(self) -> None:
        self.assertEqual([], validate_repository(ROOT))

    def test_package_version_uses_release_prefix(self) -> None:
        self.assertEqual("2.3.4", package_version("2.3.4.5"))
        with self.assertRaisesRegex(ValueError, "four numeric components"):
            package_version("2.3.4")

    def test_digest_ignores_its_own_field(self) -> None:
        payload = {"schema_version": "1.0", "compatibility_sha256": "a" * 64}
        self.assertEqual(canonical_digest(payload), canonical_digest({"schema_version": "1.0"}))

    def test_stale_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            mutate_manifest(root, lambda payload: payload.update(status="retired"))
            payload = json.loads((root / MANIFEST_PATH).read_text())
            payload["compatibility_sha256"] = "0" * 64
            (root / MANIFEST_PATH).write_text(json.dumps(payload))
            self.assertIn(
                "compatibility_sha256 does not match canonical manifest bytes",
                validate_repository(root),
            )

    def test_release_version_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            mutate_manifest(root, lambda payload: payload.update(release_version="9.9.9.9"))
            self.assertIn("release_version must match VERSION", validate_repository(root))

    def test_client_version_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            mutate_manifest(
                root,
                lambda payload: payload["clients"]["javascript"].update(current="9.9.9"),
            )
            self.assertIn(
                "clients.javascript.current must match VERSION package prefix",
                validate_repository(root),
            )

    def test_npm_package_version_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / "packages/npm/package.json"
            package = json.loads(path.read_text(encoding="utf-8"))
            package["version"] = "9.9.9"
            path.write_text(json.dumps(package), encoding="utf-8")
            self.assertIn("npm package version must match VERSION", validate_repository(root))

    def test_python_and_cli_versions_use_the_published_wheel_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            mutate_manifest(
                root,
                lambda payload: payload["clients"]["python"].update(current="9.9.9.9"),
            )
            self.assertIn("clients.python.current must match VERSION", validate_repository(root))

    def test_mcp_discovery_cannot_be_enabled_in_foundation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            mutate_manifest(
                root,
                lambda payload: payload["clients"]["mcp"].update(
                    discovery_enabled=True, status="preview"
                ),
            )
            self.assertIn(
                "clients.mcp must remain disabled at protocol 1.0.0 in the foundation slice",
                validate_repository(root),
            )

    def test_discovery_protocols_start_at_version_one_while_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            mutate_manifest(
                root,
                lambda payload: payload["clients"]["embed"].update(contract_major=2),
            )
            self.assertTrue(any("manifest schema" in issue for issue in validate_repository(root)))

    def test_openapi_version_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            mutate_manifest(root, lambda payload: payload["openapi"].update(current="1.0.0"))
            self.assertIn(
                "openapi.current must match the generated OpenAPI contract",
                validate_repository(root),
            )

    def test_missing_public_operation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            mutate_manifest(root, lambda payload: payload["public_operations"].pop())
            issues = validate_repository(root)
            self.assertTrue(any("manifest schema public_operations" in issue for issue in issues))

    def test_operation_id_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            mutate_manifest(
                root,
                lambda payload: payload["public_operations"][0].update(operation_id="wrong"),
            )
            self.assertTrue(
                any("operation_id mismatch" in issue for issue in validate_repository(root))
            )

    def test_duplicate_public_operation_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)

            def duplicate(payload: dict[str, Any]) -> None:
                item = dict(payload["public_operations"][0])
                item["max_response_bytes"] -= 1
                payload["public_operations"].append(item)

            mutate_manifest(root, duplicate)
            self.assertIn("public_operations paths must be unique", validate_repository(root))

    def test_response_policy_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            mutate_manifest(
                root,
                lambda payload: payload["public_operations"][0].update(
                    max_response_bytes=67_108_864
                ),
            )
            self.assertTrue(
                any("response policy mismatch" in issue for issue in validate_repository(root))
            )

    def test_new_public_operation_requires_an_explicit_response_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            openapi_path = root / "web/lib/generated/openapi.json"
            openapi = json.loads(openapi_path.read_text(encoding="utf-8"))
            openapi["paths"]["/api/v1/public/new-read"] = {
                "get": {"operationId": "new_read_api_v1_public_new_read_get"}
            }
            openapi_path.write_text(json.dumps(openapi), encoding="utf-8")
            mutate_manifest(
                root,
                lambda payload: payload["public_operations"].append(
                    {
                        "operation_id": "new_read_api_v1_public_new_read_get",
                        "method": "GET",
                        "path": "/api/v1/public/new-read",
                        "media_type": "application/json",
                        "max_response_bytes": 1024,
                    }
                ),
            )
            self.assertIn(
                "response policy missing for public operations: /api/v1/public/new-read",
                validate_repository(root),
            )

    def test_generated_sdk_must_contain_every_public_operation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            sdk_path = root / "web/lib/generated/client/sdk.gen.ts"
            sdk_path.write_text(
                sdk_path.read_text().replace("/api/v1/public/missions'", "/missing'")
            )
            self.assertIn(
                "generated SDK missing public operation path: /api/v1/public/missions",
                validate_repository(root),
            )

    def test_generated_client_digest_detects_manual_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            sdk_path = root / "web/lib/generated/client/sdk.gen.ts"
            sdk_path.write_text(sdk_path.read_text() + "// manual edit\n")
            self.assertIn("generated client digest is stale", validate_repository(root))

    def test_npm_transport_types_must_match_the_canonical_generator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / "packages/npm/src/generated-types.d.ts"
            path.write_text(path.read_text(encoding="utf-8") + "// stale\n")

            self.assertIn("npm generated transport types are stale", validate_repository(root))

    def test_npm_problem_contract_must_match_the_canonical_openapi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / "packages/npm/src/generated-problem-contract.js"
            path.write_text(path.read_text(encoding="utf-8").replace("rate_limited", "other"))

            self.assertIn("npm generated problem contract is stale", validate_repository(root))

    def test_npm_operation_policy_must_match_the_compatibility_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / "packages/npm/src/generated-operation-policy.js"
            path.write_text(path.read_text(encoding="utf-8").replace("24576", "24577"))

            self.assertIn("npm generated operation policy is stale", validate_repository(root))

    def test_python_operation_policy_must_match_the_compatibility_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / "api/opennosh_api/sdk/_generated.py"
            path.write_text(path.read_text(encoding="utf-8").replace("24576", "24577"))

            self.assertIn("Python generated operation policy is stale", validate_repository(root))

    def test_missing_and_malformed_python_operation_policy_are_reported(self) -> None:
        for replacement, expected in (
            (None, "Python generated operation policy is missing"),
            ("not valid Python\n", "Python generated operation policy is stale"),
        ):
            with (
                self.subTest(replacement=replacement),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                copy_contract(root)
                path = root / "api/opennosh_api/sdk/_generated.py"
                if replacement is None:
                    path.unlink()
                else:
                    path.write_text(replacement, encoding="utf-8")
                self.assertIn(expected, validate_repository(root))

    def test_missing_and_malformed_npm_generated_contracts_are_reported(self) -> None:
        cases = (
            (
                "packages/npm/src/generated-types.d.ts",
                None,
                "npm generated transport types are missing",
            ),
            (
                "packages/npm/src/generated-problem-contract.js",
                None,
                "npm generated problem contract is missing",
            ),
            (
                "packages/npm/src/generated-problem-contract.js",
                "not valid generated JSON\n",
                "npm generated problem contract is stale",
            ),
            (
                "packages/npm/src/generated-operation-policy.js",
                None,
                "npm generated operation policy is missing",
            ),
            (
                "packages/npm/src/generated-operation-policy.js",
                "not valid generated JSON\n",
                "npm generated operation policy is stale",
            ),
        )
        for relative, replacement, expected in cases:
            with (
                self.subTest(relative=relative, replacement=replacement),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                copy_contract(root)
                path = root / relative
                if replacement is None:
                    path.unlink()
                else:
                    path.write_text(replacement, encoding="utf-8")

                self.assertIn(expected, validate_repository(root))

    def test_generated_client_must_include_non_get_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            sdk_path = root / "web/lib/generated/client/sdk.gen.ts"
            sdk_path.write_text(sdk_path.read_text().replace(".post<", ".get<"))
            self.assertIn(
                "generated SDK does not contain the complete operation client",
                validate_repository(root),
            )

    def test_generated_operation_count_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / "web/lib/generated/manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["operation_count"] -= 1
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertIn("generated operation count is stale", validate_repository(root))

    def test_every_public_operation_requires_a_response_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / COMPATIBILITY_FIXTURES_PATH
            fixtures = json.loads(path.read_text(encoding="utf-8"))
            fixtures["responses"].pop()
            path.write_text(json.dumps(fixtures), encoding="utf-8")
            self.assertIn(
                "response fixtures must cover every public operation exactly once",
                validate_repository(root),
            )

    def test_response_fixtures_are_validated_against_openapi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / COMPATIBILITY_FIXTURES_PATH
            fixtures = json.loads(path.read_text(encoding="utf-8"))
            fixtures["responses"][0]["body"]["barcode_lookup_enabled"] = "false"
            path.write_text(json.dumps(fixtures), encoding="utf-8")
            self.assertTrue(
                any(
                    "response fixture 2.0.0 capabilities" in issue
                    for issue in validate_repository(root)
                )
            )

    def test_response_fixture_media_type_must_match_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / COMPATIBILITY_FIXTURES_PATH
            fixtures = json.loads(path.read_text(encoding="utf-8"))
            fixtures["responses"][0]["media_type"] = "text/html"
            path.write_text(json.dumps(fixtures), encoding="utf-8")
            self.assertIn(
                "response fixture media type mismatch: capabilities_api_v1_foods_capabilities_get",
                validate_repository(root),
            )

    def test_n_minus_one_fixture_uses_the_pinned_n_minus_one_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / COMPATIBILITY_FIXTURES_PATH
            fixtures = json.loads(path.read_text(encoding="utf-8"))
            fixtures["n_minus_one_responses"][1]["body"]["schema_version"] = "2.0"
            path.write_text(json.dumps(fixtures), encoding="utf-8")
            self.assertTrue(
                any("response fixture 1.0.0 search" in issue for issue in validate_repository(root))
            )

    def test_n_minus_one_schema_snapshot_is_digest_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / OPENAPI_N_MINUS_ONE_PATH
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            self.assertIn(
                "N-1 OpenAPI fixture digest does not match its reviewed snapshot",
                validate_repository(root),
            )

    def test_n_minus_one_schema_source_commit_is_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            path = root / COMPATIBILITY_FIXTURES_PATH
            fixtures = json.loads(path.read_text(encoding="utf-8"))
            fixtures["n_minus_one_source_commit"] = "0" * 40
            path.write_text(json.dumps(fixtures), encoding="utf-8")
            self.assertIn(
                "N-1 OpenAPI fixture source commit is not pinned",
                validate_repository(root),
            )

    def test_schema_rejects_unknown_manifest_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy_contract(root)
            mutate_manifest(root, lambda payload: payload.update(secret_override=True))
            self.assertTrue(any("manifest schema" in issue for issue in validate_repository(root)))

    def test_schema_invalid_nested_values_fail_cleanly(self) -> None:
        for field in ("clients", "openapi"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                copy_contract(root)
                mutate_manifest(root, lambda payload, key=field: payload.update({key: None}))
                issues = validate_repository(root)
                self.assertTrue(any(f"manifest schema {field}" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
