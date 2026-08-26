from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Self

_PACK_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,158}[a-z0-9])?$")
CANONICAL_FORGE_TARGET = "github:RujitRaval/opennosh"

PROTECTED_STATUS_CHECKS = (
    "API checks",
    "Compose application boot",
    "OpenNosh governance attestation",
    "repository checks",
    "visual regression",
    "web checks",
)
GOVERNANCE_TRUST_CHECKS = (
    "authorization",
    "evidence",
    "license",
    "payload",
    "provenance",
    "schema",
    "self-review",
)


class GovernanceRole(StrEnum):
    STEWARD = "steward"


class GovernanceDecisionOutcome(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ApprovedFileChange:
    path: str
    content: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.path)
        if (
            not self.path
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in self.path
            or self.path != path.as_posix()
        ):
            raise ValueError("Approved file path must be a normalized relative POSIX path")
        encoded = self.content.encode("utf-8")
        if b"\x00" in encoded:
            raise ValueError("Approved file content cannot contain NUL bytes")
        if len(encoded) > 1_048_576:
            raise ValueError("Approved file content cannot exceed one MiB")

    @property
    def content_digest(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def as_json(self) -> dict[str, str]:
        return {
            "path": self.path,
            "content": self.content,
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True, slots=True)
class ApprovedChangeSet:
    pack_id: str
    files: tuple[ApprovedFileChange, ...]

    def __post_init__(self) -> None:
        if not _PACK_ID.fullmatch(self.pack_id):
            raise ValueError("Approved change set requires a normalized pack ID")
        if not self.files or len(self.files) > 32:
            raise ValueError("Approved change set requires between one and 32 files")
        expected_prefix = PurePosixPath("packs", self.pack_id)
        paths = tuple(file.path for file in self.files)
        if paths != tuple(sorted(paths)):
            raise ValueError("Approved files must be sorted by path")
        if len(set(paths)) != len(paths):
            raise ValueError("Approved file paths must be unique")
        for file in self.files:
            path = PurePosixPath(file.path)
            if path == expected_prefix or not path.is_relative_to(expected_prefix):
                raise ValueError("Approved files must remain inside their governed pack")

    @classmethod
    def build(
        cls,
        *,
        pack_id: str,
        files: tuple[ApprovedFileChange, ...],
    ) -> Self:
        return cls(pack_id=pack_id, files=tuple(sorted(files, key=lambda item: item.path)))

    @property
    def digest(self) -> str:
        material = json.dumps(
            {
                "schema_version": "1.0",
                "pack_id": self.pack_id,
                "files": [
                    {"path": file.path, "content_digest": file.content_digest}
                    for file in self.files
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def as_json(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "pack_id": self.pack_id,
            "digest": self.digest,
            "files": [file.as_json() for file in self.files],
        }

    @classmethod
    def from_json(cls, value: object) -> Self:
        if not isinstance(value, dict):
            raise ValueError("Approved change set must be an object")
        if value.get("schema_version") != "1.0":
            raise ValueError("Approved change set schema version is unsupported")
        raw_files = value.get("files")
        if not isinstance(raw_files, list):
            raise ValueError("Approved change set files must be an array")
        files: list[ApprovedFileChange] = []
        for raw in raw_files:
            if not isinstance(raw, dict):
                raise ValueError("Approved change set file must be an object")
            path = raw.get("path")
            content = raw.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                raise ValueError("Approved file path and content must be strings")
            file = ApprovedFileChange(path=path, content=content)
            if raw.get("content_digest") != file.content_digest:
                raise ValueError("Approved file content digest does not match")
            files.append(file)
        pack_id = value.get("pack_id")
        if not isinstance(pack_id, str):
            raise ValueError("Approved change set pack ID must be a string")
        result = cls.build(pack_id=pack_id, files=tuple(files))
        if value.get("digest") != result.digest:
            raise ValueError("Approved change set digest does not match")
        return result
