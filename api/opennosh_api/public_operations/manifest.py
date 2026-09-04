from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PUBLIC_COMPONENT_IDS = (
    "api",
    "contributions",
    "downloads",
    "evidence-processing",
    "publication",
    "reuse-registry",
    "search",
    "tracker",
)


class PublicStatusComponentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]
    display_name: Annotated[str, Field(min_length=1, max_length=80)]
    freshness_window_seconds: Annotated[int, Field(ge=30, le=3600)]


class PublicStatusManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_uri: Literal["../schemas/public-status.schema.json"] = Field(alias="$schema")
    schema_version: Literal["1.0"] = "1.0"
    components: tuple[PublicStatusComponentDefinition, ...]

    @model_validator(mode="after")
    def validate_fixed_inventory(self) -> PublicStatusManifest:
        observed = tuple(component.component_id for component in self.components)
        if observed != PUBLIC_COMPONENT_IDS:
            raise ValueError("Public status components must match the fixed ordered inventory")
        return self

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        return hashlib.sha256(payload).hexdigest()


def default_public_status_manifest_path() -> Path:
    repository_path = Path(__file__).resolve().parents[3] / "config/public-status.v1.json"
    if repository_path.is_file():
        return repository_path
    return Path(__file__).resolve().parents[1] / "public-status.v1.json"


def load_public_status_manifest(path: Path | None = None) -> PublicStatusManifest:
    target = path or default_public_status_manifest_path()
    return PublicStatusManifest.model_validate_json(target.read_bytes())


__all__ = [
    "PUBLIC_COMPONENT_IDS",
    "PublicStatusComponentDefinition",
    "PublicStatusManifest",
    "default_public_status_manifest_path",
    "load_public_status_manifest",
]
