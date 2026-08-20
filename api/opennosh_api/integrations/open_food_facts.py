from __future__ import annotations

import asyncio
import json
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, DecimalException, InvalidOperation
from typing import Any

import httpx
from pydantic import ValidationError

from opennosh_api.nutrition import NutrientProfile

OPEN_FOOD_FACTS_DATABASE_LICENSE = "ODbL-1.0"
OPEN_FOOD_FACTS_CONTENTS_LICENSE = "DbCL-1.0"
OPEN_FOOD_FACTS_ATTRIBUTION = (
    "Open Food Facts contributors; database licensed under ODbL 1.0 and "
    "individual contents under DbCL 1.0."
)
OPEN_FOOD_FACTS_FIELDS = (
    "code",
    "product_name",
    "brands",
    "nutriments",
    "nutrition",
)
_GTIN_LENGTHS = frozenset({8, 12, 13, 14})
_MAX_RESPONSE_BYTES = 1024 * 1024
_BIDI_CONTROL_CHARACTERS = frozenset(
    {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)
_DIRECT_NUTRIENTS = {
    "energy-kcal_100g": ("energy_kcal", Decimal(1)),
    "proteins_100g": ("protein_g", Decimal(1)),
    "fat_100g": ("fat_g", Decimal(1)),
    "carbohydrates_100g": ("carbohydrate_g", Decimal(1)),
    "fiber_100g": ("fiber_g", Decimal(1)),
    "sugars_100g": ("sugars_g", Decimal(1)),
    "saturated-fat_100g": ("saturated_fat_g", Decimal(1)),
    "salt_100g": ("salt_g", Decimal(1)),
    "sodium_100g": ("sodium_mg", Decimal(1000)),
}
_NESTED_NUTRIENTS = {
    "energy-kcal": ("energy_kcal", "kcal"),
    "proteins": ("protein_g", "g"),
    "fat": ("fat_g", "g"),
    "carbohydrates": ("carbohydrate_g", "g"),
    "fiber": ("fiber_g", "g"),
    "sugars": ("sugars_g", "g"),
    "saturated-fat": ("saturated_fat_g", "g"),
    "salt": ("salt_g", "g"),
    "sodium": ("sodium_mg", "mg"),
}


class OpenFoodFactsError(RuntimeError):
    """A controlled failure at the Open Food Facts trust boundary."""


class OpenFoodFactsNotFoundError(OpenFoodFactsError):
    """The barcode has no Open Food Facts product."""


class OpenFoodFactsTimeoutError(OpenFoodFactsError):
    """Open Food Facts did not answer within the configured time budget."""


class OpenFoodFactsRateLimitedError(OpenFoodFactsError):
    def __init__(self, retry_after: str | None = None) -> None:
        super().__init__("Open Food Facts rate limited the lookup")
        self.retry_after = retry_after


class OpenFoodFactsUpstreamError(OpenFoodFactsError):
    """Open Food Facts returned an unusable response."""


@dataclass(frozen=True)
class OpenFoodFactsProduct:
    barcode: str
    product_name: str
    brand: str | None
    nutrients_json: dict[str, Any]
    source_url: str
    attribution_text: str = OPEN_FOOD_FACTS_ATTRIBUTION


def normalize_barcode(value: str) -> str:
    if not value.isascii() or not value.isdigit() or len(value) not in _GTIN_LENGTHS:
        raise ValueError("barcode must be an 8, 12, 13, or 14 digit GTIN")
    digits = [int(character) for character in value]
    body = digits[:-1]
    weighted_sum = sum(
        digit * (3 if offset % 2 == 0 else 1)
        for offset, digit in enumerate(reversed(body))
    )
    expected_check_digit = (10 - weighted_sum % 10) % 10
    if digits[-1] != expected_check_digit:
        raise ValueError("barcode has an invalid GTIN check digit")
    return value


def _plain_text(value: object, *, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise OpenFoodFactsUpstreamError(f"Open Food Facts {label} is missing")
    if any(
        unicodedata.category(character) in {"Cc", "Cs"}
        or character in _BIDI_CONTROL_CHARACTERS
        for character in value
    ):
        raise OpenFoodFactsUpstreamError(f"Open Food Facts {label} is invalid")
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= maximum or any(
        character in "<>" for character in normalized
    ):
        raise OpenFoodFactsUpstreamError(f"Open Food Facts {label} is invalid")
    return normalized


def _optional_plain_text(value: object, *, label: str, maximum: int) -> str | None:
    if value is None or value == "":
        return None
    return _plain_text(value, label=label, maximum=maximum)


def _decimal(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise OpenFoodFactsUpstreamError(f"Open Food Facts {label} is invalid")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise OpenFoodFactsUpstreamError(f"Open Food Facts {label} is invalid") from error
    if not parsed.is_finite() or parsed < 0:
        raise OpenFoodFactsUpstreamError(f"Open Food Facts {label} is invalid")
    return parsed


def _direct_nutrients(product: dict[str, Any]) -> dict[str, Decimal] | None:
    raw = product.get("nutriments")
    if not isinstance(raw, dict):
        return None
    values: dict[str, Decimal] = {}
    for source_key, (target_key, multiplier) in _DIRECT_NUTRIENTS.items():
        if source_key in raw:
            values[target_key] = _decimal(raw[source_key], label=source_key) * multiplier
    return values


def _nested_nutrients(product: dict[str, Any]) -> dict[str, Decimal] | None:
    nutrition = product.get("nutrition")
    if not isinstance(nutrition, dict):
        return None
    aggregated = nutrition.get("aggregated_set")
    if not isinstance(aggregated, dict) or aggregated.get("per") != "100g":
        return None
    raw = aggregated.get("nutrients")
    if not isinstance(raw, dict):
        return None
    values: dict[str, Decimal] = {}
    for source_key, (target_key, target_unit) in _NESTED_NUTRIENTS.items():
        item = raw.get(source_key)
        if not isinstance(item, dict):
            continue
        value = item.get("value_computed", item.get("value"))
        unit = item.get("unit")
        parsed = _decimal(value, label=f"nutrition.{source_key}")
        if source_key == "sodium" and unit == "g":
            parsed *= Decimal(1000)
        elif unit != target_unit:
            raise OpenFoodFactsUpstreamError(
                f"Open Food Facts nutrition.{source_key} has an unsupported unit"
            )
        values[target_key] = parsed
    return values


def parse_product(payload: object, barcode: str) -> OpenFoodFactsProduct:
    normalized_barcode = normalize_barcode(barcode)
    if not isinstance(payload, dict):
        raise OpenFoodFactsUpstreamError("Open Food Facts returned invalid JSON")
    product = payload.get("product")
    if not isinstance(product, dict):
        raise OpenFoodFactsUpstreamError("Open Food Facts returned no product object")
    if product.get("code") != normalized_barcode:
        raise OpenFoodFactsUpstreamError("Open Food Facts returned a different barcode")
    profile: NutrientProfile | None = None
    last_error: (
        OpenFoodFactsUpstreamError | ValidationError | DecimalException | None
    ) = None
    for parser in (_nested_nutrients, _direct_nutrients):
        try:
            nutrients = parser(product)
            if nutrients is not None:
                profile = NutrientProfile.from_authoritative_source(nutrients)
                break
        except (OpenFoodFactsUpstreamError, ValidationError, DecimalException) as error:
            last_error = error
    if profile is None:
        raise OpenFoodFactsUpstreamError(
            "Open Food Facts returned incomplete or invalid per-100g nutrition"
        ) from last_error
    return OpenFoodFactsProduct(
        barcode=normalized_barcode,
        product_name=_plain_text(product.get("product_name"), label="product name", maximum=500),
        brand=_optional_plain_text(product.get("brands"), label="brand", maximum=255),
        nutrients_json=profile.model_dump(mode="json"),
        source_url=f"https://world.openfoodfacts.org/product/{normalized_barcode}",
    )


class OpenFoodFactsClient:
    def __init__(
        self,
        *,
        base_url: str,
        app_version: str,
        contact: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._user_agent = f"opennosh/{app_version} ({contact})"
        self._timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
            transport=transport,
            headers={
                "User-Agent": self._user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, barcode: str) -> OpenFoodFactsProduct:
        normalized_barcode = normalize_barcode(barcode)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._client.stream(
                    "GET",
                    f"{self._base_url}/api/v3/product/{normalized_barcode}",
                    params={"fields": ",".join(OPEN_FOOD_FACTS_FIELDS)},
                ) as response:
                    if response.status_code == 404:
                        raise OpenFoodFactsNotFoundError
                    if response.status_code in {429, 503}:
                        raise OpenFoodFactsRateLimitedError(
                            response.headers.get("Retry-After")
                        )
                    if response.status_code != 200:
                        raise OpenFoodFactsUpstreamError(
                            f"Open Food Facts returned HTTP {response.status_code}"
                        )
                    content_encoding = response.headers.get(
                        "Content-Encoding", "identity"
                    ).strip().casefold()
                    if content_encoding != "identity":
                        raise OpenFoodFactsUpstreamError(
                            "Open Food Facts returned an unsupported content encoding"
                        )
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None:
                        try:
                            if not 0 <= int(content_length) <= _MAX_RESPONSE_BYTES:
                                raise OpenFoodFactsUpstreamError(
                                    "Open Food Facts response is too large"
                                )
                        except ValueError as error:
                            raise OpenFoodFactsUpstreamError(
                                "Open Food Facts returned an invalid Content-Length"
                            ) from error
                    content = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(content) + len(chunk) > _MAX_RESPONSE_BYTES:
                            raise OpenFoodFactsUpstreamError(
                                "Open Food Facts response is too large"
                            )
                        content.extend(chunk)
        except (TimeoutError, httpx.TimeoutException) as error:
            raise OpenFoodFactsTimeoutError from error
        except httpx.RequestError as error:
            raise OpenFoodFactsUpstreamError("Open Food Facts request failed") from error
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, ValueError, RecursionError) as error:
            raise OpenFoodFactsUpstreamError("Open Food Facts returned invalid JSON") from error
        return parse_product(payload, normalized_barcode)
