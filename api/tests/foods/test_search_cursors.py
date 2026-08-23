from __future__ import annotations

import base64
import hmac
import json
from uuid import UUID

import pytest
from opennosh_api.foods.cursors import (
    SEARCH_CURSOR_MAX_LENGTH,
    CursorSigningKey,
    SearchCursorError,
    SearchCursorFailure,
    SearchCursorKeyRing,
    SearchCursorPayload,
    search_fingerprint,
)
from opennosh_api.settings import Settings

_CURRENT = CursorSigningKey("v2", b"current-search-cursor-secret-000002")
_PREVIOUS = CursorSigningKey("v1", b"previous-search-cursor-secret-0001")


def _payload(*, expires_at: int = 2_000_000_000) -> SearchCursorPayload:
    return SearchCursorPayload(
        v=1,
        sid=UUID("018f7d40-7b60-7000-8000-000000000001"),
        fp=search_fingerprint(query="apple", locale="en-us", source=None),
        rv=1,
        pos=(1, "0.75", "apple", "community", "apple"),
        size=20,
        exp=expires_at,
    )


def test_cursor_round_trip_uses_current_key_without_exposing_query_text() -> None:
    ring = SearchCursorKeyRing((_CURRENT, _PREVIOUS))

    token = ring.encode(_payload())
    decoded = ring.decode(token, now=1_900_000_000)
    payload_json = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))

    assert decoded == _payload()
    assert json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))["kid"] == "v2"
    assert "query" not in payload_json
    assert "apple" not in token


def test_previous_key_remains_valid_during_rotation() -> None:
    old_ring = SearchCursorKeyRing((_PREVIOUS,))
    rotated_ring = SearchCursorKeyRing((_CURRENT, _PREVIOUS))

    assert rotated_ring.decode(old_ring.encode(_payload()), now=1_900_000_000) == _payload()


def test_signed_unsupported_schema_version_requires_restart() -> None:
    ring = SearchCursorKeyRing((_CURRENT,))
    parts = ring.encode(_payload()).split(".")
    document = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    document["v"] = 2
    parts[1] = (
        base64.urlsafe_b64encode(
            json.dumps(document, separators=(",", ":"), sort_keys=True).encode("ascii")
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    signed = f"{parts[0]}.{parts[1]}".encode("ascii")
    parts[2] = (
        base64.urlsafe_b64encode(hmac.digest(_CURRENT.secret, signed, "sha256"))
        .rstrip(b"=")
        .decode("ascii")
    )

    with pytest.raises(SearchCursorError) as raised:
        ring.decode(".".join(parts), now=1_900_000_000)

    assert raised.value.failure is SearchCursorFailure.RESTART


def test_signed_unsupported_ranking_version_requires_restart() -> None:
    ring = SearchCursorKeyRing((_CURRENT,))
    parts = ring.encode(_payload()).split(".")
    document = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    document["rv"] = 2
    parts[1] = (
        base64.urlsafe_b64encode(
            json.dumps(document, separators=(",", ":"), sort_keys=True).encode("ascii")
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    signed = f"{parts[0]}.{parts[1]}".encode("ascii")
    parts[2] = (
        base64.urlsafe_b64encode(hmac.digest(_CURRENT.secret, signed, "sha256"))
        .rstrip(b"=")
        .decode("ascii")
    )

    with pytest.raises(SearchCursorError) as raised:
        ring.decode(".".join(parts), now=1_900_000_000)

    assert raised.value.failure is SearchCursorFailure.RESTART


@pytest.mark.parametrize(("field", "value"), [("v", True), ("rv", "1")])
def test_signed_noninteger_versions_are_invalid(field: str, value: object) -> None:
    ring = SearchCursorKeyRing((_CURRENT,))
    parts = ring.encode(_payload()).split(".")
    document = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    document[field] = value
    parts[1] = (
        base64.urlsafe_b64encode(
            json.dumps(document, separators=(",", ":"), sort_keys=True).encode("ascii")
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    signed = f"{parts[0]}.{parts[1]}".encode("ascii")
    parts[2] = (
        base64.urlsafe_b64encode(hmac.digest(_CURRENT.secret, signed, "sha256"))
        .rstrip(b"=")
        .decode("ascii")
    )

    with pytest.raises(SearchCursorError) as raised:
        ring.decode(".".join(parts), now=1_900_000_000)

    assert raised.value.failure is SearchCursorFailure.INVALID


def test_missing_schema_version_is_invalid() -> None:
    ring = SearchCursorKeyRing((_CURRENT,))
    document = _payload().model_dump(mode="json")
    document.pop("v")
    header = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"alg": "HS256", "kid": _CURRENT.key_id, "typ": "ONSC"},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(document, separators=(",", ":"), sort_keys=True).encode("ascii")
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    signed = f"{header}.{payload}".encode("ascii")
    signature = (
        base64.urlsafe_b64encode(hmac.digest(_CURRENT.secret, signed, "sha256"))
        .rstrip(b"=")
        .decode("ascii")
    )

    with pytest.raises(SearchCursorError) as raised:
        ring.decode(f"{header}.{payload}.{signature}", now=1_900_000_000)

    assert raised.value.failure is SearchCursorFailure.INVALID


def test_retired_key_requires_restart() -> None:
    retired_ring = SearchCursorKeyRing((_PREVIOUS,))
    current_ring = SearchCursorKeyRing((_CURRENT,))

    with pytest.raises(SearchCursorError) as raised:
        current_ring.decode(retired_ring.encode(_payload()), now=1_900_000_000)

    assert raised.value.failure is SearchCursorFailure.RESTART


def test_noncanonical_signature_padding_bits_are_rejected() -> None:
    ring = SearchCursorKeyRing((_CURRENT,))
    parts = ring.encode(_payload()).split(".")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last_index = alphabet.index(parts[2][-1])
    assert last_index % 4 == 0
    parts[2] = parts[2][:-1] + alphabet[last_index + 1]

    with pytest.raises(SearchCursorError) as raised:
        ring.decode(".".join(parts), now=1_900_000_000)

    assert raised.value.failure is SearchCursorFailure.INVALID


@pytest.mark.parametrize("mutation", ["payload", "signature"])
def test_tampered_cursor_is_rejected(mutation: str) -> None:
    ring = SearchCursorKeyRing((_CURRENT,))
    parts = ring.encode(_payload()).split(".")
    index = 1 if mutation == "payload" else 2
    parts[index] = ("A" if parts[index][0] != "A" else "B") + parts[index][1:]

    with pytest.raises(SearchCursorError) as raised:
        ring.decode(".".join(parts), now=1_900_000_000)

    assert raised.value.failure is SearchCursorFailure.INVALID


def test_expired_malformed_and_oversized_cursors_are_classified() -> None:
    ring = SearchCursorKeyRing((_CURRENT,))

    with pytest.raises(SearchCursorError) as expired:
        ring.decode(ring.encode(_payload(expires_at=100)), now=100)
    with pytest.raises(SearchCursorError) as malformed:
        ring.decode("not-a-cursor", now=1)
    with pytest.raises(SearchCursorError) as oversized:
        ring.decode("x" * (SEARCH_CURSOR_MAX_LENGTH + 1), now=1)

    assert expired.value.failure is SearchCursorFailure.EXPIRED
    assert malformed.value.failure is SearchCursorFailure.INVALID
    assert oversized.value.failure is SearchCursorFailure.INVALID


def test_fingerprint_binds_exact_normalized_query_locale_and_filter() -> None:
    baseline = search_fingerprint(query="Apple", locale="en-us", source=None)

    assert baseline != search_fingerprint(query="apple", locale="en-us", source=None)
    assert search_fingerprint(query="Straße", locale="de-de", source=None) != search_fingerprint(
        query="STRASSE", locale="de-de", source=None
    )
    assert baseline != search_fingerprint(query="apple", locale="en-gb", source=None)
    assert baseline != search_fingerprint(query="apple", locale="en-us", source="community")


def test_key_ring_and_lifetime_settings_are_validated() -> None:
    with pytest.raises(ValueError, match="one or two keys"):
        Settings(
            food_search_cursor_signing_keys=(
                "v3:33333333333333333333333333333333,"
                "v2:22222222222222222222222222222222,"
                "v1:11111111111111111111111111111111"
            ),
            _env_file=None,
        )
    with pytest.raises(ValueError, match="retention"):
        Settings(
            food_search_cursor_lifetime_seconds=1_201,
            food_search_snapshot_retention_seconds=1_200,
            _env_file=None,
        )
    with pytest.raises(ValueError, match="unique food search"):
        Settings(app_environment="production", _env_file=None)
