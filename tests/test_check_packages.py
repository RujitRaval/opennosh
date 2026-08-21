from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.check_packages import npm_version, validate_repository

ROOT = Path(__file__).resolve().parents[1]


class PackageCheckTests(unittest.TestCase):
    def test_repository_package_identities_are_aligned(self) -> None:
        self.assertEqual([], validate_repository(ROOT))

    def test_npm_version_uses_the_registry_compatible_release_prefix(self) -> None:
        self.assertEqual("2.3.4", npm_version("2.3.4.5"))
        with self.assertRaisesRegex(ValueError, "four numeric components"):
            npm_version("2.3.4")
        with self.assertRaisesRegex(ValueError, "four numeric components"):
            npm_version("2.3.x.5")

    def test_invalid_repository_version_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text("invalid\n", encoding="utf-8")

            self.assertEqual(
                ["VERSION: VERSION must contain four numeric components"],
                validate_repository(root),
            )

    def test_stale_npm_package_version_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "packages" / "npm").mkdir(parents=True)
            for relative in (
                "VERSION",
                "pyproject.toml",
                "LICENSE",
                "packages/npm/LICENSE",
                "packages/npm/package-lock.json",
            ):
                source = ROOT / relative
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            metadata = json.loads((ROOT / "packages/npm/package.json").read_text())
            metadata["version"] = "9.9.9"
            (root / "packages/npm/package.json").write_text(json.dumps(metadata))

            issues = validate_repository(root)

        self.assertTrue(any("version must match" in issue for issue in issues))

    def test_all_package_identity_mismatches_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "packages", root / "packages")
            for relative in ("VERSION", "pyproject.toml", "LICENSE"):
                shutil.copyfile(ROOT / relative, root / relative)

            pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
            pyproject = pyproject.replace('name = "opennosh"', 'name = "wrong"', 1)
            pyproject = pyproject.replace('dynamic = ["version"]', "dynamic = []", 1)
            pyproject = pyproject.replace('path = "VERSION"', 'path = "OTHER"', 1)
            (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")

            package_path = root / "packages/npm/package.json"
            package = json.loads(package_path.read_text(encoding="utf-8"))
            package.update({"name": "wrong", "private": True, "bin": {}})
            package_path.write_text(json.dumps(package), encoding="utf-8")
            (root / "packages/npm/LICENSE").write_text("wrong", encoding="utf-8")

            lock_path = root / "packages/npm/package-lock.json"
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock.update({"name": "wrong", "version": "9.9.9"})
            lock["packages"][""].update({"name": "wrong", "version": "9.9.9"})
            lock_path.write_text(json.dumps(lock), encoding="utf-8")

            issues = validate_repository(root)

        expected_fragments = (
            "public project name",
            "version must be dynamic",
            "Hatch must read",
            "public package name",
            "cannot be private",
            "executable is missing",
            "must match the repository MIT license",
            "root identity is stale",
            "package identity is stale",
        )
        for fragment in expected_fragments:
            self.assertTrue(any(fragment in issue for issue in issues), fragment)


if __name__ == "__main__":
    unittest.main()
