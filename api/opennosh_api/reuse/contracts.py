from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_COUNTRY = re.compile(r"^[A-Z]{2}$")
_MACROREGION = re.compile(r"^[0-9]{3}$")
_PLAIN_TEXT = re.compile(r"^[^<>\x00-\x1f\x7f]+$")


class ReuseDeclarationState(StrEnum):
    COMMUNITY_DECLARED = "community_declared"
    VERIFICATION_PENDING = "verification_pending"
    VERIFIED = "verified"
    WITHDRAWN = "withdrawn"


class ReuseEventType(StrEnum):
    DECLARED = "declared"
    EDITED = "edited"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    RESTORED = "restored"


class ReuseRegionLevel(StrEnum):
    COUNTRY = "country"
    MACROREGION = "macroregion"


def normalize_label(value: str, *, maximum: int) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized or len(normalized) > maximum or not _PLAIN_TEXT.fullmatch(normalized):
        raise ValueError("Reuse registry text must be printable plain text")
    return normalized


def normalized_key(value: str) -> str:
    return normalize_label(value, maximum=160).casefold()


def normalize_public_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    parsed = urlsplit(normalized)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Project URL must be a public HTTPS URL") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (port is not None and not 1 <= port <= 65_535)
        or any(character.isspace() or character in "<>\"'\\" for character in normalized)
    ):
        raise ValueError("Project URL must be a public HTTPS URL")
    return normalized


class ReuseDeclarationFields(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_name: Annotated[str, Field(min_length=1, max_length=160)]
    project_name: Annotated[str, Field(min_length=1, max_length=160)]
    project_url: Annotated[str | None, Field(max_length=2048)] = None
    use_case: Annotated[str, Field(min_length=1, max_length=1000)]
    region_level: ReuseRegionLevel | None = None
    region_code: Annotated[str | None, Field(max_length=3)] = None

    @field_validator("organization_name", "project_name")
    @classmethod
    def validate_label(cls, value: str) -> str:
        return normalize_label(value, maximum=160)

    @field_validator("use_case")
    @classmethod
    def validate_use_case(cls, value: str) -> str:
        return normalize_label(value, maximum=1000)

    @field_validator("project_url")
    @classmethod
    def validate_project_url(cls, value: str | None) -> str | None:
        return normalize_public_url(value)

    @model_validator(mode="after")
    def validate_region(self) -> ReuseDeclarationFields:
        if (self.region_level is None) != (self.region_code is None):
            raise ValueError("Region level and code must be supplied together")
        if self.region_level is ReuseRegionLevel.COUNTRY and not _COUNTRY.fullmatch(
            self.region_code or ""
        ):
            raise ValueError("Country reuse regions require an uppercase ISO 3166-1 alpha-2 code")
        if self.region_level is ReuseRegionLevel.MACROREGION and not _MACROREGION.fullmatch(
            self.region_code or ""
        ):
            raise ValueError("Macroregion reuse regions require a three-digit UN M49 code")
        return self


class ReuseDeclarationCreate(ReuseDeclarationFields):
    pass


class ReuseDeclarationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_name: Annotated[str | None, Field(min_length=1, max_length=160)] = None
    project_name: Annotated[str | None, Field(min_length=1, max_length=160)] = None
    project_url: Annotated[str | None, Field(max_length=2048)] = None
    clear_project_url: bool = False
    use_case: Annotated[str | None, Field(min_length=1, max_length=1000)] = None
    region_level: ReuseRegionLevel | None = None
    region_code: Annotated[str | None, Field(max_length=3)] = None
    clear_region: bool = False

    @field_validator("organization_name", "project_name")
    @classmethod
    def validate_optional_label(cls, value: str | None) -> str | None:
        return None if value is None else normalize_label(value, maximum=160)

    @field_validator("use_case")
    @classmethod
    def validate_optional_use_case(cls, value: str | None) -> str | None:
        return None if value is None else normalize_label(value, maximum=1000)

    @field_validator("project_url")
    @classmethod
    def validate_optional_project_url(cls, value: str | None) -> str | None:
        return normalize_public_url(value)

    @model_validator(mode="after")
    def validate_patch(self) -> ReuseDeclarationPatch:
        if self.clear_project_url and self.project_url is not None:
            raise ValueError("Project URL cannot be set and cleared together")
        supplied_region = self.region_level is not None or self.region_code is not None
        if self.clear_region and supplied_region:
            raise ValueError("Region cannot be set and cleared together")
        if supplied_region:
            ReuseDeclarationFields(
                organization_name="placeholder",
                project_name="placeholder",
                use_case="placeholder",
                region_level=self.region_level,
                region_code=self.region_code,
            )
        if not any(
            value is not None
            for value in (
                self.organization_name,
                self.project_name,
                self.project_url,
                self.use_case,
                self.region_level,
                self.region_code,
            )
        ) and not (self.clear_project_url or self.clear_region):
            raise ValueError("Reuse declaration patch must change at least one field")
        return self


class ReuseDeclarationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    schema_version: Literal["1.0"] = "1.0"
    id: UUID
    organization_name: str
    project_name: str
    project_url: str | None
    use_case: str
    region_level: ReuseRegionLevel | None
    region_code: str | None
    state: ReuseDeclarationState
    revision: int
    created_at: datetime
    updated_at: datetime
    withdrawn_at: datetime | None


class ReuseDeclarationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    declarations: tuple[ReuseDeclarationResponse, ...]


class ReuseTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Annotated[str | None, Field(min_length=1, max_length=1000)] = None

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        return None if value is None else normalize_label(value, maximum=1000)


__all__ = [
    "ReuseDeclarationCreate",
    "ReuseDeclarationFields",
    "ReuseDeclarationListResponse",
    "ReuseDeclarationPatch",
    "ReuseDeclarationResponse",
    "ReuseDeclarationState",
    "ReuseEventType",
    "ReuseRegionLevel",
    "ReuseTransitionRequest",
    "normalize_label",
    "normalize_public_url",
    "normalized_key",
]
