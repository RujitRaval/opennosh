import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from opennosh_api.public_commons.schemas import CommonsSnapshotState, PublicCommonsSnapshot

MAX_PUBLIC_SNAPSHOT_BYTES = 24 * 1024
MAX_STORED_PROJECTION_BYTES = 32 * 1024


class PointerRevision(BaseModel):
    """Filesystem identity captured from the exact latest-pointer bytes read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device: Annotated[int, Field(ge=0)]
    inode: Annotated[int, Field(ge=0)]
    size: Annotated[int, Field(ge=0)]
    modified_ns: Annotated[int, Field(ge=0)]
    changed_ns: Annotated[int, Field(ge=0)]

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "PointerRevision":
        return cls(
            device=value.st_dev,
            inode=value.st_ino,
            size=value.st_size,
            modified_ns=value.st_mtime_ns,
            changed_ns=value.st_ctime_ns,
        )


class PublicCommonsProjection(BaseModel):
    """Rebuildable, non-canonical homepage proof projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    source_release_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_release_version: Annotated[
        str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")
    ]
    event_checkpoint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    activity_cutoff: datetime
    as_of_bucket: datetime
    built_at: datetime
    pointer_revision: PointerRevision
    snapshot: PublicCommonsSnapshot

    @field_validator("activity_cutoff", "as_of_bucket", "built_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("projection timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> "PublicCommonsProjection":
        release = self.snapshot.release
        if release is None:
            raise ValueError("stored projections require verified release proof")
        if release.manifest_digest != self.source_release_digest:
            raise ValueError("projection digest and snapshot release disagree")
        if release.version != self.source_release_version:
            raise ValueError("projection version and snapshot release disagree")
        if self.snapshot.as_of != self.as_of_bucket:
            raise ValueError("projection bucket and snapshot as_of disagree")
        if self.snapshot.activity.ends_at != self.activity_cutoff:
            raise ValueError("projection cutoff and activity window disagree")
        if self.snapshot.state not in {
            CommonsSnapshotState.LIVE,
            CommonsSnapshotState.QUIET,
            CommonsSnapshotState.PARTIAL,
        }:
            raise ValueError("stored projections must contain currently verified proof")
        expected_snapshot_id = hashlib.sha256(self.cache_identity()).hexdigest()[:32]
        if self.snapshot.snapshot_id != expected_snapshot_id:
            raise ValueError("projection identity and snapshot ID disagree")
        if len(self.snapshot.model_dump_json().encode()) > MAX_PUBLIC_SNAPSHOT_BYTES:
            raise ValueError("public commons snapshot exceeds its response budget")
        return self

    def cache_identity(self) -> bytes:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "release_digest": self.source_release_digest,
                "event_checkpoint": self.event_checkpoint,
                "activity_cutoff": self.activity_cutoff.isoformat(),
                "as_of_bucket": self.as_of_bucket.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def content_digest(self) -> str:
        """Bind every persisted projection field to the trusted checkpoint."""

        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return hashlib.sha256(payload).hexdigest()


class SnapshotProjectionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(f"{path.suffix}.lock")

    def read(self) -> tuple[PublicCommonsProjection, int]:
        with self.path.open("rb") as projection_file:
            payload = projection_file.read(MAX_STORED_PROJECTION_BYTES + 1)
        if len(payload) > MAX_STORED_PROJECTION_BYTES:
            raise ValueError("stored public commons projection exceeds its read budget")
        return PublicCommonsProjection.model_validate_json(payload), len(payload)

    def write(self, projection: PublicCommonsProjection) -> int:
        payload = projection.model_dump_json().encode()
        if len(payload) > MAX_STORED_PROJECTION_BYTES:
            raise ValueError("stored public commons projection exceeds its write budget")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{projection.snapshot.snapshot_id}.tmp"
        )
        try:
            with temporary.open("xb") as projection_file:
                projection_file.write(payload)
                projection_file.flush()
                os.fsync(projection_file.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        return len(payload)
