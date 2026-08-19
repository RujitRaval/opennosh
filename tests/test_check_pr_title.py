import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import check_pr_title
from scripts.check_pr_title import valid_title


class CheckPullRequestTitleTests(TestCase):
    def test_accepts_gstack_title(self) -> None:
        self.assertTrue(valid_title("v0.1.0.0 feat: validate food packs", "0.1.0.0"))

    def test_rejects_title_without_version(self) -> None:
        self.assertFalse(valid_title("feat: validate food packs"))

    def test_rejects_unknown_change_type(self) -> None:
        self.assertFalse(valid_title("v0.1.0.0 feature: validate food packs"))

    def test_rejects_version_that_does_not_match_repository(self) -> None:
        self.assertFalse(valid_title("v9.9.9.9 feat: validate food packs", "0.1.0.0"))

    def test_accepts_valid_title_without_expected_version(self) -> None:
        self.assertTrue(valid_title("v9.9.9.9 feat: validate food packs"))

    def test_main_rejects_stale_changelog(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            script_path = root / "scripts" / "check_pr_title.py"
            script_path.parent.mkdir()
            (root / "VERSION").write_text("0.1.0.0\n", encoding="utf-8")
            (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
            with patch.object(check_pr_title, "__file__", str(script_path)):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        1,
                        check_pr_title.main(
                            ["check_pr_title.py", "v0.1.0.0 feat: validate food packs"]
                        ),
                    )
