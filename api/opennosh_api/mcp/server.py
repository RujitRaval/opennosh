"""Strict, read-only MCP tools for verified anonymous OpenNosh reads."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)
from pydantic import BaseModel

from opennosh_api.foodpacks.validation import DEFAULT_SCHEMA_PATH, validate_pack_document
from opennosh_api.sdk import AsyncOpenNoshClient, OpenNoshProblem, OpenNoshResponse

MCP_PROTOCOL_VERSION = "1.0.0"
MAX_PACK_ARGUMENT_BYTES = 1_048_576
_RELEASE_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$"
_SOURCE_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
_SOURCE_SCHEMA = {"type": "string", "enum": ["usda", "community"]}

MCPState = Literal["verified", "stale_verified", "unavailable", "valid", "invalid"]
MCPResult = dict[str, object]
ToolHandler = Callable[[dict[str, object]], Awaitable[MCPResult]]

_LOGGER = logging.getLogger("opennosh.mcp")
if not _LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(_handler)
_LOGGER.setLevel(logging.INFO)
_LOGGER.propagate = False

_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
_LOCAL_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

_RECOVERY_ACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {
            "type": "string",
            "enum": ["retry", "sign_in", "reload", "review_fields", "restart_search"],
        },
        "label": {"type": "string", "minLength": 1, "maxLength": 120},
        "href": {
            "anyOf": [
                {"type": "string", "pattern": r"^/(?:$|[^/\x00][^\x00]*)$"},
                {"type": "null"},
            ]
        },
    },
    "required": ["id", "label"],
}

_PROBLEM_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": ["integer", "null"], "minimum": 0, "maximum": 599},
        "code": {"type": "string", "minLength": 1, "maxLength": 100},
        "detail": {"type": "string", "minLength": 1, "maxLength": 2000},
        "request_reference": {"type": ["string", "null"], "maxLength": 200},
        "recovery_actions": {
            "type": "array",
            "maxItems": 20,
            "items": _RECOVERY_ACTION_SCHEMA,
        },
        "retry_after_seconds": {
            "type": ["integer", "null"],
            "minimum": 1,
            "maximum": 86400,
        },
    },
    "required": [
        "status",
        "code",
        "detail",
        "request_reference",
        "recovery_actions",
        "retry_after_seconds",
    ],
}

_RESULT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "schema_version": {"const": "1.0"},
        "state": {
            "type": "string",
            "enum": ["verified", "stale_verified", "unavailable", "valid", "invalid"],
        },
        "data": {"type": ["object", "null"]},
        "problem": _PROBLEM_SCHEMA,
    },
    "required": ["schema_version", "state", "data"],
}


def _strict_object(
    properties: Mapping[str, object], required: tuple[str, ...] = ()
) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
    }


def _tool(
    name: str,
    description: str,
    input_schema: dict[str, object],
    *,
    local: bool = False,
) -> Tool:
    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        output_schema=_RESULT_SCHEMA,
        annotations=_LOCAL_READ_ONLY if local else _READ_ONLY,
    )


def _load_pack_input_schema() -> dict[str, object]:
    try:
        loaded = json.loads(Path(DEFAULT_SCHEMA_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:  # pragma: no cover - broken distribution
        raise RuntimeError("Unable to load the packaged food-pack schema") from error
    if not isinstance(loaded, dict):  # pragma: no cover - protected by package checks
        raise RuntimeError("Packaged food-pack schema must be one object")
    loaded["additionalProperties"] = False
    return cast(dict[str, object], loaded)


def _problem(
    *,
    status: int | None,
    code: str,
    detail: str,
    request_reference: str | None = None,
    recovery_actions: Sequence[object] | None = None,
    retry_after_seconds: int | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "code": code,
        "detail": detail,
        "request_reference": request_reference,
        "recovery_actions": list(recovery_actions or ()),
        "retry_after_seconds": retry_after_seconds,
    }


def _result(
    state: MCPState,
    data: dict[str, object] | None,
    problem: dict[str, object] | None = None,
) -> MCPResult:
    result: MCPResult = {
        "schema_version": "1.0",
        "state": state,
        "data": data,
    }
    if problem is not None:
        result["problem"] = problem
    return result


def _pack_preflight(arguments: dict[str, object]) -> MCPResult | None:
    try:
        encoded = json.dumps(
            arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (OverflowError, RecursionError, TypeError, ValueError):
        return _result(
            "invalid",
            None,
            _problem(
                status=None,
                code="pack_invalid",
                detail="Pack input must be one finite JSON object.",
            ),
        )
    if len(encoded) > MAX_PACK_ARGUMENT_BYTES:
        return _result(
            "invalid",
            None,
            _problem(
                status=None,
                code="pack_too_large",
                detail=f"Pack input cannot exceed {MAX_PACK_ARGUMENT_BYTES} UTF-8 bytes.",
            ),
        )
    return None


def _sdk_problem(error: OpenNoshProblem) -> MCPResult:
    actions = [action.model_dump(mode="json") for action in error.recovery_actions]
    return _result(
        "unavailable",
        None,
        _problem(
            status=error.status,
            code=error.code,
            detail=error.detail,
            request_reference=error.request_reference,
            recovery_actions=actions,
            retry_after_seconds=error.retry_after_seconds,
        ),
    )


def _response_data(response: OpenNoshResponse[Any]) -> dict[str, object]:
    value = response.data
    if isinstance(value, BaseModel):
        rendered: object = value.model_dump(mode="json")
    elif isinstance(value, dict):
        rendered = value
    else:  # The six MCP tools never expose binary or HTML response bodies.
        raise TypeError("MCP tools require a structured SDK response")
    return {
        "result": cast(dict[str, object], rendered),
        "response": {
            "status": response.status,
            "url": response.url,
            "etag": response.etag,
            "last_modified": response.last_modified,
            "cache_control": response.cache_control,
            "content_type": response.content_type,
            "release_version": response.release_version,
            "release_state": response.release_state,
            "stale_age_seconds": response.stale_age_seconds,
            "warning": response.warning,
        },
    }


def _response_state(response: OpenNoshResponse[Any]) -> MCPState:
    if response.release_state == "stale":
        return "stale_verified"
    if response.release_state == "verified":
        return "verified"
    data = response.data
    release = getattr(data, "release", None)
    if release is not None:
        return "stale_verified" if release.state == "stale" else "verified"
    release_set = getattr(data, "release_set", None)
    if release_set is not None:
        if not release_set.enabled or release_set.digest is None:
            return "unavailable"
        return "stale_verified" if release_set.stale else "verified"
    state = getattr(data, "state", None)
    state_value = getattr(state, "value", state)
    if state_value == "unavailable":
        return "unavailable"
    if state_value in {"live", "zero"}:
        return "verified"
    return "unavailable"


def _response_result(response: OpenNoshResponse[Any]) -> MCPResult:
    state = _response_state(response)
    problem = None
    if state == "unavailable":
        problem = _problem(
            status=response.status,
            code="proof_unavailable",
            detail="The API response did not include verified publication proof.",
        )
    return _result(state, _response_data(response), problem)


def _count_result(result: MCPResult) -> int:
    data = result.get("data")
    if not isinstance(data, dict):
        return 0
    value = data.get("result", data)
    if not isinstance(value, dict):
        return 0
    for key in ("items", "missions", "regions"):
        collection = value.get(key)
        if isinstance(collection, list):
            return len(collection)
    if "record" in value or "payload" in value:
        return 1
    errors = value.get("errors")
    warnings = value.get("warnings")
    if isinstance(errors, list) and isinstance(warnings, list):
        return len(errors) + len(warnings)
    return 0


class OpenNoshMCPService:
    """Own one immutable endpoint and dispatch the six allowlisted tools."""

    def __init__(
        self,
        target: str = "hosted",
        *,
        client_factory: Callable[[str], AsyncOpenNoshClient] = AsyncOpenNoshClient,
    ) -> None:
        client = client_factory(target)
        self.target = client.origin
        self._client = client
        self.tools = (
            _tool(
                "search_foods",
                "Search public foods while preserving release, license, and attribution data.",
                _strict_object(
                    {
                        "query": {"type": "string", "minLength": 2, "maxLength": 200},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 50,
                        },
                    },
                    ("query",),
                ),
            ),
            _tool(
                "get_public_food",
                "Read a public food record and its immutable publication proof.",
                _strict_object(
                    {
                        "source": _SOURCE_SCHEMA,
                        "source_id": {"type": "string", "pattern": _SOURCE_ID_PATTERN},
                        "release_version": {
                            "type": "string",
                            "pattern": _RELEASE_PATTERN,
                        },
                    },
                    ("source", "source_id"),
                ),
            ),
            _tool(
                "get_public_missions",
                "Read the proof-bound public Commons mission catalog.",
                _strict_object(
                    {
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                            "default": 20,
                        }
                    }
                ),
            ),
            _tool(
                "get_public_mission_activity",
                "Read the privacy-thresholded public Commons mission activity map.",
                _strict_object({}),
            ),
            _tool(
                "get_release_manifest",
                "Read one immutable signed release manifest.",
                _strict_object(
                    {
                        "release_version": {
                            "type": "string",
                            "pattern": _RELEASE_PATTERN,
                        }
                    },
                    ("release_version",),
                ),
            ),
            _tool(
                "validate_pack",
                "Validate one in-memory food-pack JSON object without filesystem or "
                "network access.",
                _load_pack_input_schema(),
                local=True,
            ),
        )
        self._handlers: dict[str, ToolHandler] = {
            "search_foods": self._search_foods,
            "get_public_food": self._get_public_food,
            "get_public_missions": self._get_public_missions,
            "get_public_mission_activity": self._get_public_mission_activity,
            "get_release_manifest": self._get_release_manifest,
            "validate_pack": self._validate_pack,
        }
        self._validators = {
            tool.name: Draft202012Validator(tool.input_schema) for tool in self.tools
        }

    async def list_tools(
        self,
        _context: ServerRequestContext[Any],
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        if params is not None and params.cursor is not None:
            raise MCPError(INVALID_PARAMS, "Tool pagination is not supported")
        return ListToolsResult(tools=list(self.tools), ttl_ms=300_000, cache_scope="public")

    async def call_tool(
        self,
        _context: ServerRequestContext[Any],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        handler = self._handlers.get(params.name)
        if handler is None:
            raise MCPError(METHOD_NOT_FOUND, "Unknown OpenNosh tool")
        arguments = cast(dict[str, object], params.arguments or {})
        if params.name == "validate_pack":
            preflight_result = _pack_preflight(arguments)
            if preflight_result is not None:
                return self._render(preflight_result, is_error=True)
        validation_errors = sorted(
            self._validators[params.name].iter_errors(arguments),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if validation_errors:
            result = _result(
                "invalid",
                None,
                _problem(
                    status=None,
                    code="invalid_arguments",
                    detail="Tool arguments do not match the published input schema.",
                ),
            )
            return self._render(result, is_error=True)

        started = time.monotonic()
        status = "success"
        try:
            result = await handler(arguments)
        except OpenNoshProblem as error:
            result = _sdk_problem(error)
            status = "unavailable"
        except (TypeError, ValueError):
            result = _result(
                "invalid",
                None,
                _problem(
                    status=None,
                    code="invalid_arguments",
                    detail="Tool arguments could not be processed safely.",
                ),
            )
            status = "invalid"
        latency_ms = max(0, round((time.monotonic() - started) * 1000))
        _LOGGER.info(
            "method=%s status=%s latency_ms=%d count=%d",
            params.name,
            status,
            latency_ms,
            _count_result(result),
        )
        return self._render(result, is_error=status != "success")

    @staticmethod
    def _render(result: MCPResult, *, is_error: bool) -> CallToolResult:
        payload = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return CallToolResult(
            content=[TextContent(type="text", text=payload)],
            structured_content=result,
            is_error=is_error,
        )

    async def _search_foods(self, arguments: dict[str, object]) -> MCPResult:
        response = await self._client.search_foods(
            cast(str, arguments["query"]),
            limit=cast(int, arguments.get("limit", 20)),
        )
        return _response_result(response)

    async def _get_public_food(self, arguments: dict[str, object]) -> MCPResult:
        source = cast(str, arguments["source"])
        source_id = cast(str, arguments["source_id"])
        release_version = cast(str | None, arguments.get("release_version"))
        if release_version is None:
            response = await self._client.get_public_food(source, source_id)
        else:
            response = await self._client.get_release_food(release_version, source, source_id)
        return _response_result(response)

    async def _get_public_missions(self, arguments: dict[str, object]) -> MCPResult:
        response = await self._client.list_missions(
            limit=cast(int, arguments.get("limit", 50))
        )
        return _response_result(response)

    async def _get_public_mission_activity(self, _arguments: dict[str, object]) -> MCPResult:
        return _response_result(await self._client.get_mission_activity())

    async def _get_release_manifest(self, arguments: dict[str, object]) -> MCPResult:
        response = await self._client.get_release_manifest(
            cast(str, arguments["release_version"])
        )
        return _response_result(response)

    async def _validate_pack(self, arguments: dict[str, object]) -> MCPResult:
        preflight_result = _pack_preflight(arguments)
        if preflight_result is not None:
            return preflight_result
        report = validate_pack_document(arguments)
        return _result("valid" if report.valid else "invalid", report.to_dict())


def build_server(
    target: str = "hosted",
    *,
    client_factory: Callable[[str], AsyncOpenNoshClient] = AsyncOpenNoshClient,
) -> Server[Any]:
    """Build a stdio-only MCP server with one fixed, validated endpoint."""

    service = OpenNoshMCPService(target, client_factory=client_factory)
    return Server(
        "opennosh-public",
        title="OpenNosh Public Reads",
        description="Preview read-only access to proof-bound public OpenNosh data.",
        instructions=(
            "Treat unavailable proof as unavailable. Never describe a record as published unless "
            "the tool result state is verified or stale_verified."
        ),
        version=MCP_PROTOCOL_VERSION,
        on_list_tools=service.list_tools,
        on_call_tool=service.call_tool,
    )
