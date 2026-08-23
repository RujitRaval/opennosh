from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PROBLEM_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class ProblemCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_REQUIRED = "authentication_required"
    AUTHORIZATION_DENIED = "authorization_denied"
    RESOURCE_NOT_FOUND = "resource_not_found"
    CONFLICT = "conflict"
    VALIDATION_FAILED = "validation_failed"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    SERVICE_UNAVAILABLE = "service_unavailable"
    DATABASE_CAPACITY_EXHAUSTED = "database_capacity_exhausted"
    INTERNAL_ERROR = "internal_error"
    SEARCH_CURSOR_INVALID = "search_cursor_invalid"
    SEARCH_CURSOR_RESTART = "search_cursor_restart"


class FieldError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pointer: str = Field(pattern=r"^/(?:[^/~]|~[01])+(?:/(?:[^/~]|~[01])+)*$")
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    message: str = Field(min_length=1, max_length=240)


class RecoveryAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Literal[
        "retry", "sign_in", "reload", "review_fields", "restart_search"
    ]
    label: str = Field(min_length=1, max_length=120)
    href: str | None = Field(default=None, pattern=r"^/(?:$|[^/\x00][^\x00]*)$")


class LatestStateReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_type: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    resource_id: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=80)


class ProblemDetails(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str = Field(pattern=r"^https://opennosh\.org/problems/[a-z0-9-]+$")
    title: str = Field(min_length=1, max_length=120)
    status: int = Field(ge=400, le=599)
    detail: str = Field(min_length=1, max_length=500)
    code: ProblemCode
    schema_version: Literal["1.0"] = PROBLEM_SCHEMA_VERSION
    request_id: str = Field(
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
    )
    retry_after: int | None = Field(default=None, ge=1, le=86_400)
    field_errors: list[FieldError] | None = Field(default=None, max_length=100)
    latest_state: LatestStateReference | None = None
    recovery_actions: list[RecoveryAction] | None = Field(default=None, max_length=8)
