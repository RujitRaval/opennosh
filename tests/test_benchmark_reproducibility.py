from __future__ import annotations

import asyncio
import json
import unittest

from benchmarks.performance.contract import load_contract
from benchmarks.performance.database import seed_database
from benchmarks.performance.reproducibility import REDACTED, sanitized_command


class BenchmarkReproducibilityTests(unittest.TestCase):
    def test_sanitizes_split_database_url_and_boundary_values(self) -> None:
        database_url = (
            "postgresql+asyncpg://"
            + "runner"
            + ":"
            + "database-secret"
            + "@"
            + "db.example/opennosh_benchmark?sslpassword=query-secret&sslmode=require"
        )
        edge_url = (
            "edge_browser=https://"
            + "edge-user"
            + ":"
            + "edge-secret"
            + "@"
            + "example.org/search?token=edge-token"
        )
        command = sanitized_command(
            [
                "benchmark",
                "--database-url",
                database_url,
                "--boundary",
                edge_url,
                "--profile",
                "launch-reference",
            ]
        )

        serialized = json.dumps(command)
        for secret in (
            "runner",
            "database-secret",
            "query-secret",
            "edge-user",
            "edge-secret",
            "edge-token",
        ):
            self.assertNotIn(secret, serialized)
        self.assertEqual(
            command[2],
            "postgresql+asyncpg://db.example/opennosh_benchmark"
            "?sslpassword=%5BREDACTED%5D&sslmode=%5BREDACTED%5D",
        )
        self.assertEqual(
            command[4],
            "edge_browser=https://example.org/search?token=%5BREDACTED%5D",
        )
        self.assertEqual(command[-2:], ["--profile", "launch-reference"])

    def test_sanitizes_equals_form_and_malformed_sensitive_values(self) -> None:
        database_argument = (
            "--database-url=postgresql+asyncpg://"
            + "runner"
            + ":"
            + "secret"
            + "@"
            + "db.example/benchmark"
        )
        command = sanitized_command(
            [
                "benchmark",
                database_argument,
                "--boundary=edge_browser=https://example.org/?signature=secret-signature",
                "--boundary",
                "not-a-labeled-url",
            ]
        )

        serialized = json.dumps(command)
        self.assertNotIn("runner", serialized)
        self.assertNotIn("secret-signature", serialized)
        self.assertEqual(
            command[1],
            "--database-url=postgresql+asyncpg://db.example/benchmark",
        )
        self.assertEqual(
            command[2],
            "--boundary=edge_browser=https://example.org/?signature=%5BREDACTED%5D",
        )
        self.assertEqual(command[-1], REDACTED)

    def test_seed_database_rejects_direct_non_benchmark_calls(self) -> None:
        with self.assertRaisesRegex(ValueError, "refusing to replace data"):
            asyncio.run(
                seed_database(
                    "postgresql+asyncpg://"
                    + "runner"
                    + ":"
                    + "secret"
                    + "@"
                    + "db.example/opennosh_production",
                    load_contract(),
                    "launch-reference",
                )
            )
        with self.assertRaisesRegex(ValueError, "refusing to replace data"):
            asyncio.run(
                seed_database(
                    "postgresql+asyncpg://db.example/opennosh_production?redirect=/benchmark",
                    load_contract(),
                    "launch-reference",
                )
            )


if __name__ == "__main__":
    unittest.main()
