import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import check_docs
from scripts.check_docs import CORE_DOCUMENTS, validate_markdown_tree


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
            {"README.md": "# Title\n", ".venv/lib/pkg/README.md": "no heading\n"}
        )
        self.assertEqual([], issues)

    def test_reports_missing_core_documents(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("# Title\n", encoding="utf-8")
            issues = validate_markdown_tree(root)
        expected_missing = len(CORE_DOCUMENTS) - 1
        self.assertEqual(expected_missing, sum("missing core document" in issue for issue in issues))

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
            (root / "README.md").write_text("no H1\n", encoding="utf-8")
            with patch.object(check_docs, "__file__", str(script_path)):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(1, check_docs.main())
