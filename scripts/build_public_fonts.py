#!/usr/bin/env python3
"""Rebuild the immutable Living Commons WOFF2 subsets from licensed sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fontTools import __version__ as fonttools_version
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
FONT_NAME_IDS = {
    1: "family",
    2: "subfamily",
    3: "unique",
    4: "full",
    6: "postScript",
    16: "family",
    17: "subfamily",
    21: "family",
    22: "subfamily",
    25: "postScript",
}
MANIFEST = ROOT / "web/assets/fonts/v2/font-build.v2.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str], *, environment: dict[str, str]) -> None:
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def rename_output_font(path: Path, names: dict[str, str], reserved_names: list[str]) -> None:
    font = TTFont(path, recalcTimestamp=False)
    name_table = font["name"]
    resolved_names = {**names, "unique": f"opennosh;{names['postScript']}"}
    if any(
        reserved.casefold() in value.casefold()
        for reserved in reserved_names
        for value in resolved_names.values()
    ):
        raise SystemExit(f"Reserved font name used by derivative: {path.name}")
    for record in list(name_table.names):
        key = FONT_NAME_IDS.get(record.nameID)
        if key:
            name_table.setName(
                resolved_names[key],
                record.nameID,
                record.platformID,
                record.platEncID,
                record.langID,
            )
    font.save(path, reorderTables=True)
    font.close()


def build_font(
    font: dict[str, Any],
    manifest: dict[str, Any],
    destination: Path,
    temporary: Path,
) -> None:
    manifest_root = MANIFEST.parent
    source = (manifest_root / font["source"]).resolve()
    if digest(source) != font["sourceSha256"]:
        raise SystemExit(f"Source digest drift: {source.relative_to(ROOT)}")

    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(manifest["tool"]["sourceDateEpoch"])
    subset_input = source
    if font["axisLimits"]:
        subset_input = temporary / f"{font['id']}-limited.woff2"
        run(
            [
                sys.executable,
                "-m",
                "fontTools.varLib.instancer",
                str(source),
                *font["axisLimits"],
                "--no-recalc-timestamp",
                "-o",
                str(subset_input),
            ],
            environment=environment,
        )

    unicode_file = (manifest_root / manifest["scripts"][font["script"]]["unicodes"]).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            "-m",
            "fontTools.subset",
            str(subset_input),
            f"--output-file={destination}",
            f"--unicodes-file={unicode_file}",
            "--flavor=woff2",
            "--layout-features=*",
            "--name-IDs=0,1,2,3,4,5,6",
            "--name-languages=0x409",
            "--notdef-glyph",
            "--notdef-outline",
            "--recommended-glyphs",
            "--canonical-order",
            "--no-recalc-timestamp",
        ],
        environment=environment,
    )

    rename_output_font(destination, font["outputNames"], font["reservedNames"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="replace committed outputs")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    expected_version = manifest["tool"]["version"]
    if fonttools_version != expected_version:
        raise SystemExit(
            f"FontTools version drift: expected {expected_version}, running {fonttools_version}"
        )

    with tempfile.TemporaryDirectory(prefix="opennosh-fonts-") as directory:
        temporary = Path(directory)
        generated = temporary / "generated"
        for license_notice in manifest["licenses"]:
            source = (MANIFEST.parent / license_notice["source"]).resolve()
            public = (MANIFEST.parent / license_notice["public"]).resolve()
            if digest(source) != license_notice["sha256"]:
                raise SystemExit(f"License digest drift: {source.relative_to(ROOT)}")
            if args.write:
                public.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, public)
            elif not public.exists() or source.read_bytes() != public.read_bytes():
                raise SystemExit(f"Published license drift: {public.relative_to(ROOT)}")

        for font in manifest["fonts"]:
            built = generated / Path(font["output"]).name
            build_font(font, manifest, built, temporary)
            committed = (MANIFEST.parent / font["output"]).resolve()
            if args.write:
                committed.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(built, committed)
                font["outputBytes"] = committed.stat().st_size
                font["outputSha256"] = digest(committed)
            elif not committed.exists() or built.read_bytes() != committed.read_bytes():
                raise SystemExit(f"Generated font drift: {committed.relative_to(ROOT)}")

    if args.write:
        MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        print(f"Rebuilt {len(manifest['fonts'])} public font subsets and refreshed hashes.")
    else:
        print(f"Reproducible font build verified for {len(manifest['fonts'])} WOFF2 files.")


if __name__ == "__main__":
    main()
