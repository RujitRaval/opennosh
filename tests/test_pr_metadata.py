from __future__ import annotations

import unittest

from scripts.check_pr_metadata import valid_metadata


class PullRequestMetadataTests(unittest.TestCase):
    def test_versioned_codex_release_is_accepted(self) -> None:
        self.assertTrue(
            valid_metadata(
                "codex/t2-governed-forge-merge",
                "v0.49.0.0 feat: add governed forge merge",
                expected_version="0.49.0.0",
                changelog="## [0.49.0.0] - 2026-08-25",
            )
        )

    def test_exact_governed_branch_and_title_are_accepted(self) -> None:
        self.assertTrue(
            valid_metadata(
                "opennosh/contribution/0123456789abcdef01234567",
                "Governed contribution: global-core",
                expected_version="0.49.0.0",
                changelog="",
            )
        )

    def test_spoofed_governed_metadata_is_rejected(self) -> None:
        cases = (
            ("opennosh/contribution/not-a-digest", "Governed contribution: global-core"),
            (
                "opennosh/contribution/0123456789abcdef01234567",
                "Governed contribution: ../other-pack",
            ),
            ("feature/untrusted", "Governed contribution: global-core"),
        )
        for branch, title in cases:
            with self.subTest(branch=branch, title=title):
                self.assertFalse(
                    valid_metadata(
                        branch,
                        title,
                        expected_version="0.49.0.0",
                        changelog="## [0.49.0.0]",
                    )
                )


if __name__ == "__main__":
    unittest.main()
