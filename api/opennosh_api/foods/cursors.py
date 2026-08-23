from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

SEARCH_CURSOR_SCHEMA_VERSION: Literal[1] = 1
SEARCH_RANKING_VERSION: Literal[1] = 1
SEARCH_CURSOR_MAX_LENGTH = 2_048


class SearchCursorFailure(StrEnum):
    INVALID = "invalid"
    EXPIRED = "expired"
    RESTART = "restart"


class SearchCursorError(ValueError):
    def __init__(self, failure: SearchCursorFailure, detail: str) -> None:
        super().__init__(detail)
        self.failure = failure
        self.detail = detail


class SearchCursorPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    v: Literal[1]
    sid: UUID
    fp: str = Field(pattern=r"^[0-9a-f]{64}$")
    rv: Literal[1]
    pos: tuple[int, str, str, str, str]
    size: int = Field(ge=1, le=50)
    exp: int = Field(gt=0)


@dataclass(frozen=True)
class CursorSigningKey:
    key_id: str
    secret: bytes


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not value or not value.isascii():
        raise ValueError("cursor segment is not URL-safe ASCII")
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    if _b64encode(decoded) != value:
        raise ValueError("cursor segment is not canonical base64url")
    return decoded


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


class SearchCursorKeyRing:
    """Sign with N and verify with N/N-1 during the compatibility window."""

    def __init__(self, keys: tuple[CursorSigningKey, ...]) -> None:
        if not 1 <= len(keys) <= 2:
            raise ValueError("Search cursor key ring must contain one or two keys")
        if len({key.key_id for key in keys}) != len(keys):
            raise ValueError("Search cursor key identifiers must be unique")
        self._keys = keys
        self._by_id: Mapping[str, CursorSigningKey] = {key.key_id: key for key in keys}

    @classmethod
    def from_secret(cls, value: SecretStr) -> SearchCursorKeyRing:
        entries: list[CursorSigningKey] = []
        for raw_entry in value.get_secret_value().split(","):
            key_id, separator, raw_secret = raw_entry.strip().partition(":")
            if (
                not separator
                or not key_id
                or len(key_id) > 32
                or not key_id.replace("-", "").replace("_", "").isalnum()
            ):
                raise ValueError("Search cursor keys must use kid:secret entries")
            secret = raw_secret.encode("utf-8")
            if len(secret) < 32:
                raise ValueError("Search cursor signing secrets must be at least 32 bytes")
            entries.append(CursorSigningKey(key_id=key_id, secret=secret))
        return cls(tuple(entries))

    @property
    def current_key_id(self) -> str:
        return self._keys[0].key_id

    def encode(self, payload: SearchCursorPayload) -> str:
        key = self._keys[0]
        header_segment = _b64encode(
            _canonical_json({"alg": "HS256", "kid": key.key_id, "typ": "ONSC"})
        )
        payload_segment = _b64encode(_canonical_json(payload.model_dump(mode="json")))
        signed = f"{header_segment}.{payload_segment}".encode("ascii")
        signature = _b64encode(hmac.digest(key.secret, signed, "sha256"))
        return f"{header_segment}.{payload_segment}.{signature}"

    def decode(
        self,
        token: str,
        *,
        now: int | None = None,
    ) -> SearchCursorPayload:
        if len(token) > SEARCH_CURSOR_MAX_LENGTH:
            raise SearchCursorError(
                SearchCursorFailure.INVALID,
                "The search cursor is too large.",
            )
        try:
            header_segment, payload_segment, signature_segment = token.split(".")
            header = json.loads(_b64decode(header_segment))
            if (
                not isinstance(header, dict)
                or set(header) != {"alg", "kid", "typ"}
                or header.get("alg") != "HS256"
                or header.get("typ") != "ONSC"
                or not isinstance(header.get("kid"), str)
            ):
                raise ValueError("unsupported cursor header")
            key = self._by_id.get(header["kid"])
            if key is None:
                raise SearchCursorError(
                    SearchCursorFailure.RESTART,
                    "This search cursor uses a retired signing key.",
                )
            signed = f"{header_segment}.{payload_segment}".encode("ascii")
            supplied_signature = _b64decode(signature_segment)
            expected_signature = hmac.digest(key.secret, signed, "sha256")
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise SearchCursorError(
                    SearchCursorFailure.INVALID,
                    "The search cursor signature is invalid.",
                )
            payload_bytes = _b64decode(payload_segment)
            payload_document = json.loads(payload_bytes)
            if isinstance(payload_document, dict):
                for field, expected, label in (
                    ("v", SEARCH_CURSOR_SCHEMA_VERSION, "schema"),
                    ("rv", SEARCH_RANKING_VERSION, "ranking"),
                ):
                    if field not in payload_document:
                        continue
                    version = payload_document[field]
                    if not isinstance(version, int) or isinstance(version, bool):
                        raise ValueError(f"cursor {label} version must be an integer")
                    if version != expected:
                        raise SearchCursorError(
                            SearchCursorFailure.RESTART,
                            f"This search cursor uses an unsupported {label} version.",
                        )
            payload = SearchCursorPayload.model_validate_json(payload_bytes, strict=True)
        except SearchCursorError:
            raise
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError, ValidationError) as error:
            raise SearchCursorError(
                SearchCursorFailure.INVALID,
                "The search cursor is malformed.",
            ) from error
        if payload.exp <= (int(time.time()) if now is None else now):
            raise SearchCursorError(
                SearchCursorFailure.EXPIRED,
                "This search cursor has expired.",
            )
        return payload


def search_fingerprint(
    *,
    query: str,
    locale: str | None,
    source: str | None,
) -> str:
    canonical = _canonical_json(
        {
            "locale": locale,
            "query": query,
            "source": source,
        }
    )
    return hashlib.sha256(canonical).hexdigest()
