from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from opennosh_api.problems.schemas import (
    FieldError,
    ProblemCode,
    ProblemDetails,
    RecoveryAction,
)

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_ROOT = "https://opennosh.org/problems/"

_STATUS_CODES: dict[int, ProblemCode] = {
    400: ProblemCode.INVALID_REQUEST,
    401: ProblemCode.AUTHENTICATION_REQUIRED,
    403: ProblemCode.AUTHORIZATION_DENIED,
    404: ProblemCode.RESOURCE_NOT_FOUND,
    409: ProblemCode.CONFLICT,
    422: ProblemCode.VALIDATION_FAILED,
    429: ProblemCode.RATE_LIMITED,
    500: ProblemCode.INTERNAL_ERROR,
    502: ProblemCode.UPSTREAM_UNAVAILABLE,
    503: ProblemCode.SERVICE_UNAVAILABLE,
    504: ProblemCode.UPSTREAM_UNAVAILABLE,
}

_TITLES: dict[ProblemCode, str] = {
    ProblemCode.INVALID_REQUEST: "Invalid request",
    ProblemCode.AUTHENTICATION_REQUIRED: "Authentication required",
    ProblemCode.AUTHORIZATION_DENIED: "Permission denied",
    ProblemCode.RESOURCE_NOT_FOUND: "Not found",
    ProblemCode.CONFLICT: "Request conflict",
    ProblemCode.VALIDATION_FAILED: "Validation failed",
    ProblemCode.RATE_LIMITED: "Too many requests",
    ProblemCode.UPSTREAM_UNAVAILABLE: "Upstream service unavailable",
    ProblemCode.SERVICE_UNAVAILABLE: "Service unavailable",
    ProblemCode.DATABASE_CAPACITY_EXHAUSTED: "Database capacity is busy",
    ProblemCode.INTERNAL_ERROR: "Unexpected server error",
    ProblemCode.SEARCH_CURSOR_INVALID: "Invalid search cursor",
    ProblemCode.SEARCH_CURSOR_RESTART: "Restart search",
}

_DEFAULT_DETAILS: dict[ProblemCode, str] = {
    ProblemCode.INVALID_REQUEST: "That request could not be understood.",
    ProblemCode.AUTHENTICATION_REQUIRED: "Sign in to continue.",
    ProblemCode.AUTHORIZATION_DENIED: "You do not have permission for this action.",
    ProblemCode.RESOURCE_NOT_FOUND: "The requested resource was not found.",
    ProblemCode.CONFLICT: "The request conflicts with the latest saved state.",
    ProblemCode.VALIDATION_FAILED: "Check the highlighted fields and try again.",
    ProblemCode.RATE_LIMITED: "Too many requests were made. Try again later.",
    ProblemCode.UPSTREAM_UNAVAILABLE: (
        "A required upstream service is unavailable. Try again later."
    ),
    ProblemCode.SERVICE_UNAVAILABLE: ("The service is temporarily unavailable. Try again later."),
    ProblemCode.DATABASE_CAPACITY_EXHAUSTED: (
        "Database capacity is temporarily full. Wait briefly and try again."
    ),
    ProblemCode.INTERNAL_ERROR: "The server could not complete the request.",
    ProblemCode.SEARCH_CURSOR_INVALID: "That search cursor could not be verified.",
    ProblemCode.SEARCH_CURSOR_RESTART: (
        "This search changed or expired. Restart from the first page."
    ),
}

_VALIDATION_MESSAGES: dict[str, str] = {
    "missing": "This field is required.",
    "string_too_short": "This value is too short.",
    "string_too_long": "This value is too long.",
    "string_pattern_mismatch": "This value has an invalid format.",
    "greater_than_equal": "This value is below the supported minimum.",
    "less_than_equal": "This value exceeds the supported maximum.",
    "int_parsing": "Enter a whole number.",
    "decimal_parsing": "Enter a valid number.",
    "date_from_datetime_parsing": "Enter a valid date.",
    "datetime_from_date_parsing": "Enter a valid date and time.",
    "enum": "Choose one of the supported values.",
    "extra_forbidden": "This field is not supported.",
    "value_error": "This value is invalid.",
}


@dataclass
class ProblemException(Exception):
    status: int
    code: ProblemCode
    detail: str
    recovery_actions: tuple[RecoveryAction, ...] = ()
    retry_after: int | None = None


def request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    if isinstance(value, str):
        return value
    generated = str(uuid4())
    request.state.request_id = generated
    return generated


def _problem_type(code: ProblemCode) -> str:
    return f"{PROBLEM_TYPE_ROOT}{code.value.replace('_', '-')}"


def _retry_after(headers: Mapping[str, str] | None) -> int | None:
    if not headers:
        return None
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None or not value.isascii() or not value.isdigit():
        return None
    parsed = int(value)
    return parsed if 1 <= parsed <= 86_400 else None


def _recovery_actions(code: ProblemCode) -> list[RecoveryAction] | None:
    if code is ProblemCode.AUTHENTICATION_REQUIRED:
        return [RecoveryAction(id="sign_in", label="Sign in", href="/tracker")]
    if code in {
        ProblemCode.RATE_LIMITED,
        ProblemCode.UPSTREAM_UNAVAILABLE,
        ProblemCode.SERVICE_UNAVAILABLE,
    }:
        return [RecoveryAction(id="retry", label="Try again")]
    if code is ProblemCode.VALIDATION_FAILED:
        return [RecoveryAction(id="review_fields", label="Review highlighted fields")]
    if code is ProblemCode.CONFLICT:
        return [RecoveryAction(id="reload", label="Load the latest saved state")]
    return None


def build_problem(
    request: Request,
    *,
    status: int,
    code: ProblemCode,
    detail: str | None = None,
    retry_after: int | None = None,
    field_errors: list[FieldError] | None = None,
    recovery_actions: list[RecoveryAction] | None = None,
) -> ProblemDetails:
    candidate_detail = (
        detail if isinstance(detail, str) and detail.strip() else _DEFAULT_DETAILS[code]
    )
    safe_detail = candidate_detail[:500]
    return ProblemDetails(
        type=_problem_type(code),
        title=_TITLES[code],
        status=status,
        detail=safe_detail,
        code=code,
        request_id=request_id(request),
        retry_after=retry_after,
        field_errors=field_errors,
        recovery_actions=(
            recovery_actions if recovery_actions is not None else _recovery_actions(code)
        ),
    )


def problem_response(
    problem: ProblemDetails,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = problem.request_id
    response_headers["Cache-Control"] = "no-store"
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=problem.status,
        headers=response_headers,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def _pointer(location: tuple[Any, ...]) -> str:
    parts = [str(part).replace("~", "~0").replace("/", "~1") for part in location]
    return "/" + "/".join(parts or ["request"])


async def problem_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    problem_exception = cast(ProblemException, exception)
    problem = build_problem(
        request,
        status=problem_exception.status,
        code=problem_exception.code,
        detail=problem_exception.detail,
        retry_after=problem_exception.retry_after,
        recovery_actions=list(problem_exception.recovery_actions),
    )
    headers = (
        {"Retry-After": str(problem_exception.retry_after)}
        if problem_exception.retry_after is not None
        else None
    )
    return problem_response(problem, headers=headers)


async def http_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    http_exception = cast(StarletteHTTPException, exception)
    code = _STATUS_CODES.get(http_exception.status_code, ProblemCode.INVALID_REQUEST)
    detail = http_exception.detail if isinstance(http_exception.detail, str) else None
    headers = dict(http_exception.headers or {})
    retry_after = _retry_after(headers)
    if retry_after is not None:
        code = ProblemCode.RATE_LIMITED
    problem = build_problem(
        request,
        status=http_exception.status_code,
        code=code,
        detail=detail,
        retry_after=retry_after,
    )
    return problem_response(problem, headers=headers)


async def validation_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    validation_error = cast(RequestValidationError, exception)
    field_errors = [
        FieldError(
            pointer=_pointer(tuple(error.get("loc", ("request",)))),
            code=str(error.get("type", "invalid")).replace(".", "_")[:80],
            message=_VALIDATION_MESSAGES.get(
                str(error.get("type")),
                "This value is invalid.",
            ),
        )
        for error in validation_error.errors()[:100]
    ]
    problem = build_problem(
        request,
        status=422,
        code=ProblemCode.VALIDATION_FAILED,
        field_errors=field_errors,
    )
    return problem_response(problem)


async def unexpected_exception_handler(
    request: Request,
    _exception: Exception,
) -> JSONResponse:
    problem = build_problem(
        request,
        status=500,
        code=ProblemCode.INTERNAL_ERROR,
    )
    return problem_response(problem)


def install_problem_handlers(application: FastAPI) -> None:
    application.add_exception_handler(ProblemException, problem_exception_handler)
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(Exception, unexpected_exception_handler)
