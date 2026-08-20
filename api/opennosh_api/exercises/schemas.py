from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

WGER_LICENSE_SPDX = "CC-BY-SA-3.0"
WGER_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/3.0/"


def _plain_text(value: str, *, maximum: int, field: str) -> str:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field} must contain between 1 and {maximum} characters")
    if any(character in "<>\x00" or ord(character) < 32 for character in normalized):
        raise ValueError(f"{field} contains unsafe characters")
    return normalized


def _safe_url(value: str, *, field: str) -> str:
    if len(value) > 2048 or any(
        character.isspace() or character in '<>"\'\\' for character in value
    ):
        raise ValueError(f"{field} is not a safe HTTP URL")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{field} is not a safe HTTP URL") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field} is not a safe HTTP URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field} cannot contain credentials")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{field} is not a safe HTTP URL")
    return value


class _AttributedWgerModel(BaseModel):
    @field_validator(
        "source_id", "source_uuid", "language_id", "name", "description", "license_title",
        "author", "attribution_text", check_fields=False
    )
    @classmethod
    def validate_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "text")
        maximum = 10_000 if field_name in {"description", "attribution_text"} else 255
        return _plain_text(value, maximum=maximum, field=field_name)

    @field_validator("aliases", "notes", check_fields=False)
    @classmethod
    def validate_text_lists(cls, values: list[str], info: object) -> list[str]:
        field_name = getattr(info, "field_name", "items")
        if len(values) > 100:
            raise ValueError(f"{field_name} cannot contain more than 100 values")
        return [_plain_text(value, maximum=500, field=field_name) for value in values]

    @field_validator("source_url", "derivative_source_url", "author_url", check_fields=False)
    @classmethod
    def validate_urls(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _safe_url(value, field=getattr(info, "field_name", "url"))

    @field_validator("license_spdx", check_fields=False)
    @classmethod
    def validate_license_spdx(cls, value: str) -> str:
        if value != WGER_LICENSE_SPDX:
            raise ValueError("license_spdx must be CC-BY-SA-3.0")
        return value

    @field_validator("license_url", check_fields=False)
    @classmethod
    def validate_license_url(cls, value: str) -> str:
        if value != WGER_LICENSE_URL:
            raise ValueError("license_url must be the canonical CC BY-SA 3.0 URL")
        return value


class ExerciseTranslation(_AttributedWgerModel):
    source_id: str
    source_uuid: str | None = None
    language_id: str
    name: str
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source_url: str | None = None
    derivative_source_url: str | None = None
    license_spdx: str
    license_url: str
    license_title: str | None = None
    author: str
    author_url: str | None = None
    attribution_text: str


class ExerciseTranslationAttribution(_AttributedWgerModel):
    source_id: str
    language_id: str
    source_url: str | None = None
    derivative_source_url: str | None = None
    license_spdx: str
    license_url: str
    license_title: str | None = None
    author: str
    author_url: str | None = None
    attribution_text: str


class ExerciseAttribution(_AttributedWgerModel):
    source: str
    source_id: str
    source_url: str
    derivative_source_url: str | None = None
    license_spdx: str
    license_url: str
    author: str | None = None
    author_url: str | None = None
    attribution_text: str
    translations: list[ExerciseTranslationAttribution] = Field(default_factory=list)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if value != "wger":
            raise ValueError("source must be wger")
        return value


class ExerciseDetail(_AttributedWgerModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    muscle_groups: list[str]
    equipment: list[str]
    translations: list[ExerciseTranslation]
    attribution: ExerciseAttribution
    source_updated_at: datetime | None = None

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        return _plain_text(value, maximum=160, field="slug")

    @field_validator("muscle_groups", "equipment")
    @classmethod
    def validate_taxonomy(cls, values: list[str], info: object) -> list[str]:
        field_name = getattr(info, "field_name", "taxonomy")
        if len(values) > 100:
            raise ValueError(f"{field_name} cannot contain more than 100 values")
        return [_plain_text(value, maximum=100, field=field_name) for value in values]


class ExerciseSearchResponse(BaseModel):
    items: list[ExerciseDetail]
    limit: int
    offset: int
    has_more: bool


class ExerciseExport(BaseModel):
    schema_version: str = "1.0.0"
    dataset: str = "opennosh-wger-exercises"
    source: str = "wger"
    source_url: str = "https://wger.de/"
    license_spdx: str = "CC-BY-SA-3.0"
    license_url: str = "https://creativecommons.org/licenses/by-sa/3.0/"
    share_alike_notice: str = (
        "This exercise dataset is licensed CC BY-SA 3.0. Attribution and ShareAlike "
        "requirements apply to redistribution and adaptations."
    )
    entries: list[ExerciseDetail]
