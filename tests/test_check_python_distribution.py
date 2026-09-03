from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.check_python_distribution import (
    EXPECTED_CONSOLE_SCRIPTS,
    validate_distribution,
)

VERSION = "301.2.3.4"
DIST_INFO = f"opennosh-{VERSION}.dist-info"
ENTRY_POINTS = (
    "[console_scripts]\n"
    + "".join(f"{command}={target}\n" for command, target in EXPECTED_CONSOLE_SCRIPTS.items())
).encode()
WHEEL_MEMBERS = {
    "opennosh_api/foodpacks/food-pack.schema.json": b"{}",
    "opennosh_api/database-capacity.v1.json": b"{}",
    "opennosh_api/publication-claims-activation.v1.json": b"{}",
    "opennosh_api/natural-publication-proof-activation.v1.json": b"{}",
    "opennosh_api/contracts/developer-compatibility.schema.json": b"{}",
    "opennosh_api/contracts/developer-compatibility.v1.json": b"{}",
    f"{DIST_INFO}/entry_points.txt": ENTRY_POINTS,
    f"{DIST_INFO}/licenses/AUTHORS.md": b"contributors",
    f"{DIST_INFO}/licenses/LICENSE": b"MIT",
    f"{DIST_INFO}/licenses/LICENSES.md": b"licenses",
    f"{DIST_INFO}/licenses/NOTICE.md": b"notices",
    f"{DIST_INFO}/METADATA": (
        b"Name: opennosh\nVersion: 301.2.3.4\nRequires-Dist: mcp<3,>=2\n"
    ),
}
SDIST_MEMBERS = (
    "VERSION",
    "schemas/food-pack.schema.json",
    "config/database-capacity.v1.json",
    "config/publication-claims-activation.v1.json",
    "config/natural-publication-proof-activation.v1.json",
    "schemas/developer-compatibility.schema.json",
    "config/developer-compatibility.v1.json",
    "NOTICE.md",
    "LICENSES.md",
)


def write_archives(
    root: Path,
    *,
    wheel_members: dict[str, bytes] | None = None,
    sdist_members: tuple[str, ...] = SDIST_MEMBERS,
) -> Path:
    (root / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
    dist = root / "dist"
    dist.mkdir()
    wheel = dist / f"opennosh-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, contents in (wheel_members or WHEEL_MEMBERS).items():
            archive.writestr(name, contents)

    prefix = f"opennosh-{VERSION}"
    with tarfile.open(dist / f"{prefix}.tar.gz", "w:gz") as archive:
        for relative in sdist_members:
            contents = b"fixture"
            info = tarfile.TarInfo(f"{prefix}/{relative}")
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))
    return dist


class PythonDistributionCheckTests(unittest.TestCase):
    def test_valid_artifacts_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = write_archives(root)
            self.assertEqual([], validate_distribution(root, dist))

    def test_missing_artifacts_are_reported_before_archive_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
            dist = root / "dist"
            dist.mkdir()

            issues = validate_distribution(root, dist)

        self.assertEqual(2, len(issues))
        self.assertTrue(any("expected one" in issue and "wheel" in issue for issue in issues))
        self.assertTrue(any("source archive" in issue for issue in issues))

    def test_missing_wheel_member_and_bad_metadata_are_reported(self) -> None:
        members = dict(WHEEL_MEMBERS)
        del members[f"{DIST_INFO}/entry_points.txt"]
        members[f"{DIST_INFO}/METADATA"] = b"Name: wrong\nVersion: 9.9.9.9\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            issues = validate_distribution(
                root,
                write_archives(root, wheel_members=members),
            )

        self.assertIn(f"wheel: missing {DIST_INFO}/entry_points.txt", issues)
        self.assertIn("wheel metadata: Name must be opennosh", issues)
        self.assertIn("wheel metadata: Version must match VERSION", issues)

    def test_invalid_production_entry_point_is_reported(self) -> None:
        members = dict(WHEEL_MEMBERS)
        members[f"{DIST_INFO}/entry_points.txt"] = ENTRY_POINTS.replace(
            b"opennosh-web=opennosh_api.entrypoints.web:main",
            b"opennosh-web=opennosh_api.entrypoints.web:missing",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            issues = validate_distribution(root, write_archives(root, wheel_members=members))

        self.assertIn(
            "wheel entry point: opennosh-web must resolve to opennosh_api.entrypoints.web:main",
            issues,
        )

    def test_missing_source_member_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            issues = validate_distribution(
                root,
                write_archives(root, sdist_members=SDIST_MEMBERS[:-1]),
            )

        self.assertIn("source archive: missing LICENSES.md", issues)


if __name__ == "__main__":
    unittest.main()
