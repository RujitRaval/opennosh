from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

import yaml
from opennosh_api.capacity import CapacityManifest, ProcessRole, load_capacity_manifest

from scripts.check_database_capacity import validate_benchmark_alignment

ROOT = Path(__file__).resolve().parents[1]


class DatabaseCapacityDeploymentTests(unittest.TestCase):
    def test_compose_runs_preflight_then_exactly_one_migration_job(self) -> None:
        compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
        services = compose["services"]

        self.assertIn("capacity-preflight", services)
        manifest = load_capacity_manifest(ROOT / "config/database-capacity.local.v1.json")
        self.assertEqual(
            services["db"]["command"],
            [
                "postgres",
                "-c",
                f"max_connections={manifest.postgresql_connection_ceiling}",
            ],
        )
        preflight_command = services["capacity-preflight"]["command"]
        self.assertIn("--require-live-database", preflight_command)
        self.assertIn("--require-deployment-topology", preflight_command)
        deployed_roles = [
            preflight_command[index + 1]
            for index, value in enumerate(preflight_command[:-1])
            if value == "--deployed-role"
        ]
        self.assertEqual(
            set(deployed_roles),
            {f"{role.value}={manifest.roles[role].replicas}" for role in ProcessRole},
        )
        self.assertEqual(
            services["capacity-preflight"]["depends_on"]["db"]["condition"],
            "service_healthy",
        )
        self.assertIn("migrate", services)
        self.assertEqual(services["migrate"]["command"], ["opennosh-migrate"])
        self.assertEqual(
            services["migrate"]["depends_on"]["capacity-preflight"]["condition"],
            "service_completed_successfully",
        )
        self.assertEqual(
            services["api"]["depends_on"]["migrate"]["condition"],
            "service_completed_successfully",
        )
        self.assertEqual(services["api"]["command"], ["opennosh-web"])

    def test_acceptance_stack_runs_the_declared_real_worker_topology(self) -> None:
        class AcceptanceComposeLoader(yaml.SafeLoader):
            pass

        AcceptanceComposeLoader.add_constructor(
            "!reset",
            lambda loader, node: loader.construct_sequence(node),
        )
        acceptance = yaml.load(
            (ROOT / "compose.acceptance.yaml").read_text(encoding="utf-8"),
            Loader=AcceptanceComposeLoader,
        )
        services = acceptance["services"]
        manifest_path = ROOT / "config/database-capacity.acceptance.v1.json"
        manifest = load_capacity_manifest(manifest_path)

        self.assertEqual(manifest.deployment_id, "browser-acceptance")
        self.assertEqual(manifest.roles[ProcessRole.PUBLICATION].replicas, 1)
        self.assertEqual(manifest.roles[ProcessRole.EVIDENCE].replicas, 1)
        preflight_command = services["capacity-preflight"]["command"]
        deployed_roles = [
            preflight_command[index + 1]
            for index, value in enumerate(preflight_command[:-1])
            if value == "--deployed-role"
        ]
        self.assertEqual(
            set(deployed_roles),
            {f"{role.value}={manifest.roles[role].replicas}" for role in ProcessRole},
        )
        expected_manifest_path = "/app/config/database-capacity.acceptance.v1.json"
        for service_name in ("migrate", "api", "publication-worker", "evidence-worker"):
            self.assertEqual(
                services[service_name]["environment"]["DATABASE_CAPACITY_MANIFEST_PATH"],
                expected_manifest_path,
            )
        self.assertEqual(
            services["publication-worker"]["command"],
            ["opennosh-acceptance-publication-worker"],
        )
        self.assertEqual(
            services["publication-worker"]["environment"]["PUBLICATION_CLAIMS_ENABLED"],
            "true",
        )
        self.assertEqual(
            services["publication-worker"]["environment"]["PUBLICATION_ACTIVATION_IDS"],
            "11111111-1111-4111-8111-111111111111",
        )
        self.assertEqual(
            services["evidence-worker"]["command"],
            ["opennosh-evidence-worker"],
        )
        for worker in ("publication-worker", "evidence-worker"):
            self.assertEqual(
                services[worker]["depends_on"]["migrate"]["condition"],
                "service_completed_successfully",
            )

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn(
            "ACCEPTANCE_PROJECT ?= opennosh-acceptance-$(ACCEPTANCE_PATH_HASH)",
            makefile,
        )
        self.assertIn("--project-name $(ACCEPTANCE_PROJECT)", makefile)
        self.assertIn("publication-worker evidence-worker acceptance-fixture", makefile)
        self.assertIn("artifact-origin api web", makefile)
        self.assertIn("down --volumes --remove-orphans", makefile)

    def test_local_and_production_capacity_manifests_differ_only_by_topology(self) -> None:
        production = json.loads(
            (ROOT / "config/database-capacity.v1.json").read_text(encoding="utf-8")
        )
        local = json.loads(
            (ROOT / "config/database-capacity.local.v1.json").read_text(encoding="utf-8")
        )
        for payload in (production, local):
            payload.pop("manifest_version")
            payload.pop("deployment_id")
        production["roles"]["publication"]["replicas"] = 0

        self.assertEqual(production, local)

    def test_runbook_capacity_arithmetic_matches_the_manifest(self) -> None:
        production = load_capacity_manifest(ROOT / "config/database-capacity.v1.json")
        local = load_capacity_manifest(ROOT / "config/database-capacity.local.v1.json")
        runbook = (ROOT / "docs/operations/database-capacity.md").read_text(encoding="utf-8")

        self.assertIn(
            f"{production.uncommitted_connections} connections remain in production",
            runbook,
        )
        self.assertIn(f"{local.uncommitted_connections} remain locally", runbook)

    def test_web_container_never_runs_migrations_inline(self) -> None:
        dockerfile = (ROOT / "api/Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("alembic", dockerfile)
        self.assertIn('CMD ["opennosh-web"]', dockerfile)

    def test_every_role_has_a_packaged_command_and_credential_identity(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = project["project"]["scripts"]
        manifest = load_capacity_manifest(ROOT / "config/database-capacity.v1.json")

        self.assertEqual(set(manifest.roles), set(ProcessRole))
        for role in ProcessRole:
            command = (
                "opennosh-web"
                if role is ProcessRole.WEB
                else {
                    ProcessRole.PUBLICATION: "opennosh-publication-worker",
                    ProcessRole.EVIDENCE: "opennosh-evidence-worker",
                    ProcessRole.PROJECTION: "opennosh-projection-worker",
                    ProcessRole.RECONCILER: "opennosh-reconciler",
                    ProcessRole.SCHEDULER: "opennosh-scheduler",
                }[role]
            )
            self.assertIn(command, scripts)

    def test_benchmark_alignment_rejects_an_unrepresented_active_role(self) -> None:
        payload = json.loads(
            (ROOT / "config/database-capacity.v1.json").read_text(encoding="utf-8")
        )
        payload["roles"]["evidence"]["replicas"] = 1
        manifest = CapacityManifest.model_validate(payload)

        with self.assertRaisesRegex(ValueError, "exactly the web and bounded publication roles"):
            validate_benchmark_alignment(ROOT, manifest)


if __name__ == "__main__":
    unittest.main()
