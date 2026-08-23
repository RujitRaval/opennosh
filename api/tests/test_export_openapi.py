from __future__ import annotations

from pathlib import Path

from scripts.export_openapi import export_openapi


def test_export_uses_repository_version_not_installed_metadata(tmp_path: Path) -> None:
    destination = tmp_path / "openapi.json"

    schema = export_openapi(destination)

    assert schema["info"]["version"] == Path("VERSION").read_text(encoding="utf-8").strip()
    assert destination.is_file()
