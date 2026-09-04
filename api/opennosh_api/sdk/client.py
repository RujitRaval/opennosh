"""Policy-bound synchronous and asynchronous clients for anonymous public reads."""

# The public async API intentionally mirrors the sync client's ``timeout`` keyword.
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Generic, TypeVar, cast
from urllib.parse import quote, urlencode, urlsplit

import httpx
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from opennosh_api.foods.schemas import FoodCapabilities, FoodSearchResponse, FoodSearchResponseV1
from opennosh_api.impact.contracts import PublicImpactSnapshot
from opennosh_api.missions.activity_service import PublicMissionActivityMap
from opennosh_api.missions.public_service import PublicMissionCatalog
from opennosh_api.problems.schemas import ProblemDetails, RecoveryAction
from opennosh_api.public.artifacts import PublicFoodRecordResponse
from opennosh_api.public_commons.manifests import SignedEnvelope
from opennosh_api.public_commons.schemas import PublicCommonsSnapshot
from opennosh_api.public_operations.contracts import (
    PublicIncidentListResponse,
    PublicStatusResponse,
)
from opennosh_api.reuse.contracts import (
    ReusePublicDeclarationResponse,
    ReusePublicDependencyListResponse,
    ReusePublicListResponse,
)
from opennosh_api.sdk._generated import PUBLIC_OPERATION_POLICIES

HOSTED_ORIGIN = "https://opennosh.org"
JSON_TIMEOUT_SECONDS = 10.0
DOWNLOAD_TIMEOUT_SECONDS = 30.0
ERROR_BODY_LIMIT = 65_536

_SOURCE_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_VERSION_PATH = _SOURCE_ROOT / "VERSION"
_IS_SOURCE_CHECKOUT = (_SOURCE_ROOT / "api/opennosh_api/sdk/client.py").is_file()
try:
    PACKAGE_VERSION = (
        _SOURCE_VERSION_PATH.read_text(encoding="utf-8").strip()
        if _IS_SOURCE_CHECKOUT and _SOURCE_VERSION_PATH.is_file()
        else version("opennosh")
    )
except (OSError, PackageNotFoundError):  # pragma: no cover - damaged package only
    PACKAGE_VERSION = "0+unknown"

T = TypeVar("T")
ResponseModel = type[BaseModel] | tuple[type[BaseModel], ...]


@dataclass(frozen=True, slots=True)
class OpenNoshResponse(Generic[T]):
    """A validated response plus cache and representation metadata."""

    data: T
    status: int
    url: str
    etag: str | None
    last_modified: str | None
    cache_control: str | None
    content_type: str | None
    release_version: str | None = None
    release_state: str | None = None
    stale_age_seconds: int | None = None
    warning: str | None = None


class OpenNoshProblem(Exception):
    """Stable public failure that never exposes an untrusted response body."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        request_reference: str | None = None,
        recovery_actions: tuple[RecoveryAction, ...] = (),
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.request_reference = request_reference
        self.recovery_actions = recovery_actions
        self.retry_after_seconds = retry_after_seconds


def normalize_target(target: str = "hosted") -> str:
    """Resolve the hosted alias or validate one exact HTTPS/loopback origin."""

    if target == "hosted":
        return HOSTED_ORIGIN
    if not isinstance(target, str) or not target or target != target.strip():
        raise TypeError("target must be 'hosted' or an absolute HTTP(S) origin")
    try:
        parsed = urlsplit(target)
        port = parsed.port
    except ValueError as error:
        raise TypeError("target must be 'hosted' or an absolute HTTP(S) origin") from error
    scheme = parsed.scheme.lower()
    if scheme not in {"https", "http"} or not parsed.hostname:
        raise TypeError("target must use HTTPS, or HTTP for an exact loopback host")
    if parsed.username is not None or parsed.password is not None:
        raise TypeError("target must not include user information")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise TypeError("target must be an origin without a path, query, or fragment")
    hostname = parsed.hostname.lower()
    if scheme == "http" and hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise TypeError("plaintext HTTP is allowed only for localhost, 127.0.0.1, or [::1]")
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    authority = rendered_host if port is None or default_port else f"{rendered_host}:{port}"
    return f"{scheme}://{authority}"


def _policy(path: str) -> dict[str, Any]:
    try:
        return PUBLIC_OPERATION_POLICIES[path]
    except KeyError as error:  # pragma: no cover - generated contract gate
        raise RuntimeError(f"Missing generated operation policy for {path}") from error


def _fill_path(template: str, values: dict[str, object]) -> str:
    policy = _policy(template)
    path = template
    for name, schema in cast(dict[str, dict[str, Any]], policy["path_parameters"]).items():
        value = values.get(name)
        if value is None or value == "":
            raise TypeError(f"{name} is required")
        if not Draft202012Validator(schema).is_valid(value):
            raise TypeError(f"{name} is invalid")
        path = path.replace("{" + name + "}", quote(str(value), safe=""))
    return path


def _url(origin: str, template: str, values: dict[str, object], query: dict[str, object]) -> str:
    pairs: list[tuple[str, str]] = []
    for name, value in query.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            pairs.extend((name, str(item)) for item in value)
        else:
            pairs.append((name, str(value).lower() if isinstance(value, bool) else str(value)))
    encoded = urlencode(pairs)
    return origin + _fill_path(template, values) + (f"?{encoded}" if encoded else "")


def _timeout(value: float | None, maximum: float) -> float:
    if value is None:
        return maximum
    if (
        not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        or value > maximum
    ):
        raise ValueError(f"timeout must be greater than 0 and at most {maximum:g} seconds")
    return float(value)


def _media_type(response: httpx.Response) -> str:
    value = cast(str, response.headers.get("content-type", ""))
    return value.partition(";")[0].strip().lower()


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("retry-after", "")
    if not value.isascii() or not value.isdecimal():
        return None
    seconds = int(value)
    return seconds if 1 <= seconds <= 86_400 else None


def _declared_size(response: httpx.Response, limit: int) -> None:
    value = response.headers.get("content-length")
    if value is None:
        return
    if not value.isascii() or not value.isdecimal() or int(value) > limit:
        raise OpenNoshProblem(
            response.status_code,
            "response_too_large",
            f"Response exceeds the {limit}-byte limit.",
        )


def _problem(response: httpx.Response, body: bytes) -> OpenNoshProblem:
    retry_after = _retry_after(response)
    if _media_type(response) == "application/problem+json":
        try:
            payload = json.loads(body.decode("utf-8", errors="strict"))
            problem = ProblemDetails.model_validate(payload)
            if problem.status == response.status_code:
                return OpenNoshProblem(
                    problem.status,
                    problem.code.value,
                    problem.detail,
                    problem.request_id,
                    tuple(problem.recovery_actions or ()),
                    retry_after,
                )
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
            pass
    return OpenNoshProblem(
        response.status_code,
        "unexpected_response",
        f"OpenNosh returned HTTP {response.status_code} without a valid problem document.",
        response.headers.get("x-request-id"),
        retry_after_seconds=retry_after,
    )


def _decode(
    response: httpx.Response,
    body: bytes,
    *,
    template: str,
    model: ResponseModel | None,
    binary: bool,
) -> object:
    if binary:
        return body
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise OpenNoshProblem(
            response.status_code,
            "unexpected_response",
            f"OpenNosh returned invalid UTF-8 for {template}.",
        ) from None
    if model is None:
        return text
    candidates = model if isinstance(model, tuple) else (model,)
    for candidate in candidates:
        try:
            return candidate.model_validate_json(text)
        except ValidationError:
            continue
    raise OpenNoshProblem(
        response.status_code,
        "unexpected_response",
        f"OpenNosh returned invalid JSON for {template}.",
    ) from None


def _metadata(response: httpx.Response, data: T) -> OpenNoshResponse[T]:
    raw_stale_age = response.headers.get("x-opennosh-stale-age")
    stale_age = (
        int(raw_stale_age)
        if raw_stale_age is not None and raw_stale_age.isascii() and raw_stale_age.isdecimal()
        else None
    )
    return OpenNoshResponse(
        data=data,
        status=response.status_code,
        url=str(response.url),
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
        cache_control=response.headers.get("cache-control"),
        content_type=response.headers.get("content-type"),
        release_version=response.headers.get("x-opennosh-release-version"),
        release_state=response.headers.get("x-opennosh-release-state"),
        stale_age_seconds=stale_age,
        warning=response.headers.get("warning"),
    )


class _ClientMethods:
    origin: str

    def _request(
        self,
        template: str,
        *,
        model: ResponseModel | None,
        values: dict[str, object] | None = None,
        query: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        allow_not_modified: bool = False,
        binary: bool = False,
        timeout_seconds: float | None = None,
    ) -> Any:
        raise NotImplementedError

    def capabilities(self, *, timeout: float | None = None) -> Any:
        return self._request(
            "/api/v1/foods/capabilities",
            model=FoodCapabilities,
            timeout_seconds=timeout,
        )

    def search_foods(
        self,
        query: str,
        *,
        locale: str | None = None,
        source: str | None = None,
        packs: tuple[str, ...] | list[str] = (),
        limit: int | None = None,
        cursor: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        if not isinstance(query, str):
            raise TypeError("search_foods requires query")
        return self._request(
            "/api/v1/foods/search",
            model=(FoodSearchResponse, FoodSearchResponseV1),
            query={
                "q": query,
                "locale": locale,
                "source": source,
                "pack": packs,
                "limit": limit,
                "cursor": cursor,
            },
            timeout_seconds=timeout,
        )

    def get_commons_snapshot(
        self, *, if_none_match: str | None = None, timeout: float | None = None
    ) -> Any:
        return self._request(
            "/api/v1/public/commons-snapshot",
            model=PublicCommonsSnapshot,
            headers={"If-None-Match": if_none_match} if if_none_match else {},
            allow_not_modified=True,
            timeout_seconds=timeout,
        )

    def get_public_food(
        self,
        source: str,
        source_id: str,
        *,
        version: str | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self._request(
            "/api/v1/public/foods/{source}/{source_id}",
            model=PublicFoodRecordResponse,
            values={"source": source, "source_id": source_id},
            query={"version": version},
            timeout_seconds=timeout,
        )

    def list_missions(self, *, limit: int | None = None, timeout: float | None = None) -> Any:
        return self._request(
            "/api/v1/public/missions",
            model=PublicMissionCatalog,
            query={"limit": limit},
            timeout_seconds=timeout,
        )

    def get_mission_activity(self, *, timeout: float | None = None) -> Any:
        return self._request(
            "/api/v1/public/missions/activity",
            model=PublicMissionActivityMap,
            timeout_seconds=timeout,
        )

    def list_reuse(self, *, timeout: float | None = None) -> Any:
        return self._request(
            "/api/v1/public/reuse",
            model=ReusePublicListResponse,
            timeout_seconds=timeout,
        )

    def list_reuse_dependencies(self, *, timeout: float | None = None) -> Any:
        return self._request(
            "/api/v1/public/reuse/dependencies",
            model=ReusePublicDependencyListResponse,
            timeout_seconds=timeout,
        )

    def get_impact(self, *, timeout: float | None = None) -> Any:
        return self._request(
            "/api/v1/public/impact",
            model=PublicImpactSnapshot,
            timeout_seconds=timeout,
        )

    def get_public_status(self, *, timeout: float | None = None) -> Any:
        return self._request(
            "/api/v1/public/status",
            model=PublicStatusResponse,
            timeout_seconds=timeout,
        )

    def list_public_incidents(self, *, timeout: float | None = None) -> Any:
        return self._request(
            "/api/v1/public/incidents",
            model=PublicIncidentListResponse,
            timeout_seconds=timeout,
        )

    def get_reuse_declaration(self, declaration_id: str, *, timeout: float | None = None) -> Any:
        return self._request(
            "/api/v1/public/reuse/{declaration_id}",
            model=ReusePublicDeclarationResponse,
            values={"declaration_id": declaration_id},
            timeout_seconds=timeout,
        )

    def get_release_food(
        self, release_version: str, source: str, source_id: str, *, timeout: float | None = None
    ) -> Any:
        return self._request(
            "/api/v1/public/releases/{release_version}/foods/{source}/{source_id}",
            model=PublicFoodRecordResponse,
            values={"release_version": release_version, "source": source, "source_id": source_id},
            timeout_seconds=timeout,
        )

    def get_provenance(
        self, release_version: str, source: str, source_id: str, *, timeout: float | None = None
    ) -> Any:
        return self._request(
            "/api/v1/public/releases/{release_version}/foods/{source}/{source_id}/provenance",
            model=None,
            values={"release_version": release_version, "source": source, "source_id": source_id},
            timeout_seconds=timeout,
        )

    def get_release_manifest(self, release_version: str, *, timeout: float | None = None) -> Any:
        return self._request(
            "/api/v1/public/releases/{release_version}/manifest",
            model=SignedEnvelope,
            values={"release_version": release_version},
            timeout_seconds=timeout,
        )

    def download_pack(
        self,
        release_version: str,
        pack_id: str,
        pack_version: str,
        *,
        timeout: float | None = None,
    ) -> Any:
        return self._request(
            "/api/v1/public/releases/{release_version}/packs/{pack_id}/{pack_version}/download",
            model=None,
            values={
                "release_version": release_version,
                "pack_id": pack_id,
                "pack_version": pack_version,
            },
            binary=True,
            timeout_seconds=timeout,
        )


class OpenNoshClient(_ClientMethods):
    """Synchronous client for the sixteen supported anonymous public operations."""

    def __init__(
        self, target: str = "hosted", *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.origin = normalize_target(target)
        self._transport = transport

    def capabilities(self, *, timeout: float | None = None) -> OpenNoshResponse[FoodCapabilities]:
        return cast(OpenNoshResponse[FoodCapabilities], super().capabilities(timeout=timeout))

    def search_foods(
        self,
        query: str,
        *,
        locale: str | None = None,
        source: str | None = None,
        packs: tuple[str, ...] | list[str] = (),
        limit: int | None = None,
        cursor: str | None = None,
        timeout: float | None = None,
    ) -> OpenNoshResponse[FoodSearchResponse | FoodSearchResponseV1]:
        return cast(
            OpenNoshResponse[FoodSearchResponse | FoodSearchResponseV1],
            super().search_foods(
                query,
                locale=locale,
                source=source,
                packs=packs,
                limit=limit,
                cursor=cursor,
                timeout=timeout,
            ),
        )

    def get_commons_snapshot(
        self, *, if_none_match: str | None = None, timeout: float | None = None
    ) -> OpenNoshResponse[PublicCommonsSnapshot | None]:
        return cast(
            OpenNoshResponse[PublicCommonsSnapshot | None],
            super().get_commons_snapshot(if_none_match=if_none_match, timeout=timeout),
        )

    def get_public_food(
        self,
        source: str,
        source_id: str,
        *,
        version: str | None = None,
        timeout: float | None = None,
    ) -> OpenNoshResponse[PublicFoodRecordResponse]:
        return cast(
            OpenNoshResponse[PublicFoodRecordResponse],
            super().get_public_food(source, source_id, version=version, timeout=timeout),
        )

    def list_missions(
        self, *, limit: int | None = None, timeout: float | None = None
    ) -> OpenNoshResponse[PublicMissionCatalog]:
        return cast(
            OpenNoshResponse[PublicMissionCatalog],
            super().list_missions(limit=limit, timeout=timeout),
        )

    def get_mission_activity(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[PublicMissionActivityMap]:
        return cast(
            OpenNoshResponse[PublicMissionActivityMap],
            super().get_mission_activity(timeout=timeout),
        )

    def list_reuse(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[ReusePublicListResponse]:
        return cast(OpenNoshResponse[ReusePublicListResponse], super().list_reuse(timeout=timeout))

    def list_reuse_dependencies(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[ReusePublicDependencyListResponse]:
        return cast(
            OpenNoshResponse[ReusePublicDependencyListResponse],
            super().list_reuse_dependencies(timeout=timeout),
        )

    def get_impact(self, *, timeout: float | None = None) -> OpenNoshResponse[PublicImpactSnapshot]:
        return cast(OpenNoshResponse[PublicImpactSnapshot], super().get_impact(timeout=timeout))

    def get_public_status(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[PublicStatusResponse]:
        return cast(
            OpenNoshResponse[PublicStatusResponse], super().get_public_status(timeout=timeout)
        )

    def list_public_incidents(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[PublicIncidentListResponse]:
        return cast(
            OpenNoshResponse[PublicIncidentListResponse],
            super().list_public_incidents(timeout=timeout),
        )

    def get_reuse_declaration(
        self, declaration_id: str, *, timeout: float | None = None
    ) -> OpenNoshResponse[ReusePublicDeclarationResponse]:
        return cast(
            OpenNoshResponse[ReusePublicDeclarationResponse],
            super().get_reuse_declaration(declaration_id, timeout=timeout),
        )

    def get_release_food(
        self,
        release_version: str,
        source: str,
        source_id: str,
        *,
        timeout: float | None = None,
    ) -> OpenNoshResponse[PublicFoodRecordResponse]:
        return cast(
            OpenNoshResponse[PublicFoodRecordResponse],
            super().get_release_food(release_version, source, source_id, timeout=timeout),
        )

    def get_provenance(
        self,
        release_version: str,
        source: str,
        source_id: str,
        *,
        timeout: float | None = None,
    ) -> OpenNoshResponse[str]:
        return cast(
            OpenNoshResponse[str],
            super().get_provenance(release_version, source, source_id, timeout=timeout),
        )

    def get_release_manifest(
        self, release_version: str, *, timeout: float | None = None
    ) -> OpenNoshResponse[SignedEnvelope]:
        return cast(
            OpenNoshResponse[SignedEnvelope],
            super().get_release_manifest(release_version, timeout=timeout),
        )

    def download_pack(
        self,
        release_version: str,
        pack_id: str,
        pack_version: str,
        *,
        timeout: float | None = None,
    ) -> OpenNoshResponse[bytes]:
        return cast(
            OpenNoshResponse[bytes],
            super().download_pack(release_version, pack_id, pack_version, timeout=timeout),
        )

    def _request(
        self,
        template: str,
        *,
        model: ResponseModel | None,
        values: dict[str, object] | None = None,
        query: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        allow_not_modified: bool = False,
        binary: bool = False,
        timeout_seconds: float | None = None,
    ) -> OpenNoshResponse[Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "OpenNoshClient cannot run inside an active event loop; "
                "use AsyncOpenNoshClient instead"
            ) from None
        return asyncio.run(
            AsyncOpenNoshClient(self.origin, transport=self._transport)._request(
                template,
                model=model,
                values=values,
                query=query,
                headers=headers,
                allow_not_modified=allow_not_modified,
                binary=binary,
                timeout_seconds=timeout_seconds,
            )
        )


class AsyncOpenNoshClient(_ClientMethods):
    """Asynchronous client with the same contract as :class:`OpenNoshClient`."""

    def __init__(
        self, target: str = "hosted", *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.origin = normalize_target(target)
        self._transport = transport

    async def capabilities(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[FoodCapabilities]:
        return cast(
            OpenNoshResponse[FoodCapabilities],
            await super().capabilities(timeout=timeout),
        )

    async def search_foods(
        self,
        query: str,
        *,
        locale: str | None = None,
        source: str | None = None,
        packs: tuple[str, ...] | list[str] = (),
        limit: int | None = None,
        cursor: str | None = None,
        timeout: float | None = None,
    ) -> OpenNoshResponse[FoodSearchResponse | FoodSearchResponseV1]:
        return cast(
            OpenNoshResponse[FoodSearchResponse | FoodSearchResponseV1],
            await super().search_foods(
                query,
                locale=locale,
                source=source,
                packs=packs,
                limit=limit,
                cursor=cursor,
                timeout=timeout,
            ),
        )

    async def get_commons_snapshot(
        self, *, if_none_match: str | None = None, timeout: float | None = None
    ) -> OpenNoshResponse[PublicCommonsSnapshot | None]:
        return cast(
            OpenNoshResponse[PublicCommonsSnapshot | None],
            await super().get_commons_snapshot(if_none_match=if_none_match, timeout=timeout),
        )

    async def get_public_food(
        self,
        source: str,
        source_id: str,
        *,
        version: str | None = None,
        timeout: float | None = None,
    ) -> OpenNoshResponse[PublicFoodRecordResponse]:
        return cast(
            OpenNoshResponse[PublicFoodRecordResponse],
            await super().get_public_food(source, source_id, version=version, timeout=timeout),
        )

    async def list_missions(
        self, *, limit: int | None = None, timeout: float | None = None
    ) -> OpenNoshResponse[PublicMissionCatalog]:
        return cast(
            OpenNoshResponse[PublicMissionCatalog],
            await super().list_missions(limit=limit, timeout=timeout),
        )

    async def get_mission_activity(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[PublicMissionActivityMap]:
        return cast(
            OpenNoshResponse[PublicMissionActivityMap],
            await super().get_mission_activity(timeout=timeout),
        )

    async def list_reuse(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[ReusePublicListResponse]:
        return cast(
            OpenNoshResponse[ReusePublicListResponse],
            await super().list_reuse(timeout=timeout),
        )

    async def list_reuse_dependencies(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[ReusePublicDependencyListResponse]:
        return cast(
            OpenNoshResponse[ReusePublicDependencyListResponse],
            await super().list_reuse_dependencies(timeout=timeout),
        )

    async def get_impact(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[PublicImpactSnapshot]:
        return cast(
            OpenNoshResponse[PublicImpactSnapshot],
            await super().get_impact(timeout=timeout),
        )

    async def get_public_status(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[PublicStatusResponse]:
        return cast(
            OpenNoshResponse[PublicStatusResponse],
            await super().get_public_status(timeout=timeout),
        )

    async def list_public_incidents(
        self, *, timeout: float | None = None
    ) -> OpenNoshResponse[PublicIncidentListResponse]:
        return cast(
            OpenNoshResponse[PublicIncidentListResponse],
            await super().list_public_incidents(timeout=timeout),
        )

    async def get_reuse_declaration(
        self, declaration_id: str, *, timeout: float | None = None
    ) -> OpenNoshResponse[ReusePublicDeclarationResponse]:
        return cast(
            OpenNoshResponse[ReusePublicDeclarationResponse],
            await super().get_reuse_declaration(declaration_id, timeout=timeout),
        )

    async def get_release_food(
        self,
        release_version: str,
        source: str,
        source_id: str,
        *,
        timeout: float | None = None,
    ) -> OpenNoshResponse[PublicFoodRecordResponse]:
        return cast(
            OpenNoshResponse[PublicFoodRecordResponse],
            await super().get_release_food(release_version, source, source_id, timeout=timeout),
        )

    async def get_provenance(
        self,
        release_version: str,
        source: str,
        source_id: str,
        *,
        timeout: float | None = None,
    ) -> OpenNoshResponse[str]:
        return cast(
            OpenNoshResponse[str],
            await super().get_provenance(release_version, source, source_id, timeout=timeout),
        )

    async def get_release_manifest(
        self, release_version: str, *, timeout: float | None = None
    ) -> OpenNoshResponse[SignedEnvelope]:
        return cast(
            OpenNoshResponse[SignedEnvelope],
            await super().get_release_manifest(release_version, timeout=timeout),
        )

    async def download_pack(
        self,
        release_version: str,
        pack_id: str,
        pack_version: str,
        *,
        timeout: float | None = None,
    ) -> OpenNoshResponse[bytes]:
        return cast(
            OpenNoshResponse[bytes],
            await super().download_pack(release_version, pack_id, pack_version, timeout=timeout),
        )

    async def _request(
        self,
        template: str,
        *,
        model: ResponseModel | None,
        values: dict[str, object] | None = None,
        query: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        allow_not_modified: bool = False,
        binary: bool = False,
        timeout_seconds: float | None = None,
    ) -> OpenNoshResponse[Any]:
        policy = _policy(template)
        maximum = DOWNLOAD_TIMEOUT_SECONDS if binary else JSON_TIMEOUT_SECONDS
        bounded_timeout = _timeout(timeout_seconds, maximum)
        request_url = _url(self.origin, template, values or {}, query or {})
        request_headers = {
            "Accept": str(policy["media_type"]),
            "Accept-Encoding": "identity",
            "X-OpenNosh-Client": f"python/{PACKAGE_VERSION}",
        }
        request_headers.update(headers or {})
        try:
            async with asyncio.timeout(bounded_timeout):
                async with (
                    httpx.AsyncClient(
                        transport=self._transport,
                        follow_redirects=False,
                        timeout=bounded_timeout,
                        trust_env=False,
                    ) as client,
                    client.stream("GET", request_url, headers=request_headers) as response,
                ):
                    if 300 <= response.status_code < 400 and not (
                        allow_not_modified and response.status_code == 304
                    ):
                        raise OpenNoshProblem(
                            response.status_code,
                            "redirect_refused",
                            "OpenNosh refused a cross-origin or redirected response.",
                            response.headers.get("x-request-id"),
                        )
                    if allow_not_modified and response.status_code == 304:
                        return _metadata(response, None)
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise OpenNoshProblem(
                            response.status_code,
                            "unexpected_response",
                            f"OpenNosh returned an unexpected content encoding for {template}.",
                        )
                    limit = int(
                        policy["max_response_bytes"] if response.is_success else ERROR_BODY_LIMIT
                    )
                    _declared_size(response, limit)
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise OpenNoshProblem(
                                response.status_code,
                                "response_too_large",
                                f"Response exceeds the {limit}-byte limit.",
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    if not response.is_success:
                        raise _problem(response, body)
                    if _media_type(response) not in policy["accepted_media_types"]:
                        raise OpenNoshProblem(
                            response.status_code,
                            "unexpected_response",
                            f"OpenNosh returned an unexpected media type for {template}.",
                        )
                    return _metadata(
                        response,
                        _decode(response, body, template=template, model=model, binary=binary),
                    )
        except OpenNoshProblem:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise OpenNoshProblem(
                504,
                "request_timeout",
                f"OpenNosh did not respond within {bounded_timeout:g} seconds.",
            ) from None
        except httpx.HTTPError:
            raise OpenNoshProblem(
                0, "network_error", "The OpenNosh endpoint could not be reached."
            ) from None
