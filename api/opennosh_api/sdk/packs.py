"""Bounded JSON and ZIP loading for the public ``packs validate`` command."""

from __future__ import annotations

import io
import json
import os
import stat
import struct
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from opennosh_api.foodpacks.validation import (
    MAX_FOOD_FILES,
    MAX_PACK_BYTES,
    MAX_PACK_ENTRIES,
    MAX_YAML_FILE_BYTES,
    ValidationIssue,
    ValidationReport,
    parse_pack_foods,
    parse_pack_manifest,
    validate_pack_document,
)

MAX_ARCHIVE_BYTES = 67_108_864
MAX_ARCHIVE_MEMBERS = MAX_FOOD_FILES + 4
MAX_CENTRAL_DIRECTORY_BYTES = 131_072
_ROOT_FILES = {"pack.yaml", "README.md", "CC0-1.0.txt", "LICENSE.md"}
_END_OF_CENTRAL_DIRECTORY = b"PK\x05\x06"
_ZIP64_END_OF_CENTRAL_DIRECTORY_LOCATOR = b"PK\x06\x07"


class PackInputError(ValueError):
    """A stable, non-secret description of an unsupported or unsafe input."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error_report(error: PackInputError) -> ValidationReport:
    return ValidationReport(
        errors=(
            ValidationIssue(
                severity="error",
                code=error.code,
                message=str(error),
            ),
        )
    )


def _read_bounded(path: Path, limit: int) -> bytes:
    descriptor: int | None = None
    try:
        path_metadata = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(path_metadata.st_mode):
            raise PackInputError("input_invalid", "input must be one regular file")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != path_metadata.st_dev
            or metadata.st_ino != path_metadata.st_ino
        ):
            raise PackInputError("input_invalid", "input must be one regular file")
        if metadata.st_size > limit:
            raise PackInputError("input_too_large", f"input cannot exceed {limit} bytes")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            data = handle.read(limit + 1)
    except PackInputError:
        raise
    except OSError as error:
        raise PackInputError("input_unreadable", "input could not be read") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(data) > limit:
        raise PackInputError("input_too_large", f"input cannot exceed {limit} bytes")
    return data


def _json_document(path: Path) -> Mapping[str, object]:
    payload = _read_bounded(path, MAX_PACK_BYTES)
    try:
        document: Any = json.loads(
            payload.decode("utf-8", errors="strict"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PackInputError("json_invalid", "JSON input is invalid") from error
    if not isinstance(document, Mapping):
        raise PackInputError("json_not_object", "JSON input must contain one object")
    return document


def _allowed_member(path: PurePosixPath) -> bool:
    if len(path.parts) == 1:
        return path.name in _ROOT_FILES
    return len(path.parts) == 2 and path.parts[0] == "foods" and path.suffix in {".yaml", ".yml"}


def _decode_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    try:
        with archive.open(info) as handle:
            payload = handle.read(MAX_YAML_FILE_BYTES + 1)
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise PackInputError("archive_invalid", "ZIP input is invalid") from error
    if len(payload) != info.file_size or len(payload) > MAX_YAML_FILE_BYTES:
        raise PackInputError(
            "archive_member_too_large",
            f"pack YAML members cannot exceed {MAX_YAML_FILE_BYTES} bytes",
        )
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise PackInputError("archive_encoding_invalid", "pack YAML must be valid UTF-8") from error


def _zip_document(path: Path) -> Mapping[str, object]:
    payload = _read_bounded(path, MAX_ARCHIVE_BYTES)
    _preflight_zip_directory(payload)
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (OSError, zipfile.BadZipFile) as error:
        raise PackInputError("archive_invalid", "ZIP input is invalid") from error
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_ARCHIVE_MEMBERS:
            raise PackInputError("archive_invalid", "ZIP input has an invalid member count")
        names: set[str] = set()
        total_bytes = 0
        food_infos: list[zipfile.ZipInfo] = []
        manifest_info: zipfile.ZipInfo | None = None
        for info in infos:
            name = info.filename
            member_path = PurePosixPath(name)
            mode = info.external_attr >> 16
            if (
                not name
                or name in names
                or "\\" in name
                or member_path.is_absolute()
                or ".." in member_path.parts
                or info.is_dir()
                or info.flag_bits & 0x1
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or stat.S_ISLNK(mode)
                or not _allowed_member(member_path)
            ):
                raise PackInputError("archive_invalid", "ZIP input contains an unsafe member")
            names.add(name)
            total_bytes += info.file_size
            if total_bytes > MAX_PACK_BYTES:
                raise PackInputError(
                    "archive_too_large", f"pack YAML cannot exceed {MAX_PACK_BYTES} bytes"
                )
            if name == "pack.yaml":
                manifest_info = info
            elif member_path.parent == PurePosixPath("foods"):
                food_infos.append(info)
        if manifest_info is None or not food_infos or len(food_infos) > MAX_FOOD_FILES:
            raise PackInputError(
                "archive_incomplete", "ZIP input must contain pack.yaml and foods/*.yaml"
            )
        try:
            manifest = parse_pack_manifest(_decode_member(archive, manifest_info))
            foods: list[object] = []
            for info in sorted(food_infos, key=lambda item: item.filename):
                foods.extend(parse_pack_foods(_decode_member(archive, info)))
                if len(foods) > MAX_PACK_ENTRIES:
                    raise PackInputError(
                        "too_many_entries",
                        f"a food pack cannot contain more than {MAX_PACK_ENTRIES} entries",
                    )
        except ValueError as error:
            if isinstance(error, PackInputError):
                raise
            raise PackInputError("archive_schema_invalid", str(error)) from error
    return {"pack": manifest, "foods": foods}


def _preflight_zip_directory(payload: bytes) -> None:
    minimum = max(0, len(payload) - 65_557)
    offset = payload.rfind(_END_OF_CENTRAL_DIRECTORY, minimum)
    if offset < 0 or len(payload) - offset < 22:
        raise PackInputError("archive_invalid", "ZIP input has an invalid central directory")
    try:
        (
            signature,
            disk,
            directory_disk,
            disk_members,
            total_members,
            directory_size,
            directory_offset,
            comment_size,
        ) = struct.unpack_from("<4s4H2LH", payload, offset)
    except struct.error as error:
        raise PackInputError(
            "archive_invalid", "ZIP input has an invalid central directory"
        ) from error
    has_zip64_locator = (
        offset >= 20
        and payload[offset - 20 : offset - 16] == _ZIP64_END_OF_CENTRAL_DIRECTORY_LOCATOR
    )
    if not (
        signature == _END_OF_CENTRAL_DIRECTORY
        and not has_zip64_locator
        and disk == 0
        and directory_disk == 0
        and disk_members == total_members
        and 0 < total_members <= MAX_ARCHIVE_MEMBERS
        and directory_size <= MAX_CENTRAL_DIRECTORY_BYTES
        and directory_offset + directory_size <= offset
        and offset + 22 + comment_size == len(payload)
    ):
        raise PackInputError("archive_invalid", "ZIP input has an invalid central directory")


def validate_pack_input(path: str | Path) -> ValidationReport:
    """Validate one normalized JSON document or one bounded source ZIP."""

    input_path = Path(path)
    try:
        suffix = input_path.suffix.lower()
        if suffix == ".json":
            document = _json_document(input_path)
        elif suffix == ".zip":
            document = _zip_document(input_path)
        else:
            raise PackInputError("input_format_unsupported", "input must be JSON or ZIP")
    except PackInputError as error:
        return _error_report(error)
    return validate_pack_document(document)
