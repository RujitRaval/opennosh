from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from opennosh_api.problems.schemas import ProblemDetails

API_CONTRACT_VERSION = "1.0.0"
PROBLEM_STATUSES = ("400", "401", "403", "404", "409", "422", "429", "500", "502", "503", "504")
_PROBLEM_SCHEMA_REF = "#/components/schemas/ProblemDetails"
_LEGACY_VALIDATION_SCHEMA_REF = "#/components/schemas/HTTPValidationError"


def common_problem_responses() -> dict[int | str, dict[str, Any]]:
    descriptions = {
        400: "The request is invalid.",
        401: "Authentication is required.",
        403: "The current user is not authorized.",
        404: "The requested resource was not found.",
        409: "The request conflicts with the latest state.",
        422: "The request failed validation.",
        429: "The request rate limit was exceeded.",
        500: "The server could not complete the request.",
        502: "An upstream service returned an unusable response.",
        503: "The service is temporarily unavailable.",
        504: "An upstream service timed out.",
    }
    return {
        status: {
            "model": ProblemDetails,
            "description": description,
        }
        for status, description in descriptions.items()
    }


def _response_schema_reference(response: dict[str, Any]) -> str | None:
    content = response.get("content")
    if not isinstance(content, dict):
        return None
    json_content = content.get("application/json")
    if not isinstance(json_content, dict):
        return None
    schema = json_content.get("schema")
    if not isinstance(schema, dict):
        return None
    reference = schema.get("$ref")
    return reference if isinstance(reference, str) else None


def _use_problem_media_type(response: dict[str, Any]) -> None:
    content = response.get("content")
    if not isinstance(content, dict):
        return
    json_schema = content.pop("application/json", None)
    if json_schema is not None:
        content["application/problem+json"] = json_schema


def install_openapi_contract(application: FastAPI) -> None:
    def contract_openapi() -> dict[str, Any]:
        if application.openapi_schema is not None:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            openapi_version=application.openapi_version,
            summary=application.summary,
            description=application.description,
            routes=application.routes,
            tags=application.openapi_tags,
            servers=application.servers,
        )
        schema["info"]["x-opennosh-contract-version"] = API_CONTRACT_VERSION
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if not isinstance(operation, dict) or "responses" not in operation:
                    continue
                for status in PROBLEM_STATUSES:
                    response = operation["responses"].get(status)
                    if not isinstance(response, dict):
                        continue
                    reference = _response_schema_reference(response)
                    if reference == _LEGACY_VALIDATION_SCHEMA_REF:
                        response.clear()
                        response.update(
                            {
                                "description": "The request failed validation.",
                                "content": {
                                    "application/problem+json": {
                                        "schema": {"$ref": _PROBLEM_SCHEMA_REF}
                                    }
                                },
                            }
                        )
                    elif reference == _PROBLEM_SCHEMA_REF:
                        _use_problem_media_type(response)
        application.openapi_schema = schema
        return schema

    application.openapi = contract_openapi  # type: ignore[method-assign]
