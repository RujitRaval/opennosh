from __future__ import annotations

import io
import json
import logging
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolRequestParams, PaginatedRequestParams
from opennosh_api.foodpacks.validation import load_pack_directory
from opennosh_api.mcp import entrypoint
from opennosh_api.mcp.entrypoint import main
from opennosh_api.mcp.server import (
    MAX_PACK_ARGUMENT_BYTES,
    MCP_PROTOCOL_VERSION,
    OpenNoshMCPService,
    _count_result,
    _response_data,
    _response_state,
    build_server,
)
from opennosh_api.sdk import AsyncOpenNoshClient, OpenNoshResponse

ROOT = Path(__file__).resolve().parents[3]
COMPATIBILITY_FIXTURES = json.loads(
    (ROOT / "tests/fixtures/developer-compatibility.v1.json").read_text(encoding="utf-8")
)
RESPONSES = {item["operation_id"]: item for item in COMPATIBILITY_FIXTURES["responses"]}
VALID_PACK = ROOT / "api/tests/foodpacks/fixtures/valid/balanced-pack"


def _response_for(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/v1/foods/search":
        operation = "search_api_v1_foods_search_get"
    elif path == "/api/v1/public/foods/community/rajma-masala":
        operation = "latest_food_api_v1_public_foods__source___source_id__get"
    elif path == "/api/v1/public/missions":
        operation = "missions_api_v1_public_missions_get"
    elif path == "/api/v1/public/missions/activity":
        operation = "mission_activity_api_v1_public_missions_activity_get"
    elif path.endswith("/manifest"):
        operation = "exact_manifest_api_v1_public_releases__release_version__manifest_get"
    else:
        operation = (
            "exact_food_api_v1_public_releases__release_version__foods__source___source_id__get"
        )
    fixture = RESPONSES[operation]
    return httpx.Response(
        200,
        json=fixture["body"],
        headers={
            "content-type": fixture["media_type"],
            "etag": '"fixture"',
            "cache-control": "public, immutable",
            "x-opennosh-release-version": "0.82.1.0",
            "x-opennosh-release-state": "stale",
            "x-opennosh-stale-age": "3600",
            "warning": '110 - "Response is stale but remains cryptographically verified"',
        },
        request=request,
    )


def _service(
    handler: Any = _response_for,
) -> OpenNoshMCPService:
    transport = httpx.MockTransport(handler)
    return OpenNoshMCPService(
        "https://nosh.example",
        client_factory=cast(
            Any,
            lambda target: AsyncOpenNoshClient(target, transport=transport),
        ),
    )


async def _call(
    service: OpenNoshMCPService,
    name: str,
    arguments: dict[str, object] | None = None,
) -> dict[str, object]:
    response = await service.call_tool(
        cast(Any, None),
        CallToolRequestParams(name=name, arguments=arguments or {}),
    )
    assert len(response.content) == 1
    assert response.content[0].type == "text"
    assert response.structured_content is not None
    parsed = json.loads(response.content[0].text)
    assert parsed == response.structured_content
    output_schema = next(tool.output_schema for tool in service.tools if tool.name == name)
    Draft202012Validator(output_schema).validate(parsed)
    return cast(dict[str, object], parsed)


def test_version_and_exact_read_only_tool_catalog_are_stable() -> None:
    service = _service()

    assert MCP_PROTOCOL_VERSION == "1.0.0"
    assert [tool.name for tool in service.tools] == [
        "search_foods",
        "get_public_food",
        "get_public_missions",
        "get_public_mission_activity",
        "get_release_manifest",
        "validate_pack",
    ]
    for tool in service.tools:
        Draft202012Validator.check_schema(tool.input_schema)
        Draft202012Validator.check_schema(tool.output_schema)
        assert tool.input_schema["type"] == "object"
        assert tool.input_schema["additionalProperties"] is False
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.output_schema["additionalProperties"] is False
    assert service.tools[-1].annotations is not None
    assert service.tools[-1].annotations.open_world_hint is False

    protocol_server = build_server(
        "https://nosh.example",
        client_factory=cast(
            Any,
            lambda target: AsyncOpenNoshClient(
                target,
                transport=httpx.MockTransport(_response_for),
            ),
        ),
    )
    assert protocol_server.name == "opennosh-public"


@pytest.mark.asyncio
async def test_stdio_entrypoint_negotiates_and_serves_local_validation() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "opennosh_api.mcp.entrypoint",
            "--target",
            "https://nosh.example",
        ],
        cwd=ROOT,
    )
    async with Client(parameters) as client:
        tools = await client.list_tools()
        validation = await client.call_tool(
            "validate_pack",
            deepcopy(load_pack_directory(VALID_PACK).document),
        )

        assert client.server_info is not None
        assert client.server_info.version == MCP_PROTOCOL_VERSION
        assert [tool.name for tool in tools.tools] == [
            "search_foods",
            "get_public_food",
            "get_public_missions",
            "get_public_mission_activity",
            "get_release_manifest",
            "validate_pack",
        ]
        assert validation.is_error is False
        assert validation.structured_content is not None
        assert validation.structured_content["state"] == "valid"


@pytest.mark.asyncio
async def test_entrypoint_serve_wires_the_stdio_streams(monkeypatch: Any) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeServer:
        def create_initialization_options(self) -> str:
            return "options"

        async def run(self, *arguments: object) -> None:
            calls.append(arguments)

    class FakeStdio:
        async def __aenter__(self) -> tuple[str, str]:
            return "read", "write"

        async def __aexit__(self, *_arguments: object) -> None:
            return None

    fake_server = FakeServer()
    monkeypatch.setattr(entrypoint, "build_server", lambda target: fake_server)
    monkeypatch.setattr(entrypoint, "stdio_server", FakeStdio)

    await entrypoint._serve("hosted")  # noqa: SLF001 - verifies the console wiring

    assert calls == [("read", "write", "options")]


def test_entrypoint_success_returns_zero(monkeypatch: Any) -> None:
    def close_coroutine(awaitable: Any) -> None:
        awaitable.close()

    monkeypatch.setattr(entrypoint.asyncio, "run", close_coroutine)

    assert main(["--target", "hosted"]) == 0


@pytest.mark.asyncio
async def test_search_preserves_release_proof_and_logs_only_counts(
    monkeypatch: Any,
) -> None:
    log_stream = io.StringIO()
    logger = logging.getLogger("opennosh.mcp")
    handler = next(item for item in logger.handlers if item.name == "opennosh-mcp-stderr")
    monkeypatch.setattr(handler, "stream", log_stream)
    monkeypatch.setattr(logger, "disabled", True)
    service = _service()

    assert logger.disabled is False

    result = await _call(service, "search_foods", {"query": "rajma", "limit": 5})

    assert result["schema_version"] == "1.0"
    assert result["state"] == "stale_verified"
    assert "problem" not in result
    data = cast(dict[str, Any], result["data"])
    assert data["result"]["schema_version"] == "2.0"
    assert data["response"]["etag"] == '"fixture"'
    assert data["response"]["release_version"] == "0.82.1.0"
    assert data["response"]["release_state"] == "stale"
    logged = log_stream.getvalue()
    assert "method=search_foods status=success" in logged
    assert "count=0" in logged
    assert "rajma" not in logged
    assert "CC0-1.0" not in logged


@pytest.mark.asyncio
async def test_public_food_supports_latest_and_one_exact_release() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response_for(request)

    service = _service(handler)
    latest = await _call(
        service,
        "get_public_food",
        {"source": "community", "source_id": "rajma-masala"},
    )
    exact = await _call(
        service,
        "get_public_food",
        {
            "source": "community",
            "source_id": "rajma-masala",
            "release_version": "0.82.1.0",
        },
    )

    assert latest["state"] == exact["state"] == "stale_verified"
    latest_record = cast(dict[str, Any], latest["data"])["result"]["record"]
    assert latest_record["attribution"]["license"] == "CC0-1.0"
    assert latest_record["source"] == "community"
    assert requests[0].url.path == "/api/v1/public/foods/community/rajma-masala"
    assert requests[1].url.path.startswith("/api/v1/public/releases/0.82.1.0/")
    for request in requests:
        assert request.method == "GET"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert request.headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
async def test_all_remote_tools_delegate_to_the_supported_sdk() -> None:
    service = _service()
    calls = [
        ("get_public_missions", {"limit": 10}),
        ("get_public_mission_activity", {}),
        ("get_release_manifest", {"release_version": "0.82.1.0"}),
    ]

    results = [await _call(service, name, arguments) for name, arguments in calls]

    assert [result["state"] for result in results] == [
        "stale_verified",
        "stale_verified",
        "stale_verified",
    ]
    assert cast(dict[str, Any], results[0]["data"])["result"]["schema_version"] == "1.0"
    assert cast(dict[str, Any], results[1]["data"])["result"]["minimum_cohort"] == 10
    assert cast(dict[str, Any], results[2]["data"])["result"]["signature"]


@pytest.mark.asyncio
async def test_invalid_arguments_fail_before_network_or_host_selection() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response_for(request)

    service = _service(handler)
    invalid = [
        ("search_foods", {"query": "x"}),
        ("search_foods", {"query": "rajma", "limit": 101}),
        ("get_public_food", {"source": "federation", "source_id": "rajma"}),
        ("get_public_food", {"source": "community", "source_id": "../rajma"}),
        ("get_public_missions", {"limit": 10, "endpoint": "https://attacker.example"}),
        ("get_public_mission_activity", {"extra": True}),
        ("validate_pack", {"path": "/etc/passwd"}),
        ("validate_pack", {"url": "https://attacker.example/pack.json"}),
    ]

    for name, arguments in invalid:
        result = await _call(service, name, arguments)
        assert result["state"] == "invalid"
        assert cast(dict[str, object], result["problem"])["code"] == "invalid_arguments"
    assert calls == 0


@pytest.mark.asyncio
async def test_validate_pack_accepts_only_one_bounded_in_memory_object() -> None:
    service = _service()
    document = deepcopy(load_pack_directory(VALID_PACK).document)

    valid = await _call(service, "validate_pack", document)
    too_large = await _call(
        service,
        "validate_pack",
        {"payload": "x" * MAX_PACK_ARGUMENT_BYTES},
    )
    non_finite = await _call(service, "validate_pack", {"servings": float("nan")})
    direct_too_large = await service._validate_pack(  # noqa: SLF001 - handler defense in depth
        {"payload": "x" * MAX_PACK_ARGUMENT_BYTES}
    )

    assert valid["state"] == "valid"
    assert cast(dict[str, object], valid["data"])["valid"] is True
    assert too_large["state"] == "invalid"
    assert cast(dict[str, object], too_large["problem"])["code"] == "pack_too_large"
    assert cast(dict[str, object], non_finite["problem"])["code"] == "pack_invalid"
    assert cast(dict[str, object], direct_too_large["problem"])["code"] == "pack_too_large"


def test_response_state_and_count_helpers_fail_closed() -> None:
    response = OpenNoshResponse(
        data={"items": [{"id": "one"}]},
        status=200,
        url="https://nosh.example/api/v1/foods/search",
        etag=None,
        last_modified=None,
        cache_control=None,
        content_type="application/json",
        release_state="verified",
    )

    assert _response_state(response) == "verified"
    assert _response_data(response)["result"] == {"items": [{"id": "one"}]}
    assert (
        _response_state(
            cast(
                Any,
                SimpleNamespace(
                    release_state=None, data=SimpleNamespace(release=SimpleNamespace(state="stale"))
                ),
            )
        )
        == "stale_verified"
    )
    assert (
        _response_state(
            cast(
                Any,
                SimpleNamespace(
                    release_state=None,
                    data=SimpleNamespace(
                        release_set=SimpleNamespace(enabled=False, digest=None, stale=False)
                    ),
                ),
            )
        )
        == "unavailable"
    )
    assert (
        _response_state(
            cast(Any, SimpleNamespace(release_state=None, data=SimpleNamespace(state="live")))
        )
        == "verified"
    )
    assert (
        _response_state(cast(Any, SimpleNamespace(release_state=None, data=object())))
        == "unavailable"
    )
    assert _count_result({"data": {"result": "not-an-object"}}) == 0
    assert _count_result({"data": {"result": {"unknown": []}}}) == 0
    with pytest.raises(TypeError, match="structured SDK response"):
        _response_data(cast(Any, SimpleNamespace(data=b"binary")))


@pytest.mark.asyncio
async def test_handler_type_errors_are_sanitized(monkeypatch: Any) -> None:
    service = _service()

    async def reject(_arguments: dict[str, object]) -> dict[str, object]:
        raise TypeError("untrusted detail")

    monkeypatch.setitem(service._handlers, "search_foods", reject)  # noqa: SLF001
    result = await _call(service, "search_foods", {"query": "rajma"})

    assert result["state"] == "invalid"
    assert cast(dict[str, object], result["problem"])["code"] == "invalid_arguments"
    assert "untrusted detail" not in json.dumps(result)


@pytest.mark.asyncio
async def test_upstream_problems_are_typed_without_leaking_raw_bodies() -> None:
    body_sentinel = "upstream-body-sentinel"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            content=body_sentinel,
            headers={"content-type": "text/plain", "x-request-id": "safe-reference"},
            request=request,
        )

    result = await _call(_service(handler), "search_foods", {"query": "rajma"})
    serialized = json.dumps(result)

    assert result["state"] == "unavailable"
    problem = cast(dict[str, object], result["problem"])
    assert problem["status"] == 502
    assert problem["code"] == "unexpected_response"
    assert problem["request_reference"] == "safe-reference"
    assert body_sentinel not in serialized


@pytest.mark.asyncio
async def test_unknown_tool_and_tool_pagination_are_protocol_errors() -> None:
    service = _service()
    listed = await service.list_tools(cast(Any, None), None)
    assert len(listed.tools) == 6
    with pytest.raises(MCPError) as unknown:
        await service.call_tool(
            cast(Any, None),
            CallToolRequestParams(name="write_food", arguments={}),
        )
    assert unknown.value.error.code == -32601

    with pytest.raises(MCPError) as paginated:
        await service.list_tools(cast(Any, None), PaginatedRequestParams(cursor="next"))
    assert paginated.value.error.code == -32602


def test_entrypoint_rejects_unsafe_target_without_echoing_it(capsys: Any) -> None:
    unsafe_target = "https://private-user@example.test"

    assert main(["--target", unsafe_target]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "validated HTTP(S) origin" in captured.err
    assert unsafe_target not in captured.err
    assert "private-user" not in captured.err
