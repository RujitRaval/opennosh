import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import check_docs
from scripts.check_docs import (
    CORE_DOCUMENTS,
    LICENSE_NOTICE_REQUIREMENTS,
    validate_license_notices,
    validate_markdown_tree,
    validate_project_identity,
)


class CheckDocsTests(TestCase):
    def validate(self, files: dict[str, str]) -> list[str]:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            return validate_markdown_tree(root, require_core=False)

    def test_accepts_well_formed_document_and_existing_link(self) -> None:
        issues = self.validate(
            {
                "README.md": "# Title\n\n## Section\n\nSee [details](docs/details.md).\n",
                "docs/details.md": "# Details\n",
            }
        )
        self.assertEqual([], issues)

    def test_rejects_missing_link_target_and_heading_jump(self) -> None:
        issues = self.validate(
            {"README.md": "# Title\n\n### Skipped\n\n[missing](missing.md)\n"}
        )
        self.assertTrue(any("heading jumps" in issue for issue in issues))
        self.assertTrue(any("link target does not exist" in issue for issue in issues))

    def test_rejects_multiple_h1s_and_unclosed_fence(self) -> None:
        issues = self.validate(
            {"README.md": "# First\n\n# Second\n\n```text\nnot closed\n"}
        )
        self.assertTrue(any("expected exactly one H1" in issue for issue in issues))
        self.assertTrue(any("unclosed fenced code block" in issue for issue in issues))

    def test_rejects_link_that_escapes_repository(self) -> None:
        issues = self.validate({"README.md": "# Title\n\n[outside](/etc/passwd)\n"})
        self.assertTrue(any("link escapes repository" in issue for issue in issues))

    def test_accepts_nested_fence_inline_example_and_angle_destination(self) -> None:
        issues = self.validate(
            {
                "README.md": (
                    "# Title\n\n````markdown\n```text\n# Fake\n```\n````\n\n"
                    "Use `[bad](missing.md)` as syntax.\n\n[good](<docs/my file.md>)\n"
                ),
                "docs/my file.md": "# Linked document\n",
            }
        )
        self.assertEqual([], issues)

    def test_validates_reference_link_definition(self) -> None:
        issues = self.validate(
            {"README.md": "# Title\n\nRead [the guide][guide].\n\n[guide]: missing.md\n"}
        )
        self.assertTrue(any("link target does not exist" in issue for issue in issues))

    def test_ignores_generated_environment_directories(self) -> None:
        issues = self.validate(
            {
                "README.md": "# Title\n",
                ".gstack/pr-body.md": "OpenPlate generated scratch content\n",
                ".venv/lib/pkg/README.md": "no heading\n",
            }
        )
        self.assertEqual([], issues)

    def test_reports_missing_core_documents(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Title\n", encoding="utf-8")
            issues = validate_markdown_tree(root)
        expected_missing = len(CORE_DOCUMENTS) - 1
        self.assertEqual(expected_missing, sum("missing core document" in issue for issue in issues))

    def test_core_documents_use_settled_decision_record(self) -> None:
        self.assertIn("08-PRODUCT-DECISIONS.md", CORE_DOCUMENTS)
        self.assertIn("LICENSES.md", CORE_DOCUMENTS)
        self.assertIn("NOTICE.md", CORE_DOCUMENTS)
        self.assertIn("docs/license-notice-review.md", CORE_DOCUMENTS)
        self.assertNotIn("08-OPEN-QUESTIONS.md", CORE_DOCUMENTS)

    def test_license_notice_surfaces_require_every_source_family(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for relative, fragments in LICENSE_NOTICE_REQUIREMENTS.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(fragments), encoding="utf-8")
            self.assertEqual([], validate_license_notices(root))

            (root / "NOTICE.md").write_text("MIT License\n", encoding="utf-8")
            issues = validate_license_notices(root)

        self.assertTrue(any("NOTICE.md" in issue and "CC0 1.0 Universal" in issue for issue in issues))

    def test_license_notice_surfaces_report_missing_files(self) -> None:
        with TemporaryDirectory() as directory:
            issues = validate_license_notices(Path(directory))
        self.assertEqual(len(LICENSE_NOTICE_REQUIREMENTS), len(issues))
        self.assertTrue(all("missing license-notice surface" in issue for issue in issues))

    def test_rejects_retired_project_identity_references(self) -> None:
        issues = self.validate(
            {
                "README.md": (
                    "# Title\n\nOpenPlate used `open-plate` and "
                    "`08-OPEN-QUESTIONS.md`.\n"
                )
            }
        )
        self.assertEqual(2, sum("retired project identity" in issue for issue in issues))
        self.assertEqual(1, sum("retired decision filename" in issue for issue in issues))

    def test_rejects_identity_variants_in_user_facing_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".github" / "ISSUE_TEMPLATE" / "bug.yml"
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                "name: Open Plate\ndescription: OPENPLATE\nlabel: OpenNosh\n",
                encoding="utf-8",
            )
            issues = validate_project_identity(root)
        self.assertEqual(2, sum("retired project identity" in issue for issue in issues))
        self.assertEqual(1, sum("exact lowercase" in issue for issue in issues))

    def test_identity_scan_handles_suffix_boundaries_and_ignored_directories(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            uppercase_yaml = root / "metadata.YML"
            uppercase_yaml.write_text("name: OPENPLATE\nnote: OpenPlateau\n", encoding="utf-8")
            ignored = root / "node_modules" / "package.yml"
            ignored.parent.mkdir()
            ignored.write_text("name: OpenPlate\n", encoding="utf-8")
            issues = validate_project_identity(root)
        self.assertEqual(1, sum("retired project identity" in issue for issue in issues))

    def test_yaml_identity_is_reported_even_without_markdown(self) -> None:
        issues = self.validate({"metadata.yml": "name: Open Plate\n"})
        self.assertIn("repository contains no Markdown documents", issues)
        self.assertTrue(any("retired project identity" in issue for issue in issues))

    def test_reports_empty_invalid_utf8_and_trailing_whitespace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty.md").write_text("", encoding="utf-8")
            (root / "invalid.md").write_bytes(b"\xff")
            (root / "trailing.md").write_text("# Title \n", encoding="utf-8")
            issues = validate_markdown_tree(root, require_core=False)
        self.assertTrue(any("document is empty" in issue for issue in issues))
        self.assertTrue(any("not valid UTF-8" in issue for issue in issues))
        self.assertTrue(any("trailing whitespace" in issue for issue in issues))

    def test_reports_repository_with_no_markdown(self) -> None:
        self.assertEqual(["repository contains no Markdown documents"], self.validate({}))

    def test_skips_non_file_targets_and_decodes_local_paths(self) -> None:
        issues = self.validate(
            {
                "README.md": (
                    "# Title\n\n[anchor](#part) [mail](mailto:test@example.com) "
                    "[empty]() [local](docs/my%20file.md#section)\n"
                ),
                "docs/my file.md": "# Linked\n",
            }
        )
        self.assertEqual([], issues)

    def test_main_returns_failure_for_invalid_repository(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            script_path = root / "scripts" / "check_docs.py"
            script_path.parent.mkdir()
            (root / "README.md").write_text("# OpenPlate\n", encoding="utf-8")
            with patch.object(check_docs, "__file__", str(script_path)):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(1, check_docs.main())
        self.assertIn("Repository documentation validation failed:", output.getvalue())
        self.assertIn("retired project identity", output.getvalue())
