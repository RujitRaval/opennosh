from __future__ import annotations

import asyncio
import hashlib
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from typing import Protocol, runtime_checkable

import httpx
from PIL import Image, ImageOps

EVIDENCE_IMAGE_MAX_BYTES = 10_485_760
EVIDENCE_IMAGE_MAX_DIMENSION = 20_000
EVIDENCE_IMAGE_MAX_PIXELS = 20_000_000
SANITIZED_IMAGE_MEDIA_TYPE = "image/png"
SCANNER_RESPONSE_MAX_BYTES = 4096


class EvidenceSanitizationFailureCode(StrEnum):
    SIGNATURE_MISMATCH = "signature_mismatch"
    DECODE_FAILED = "decode_failed"
    PIXEL_LIMIT_EXCEEDED = "pixel_limit_exceeded"
    ANIMATION_UNSUPPORTED = "animation_unsupported"
    METADATA_REWRITE_FAILED = "metadata_rewrite_failed"
    SANITIZED_SIZE_EXCEEDED = "sanitized_size_exceeded"
    MALWARE_DETECTED = "malware_detected"
    SCANNER_UNAVAILABLE = "scanner_unavailable"
    SANITIZED_STORAGE_UNAVAILABLE = "sanitized_storage_unavailable"
    SANITIZED_STORAGE_CONFLICT = "sanitized_storage_conflict"


class EvidenceSanitizationError(ValueError):
    def __init__(self, code: EvidenceSanitizationFailureCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class SanitizedEvidenceImage:
    payload: bytes
    media_type: str
    content_digest: str
    width: int
    height: int


@runtime_checkable
class EvidenceContentScanner(Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def version(self) -> str: ...

    async def scan(self, image: SanitizedEvidenceImage) -> None: ...


class DeterministicAllowEvidenceScanner:
    """Explicit local/test scanner; production configuration must reject it."""

    identity = "opennosh.local-deterministic-allow"
    version = "1.0"

    async def scan(self, image: SanitizedEvidenceImage) -> None:
        del image


class CallbackEvidenceScanner:
    """Narrow adapter for a bounded external malware/content-policy client."""

    def __init__(
        self,
        *,
        identity: str,
        version: str,
        callback: Callable[[SanitizedEvidenceImage], Awaitable[bool]],
        timeout_seconds: float = 5.0,
    ) -> None:
        if not identity.strip() or not version.strip():
            raise ValueError("Evidence scanner identity and version are required")
        if timeout_seconds <= 0:
            raise ValueError("Evidence scanner timeout must be positive")
        self.identity = identity
        self.version = version
        self._callback = callback
        self._timeout_seconds = timeout_seconds

    async def scan(self, image: SanitizedEvidenceImage) -> None:
        try:
            accepted = await asyncio.wait_for(
                self._callback(image),
                timeout=self._timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise EvidenceSanitizationError(
                EvidenceSanitizationFailureCode.SCANNER_UNAVAILABLE
            ) from error
        if not accepted:
            raise EvidenceSanitizationError(EvidenceSanitizationFailureCode.MALWARE_DETECTED)


class HttpEvidenceScanner:
    """Bounded scanner adapter that sends only rewritten bytes and safe dimensions."""

    identity = "opennosh.http-evidence-scanner"
    version = "1.0"

    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str,
        timeout_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("Evidence scanner endpoint must use HTTPS")
        if not bearer_token:
            raise ValueError("Evidence scanner bearer token is required")
        if not 0 < timeout_seconds <= 10:
            raise ValueError("Evidence scanner timeout must be in (0, 10] seconds")
        self._endpoint = endpoint
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds
        self._client = client

    async def scan(self, image: SanitizedEvidenceImage) -> None:
        client = self._client or httpx.AsyncClient(
            timeout=self._timeout_seconds,
            follow_redirects=False,
        )
        try:
            async with client.stream(
                "POST",
                self._endpoint,
                content=image.payload,
                headers={
                    "authorization": f"Bearer {self._bearer_token}",
                    "content-type": image.media_type,
                    "x-content-sha256": image.content_digest,
                    "x-image-width": str(image.width),
                    "x-image-height": str(image.height),
                },
            ) as response:
                if response.status_code != 200:
                    raise EvidenceSanitizationError(
                        EvidenceSanitizationFailureCode.SCANNER_UNAVAILABLE
                    )
                declared_length = response.headers.get("content-length")
                if (
                    declared_length is not None
                    and int(declared_length) > SCANNER_RESPONSE_MAX_BYTES
                ):
                    raise EvidenceSanitizationError(
                        EvidenceSanitizationFailureCode.SCANNER_UNAVAILABLE
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > SCANNER_RESPONSE_MAX_BYTES:
                        raise EvidenceSanitizationError(
                            EvidenceSanitizationFailureCode.SCANNER_UNAVAILABLE
                        )
                    chunks.append(chunk)
            payload = b"".join(chunks)
            result = httpx.Response(200, content=payload).json()
            if not isinstance(result, dict) or not isinstance(result.get("accepted"), bool):
                raise EvidenceSanitizationError(
                    EvidenceSanitizationFailureCode.SCANNER_UNAVAILABLE
                )
            if not result["accepted"]:
                raise EvidenceSanitizationError(
                    EvidenceSanitizationFailureCode.MALWARE_DETECTED
                )
        except asyncio.CancelledError:
            raise
        except EvidenceSanitizationError:
            raise
        except Exception as error:
            raise EvidenceSanitizationError(
                EvidenceSanitizationFailureCode.SCANNER_UNAVAILABLE
            ) from error
        finally:
            if self._client is None:
                await client.aclose()


def sanitize_evidence_image(
    payload: bytes,
    *,
    declared_media_type: str,
    max_bytes: int = EVIDENCE_IMAGE_MAX_BYTES,
    max_dimension: int = EVIDENCE_IMAGE_MAX_DIMENSION,
    max_pixels: int = EVIDENCE_IMAGE_MAX_PIXELS,
) -> SanitizedEvidenceImage:
    """Decode one bounded image and return a fresh metadata-free PNG."""

    if not 1 <= max_bytes <= EVIDENCE_IMAGE_MAX_BYTES:
        raise ValueError("Evidence image byte bound is invalid")
    if not 1 <= max_dimension <= EVIDENCE_IMAGE_MAX_DIMENSION:
        raise ValueError("Evidence image dimension bound is invalid")
    if not 1 <= max_pixels <= EVIDENCE_IMAGE_MAX_PIXELS:
        raise ValueError("Evidence image pixel bound is invalid")
    if not payload or len(payload) > max_bytes:
        raise EvidenceSanitizationError(EvidenceSanitizationFailureCode.DECODE_FAILED)
    detected_media_type = _detected_media_type(payload)
    if detected_media_type != declared_media_type.strip().lower():
        raise EvidenceSanitizationError(EvidenceSanitizationFailureCode.SIGNATURE_MISMATCH)
    if not _has_exact_container_boundary(payload, detected_media_type):
        raise EvidenceSanitizationError(EvidenceSanitizationFailureCode.DECODE_FAILED)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as probe:
                _require_expected_format(probe, detected_media_type)
                _require_single_frame(probe)
                _require_pixel_bounds(
                    probe.width,
                    probe.height,
                    max_dimension=max_dimension,
                    max_pixels=max_pixels,
                )
                probe.verify()
            with Image.open(BytesIO(payload)) as decoded:
                _require_expected_format(decoded, detected_media_type)
                _require_single_frame(decoded)
                _require_pixel_bounds(
                    decoded.width,
                    decoded.height,
                    max_dimension=max_dimension,
                    max_pixels=max_pixels,
                )
                decoded.load()
                oriented = ImageOps.exif_transpose(decoded)
                mode = "RGBA" if "A" in oriented.getbands() else "RGB"
                pixels = oriented.convert(mode)
                fresh = Image.new(mode, pixels.size)
                fresh.paste(pixels)
                output = BytesIO()
                fresh.save(output, format="PNG", optimize=False, compress_level=9)
    except EvidenceSanitizationError:
        raise
    except Image.DecompressionBombError as error:
        raise EvidenceSanitizationError(
            EvidenceSanitizationFailureCode.PIXEL_LIMIT_EXCEEDED
        ) from error
    except Image.DecompressionBombWarning as error:
        raise EvidenceSanitizationError(
            EvidenceSanitizationFailureCode.PIXEL_LIMIT_EXCEEDED
        ) from error
    except Exception as error:
        raise EvidenceSanitizationError(EvidenceSanitizationFailureCode.DECODE_FAILED) from error

    sanitized = output.getvalue()
    if not sanitized or len(sanitized) > max_bytes:
        raise EvidenceSanitizationError(
            EvidenceSanitizationFailureCode.SANITIZED_SIZE_EXCEEDED
        )
    try:
        with Image.open(BytesIO(sanitized)) as verified:
            if verified.format != "PNG" or getattr(verified, "n_frames", 1) != 1:
                raise ValueError("Sanitized output is not one PNG image")
            verified.verify()
        with Image.open(BytesIO(sanitized)) as verified:
            verified.load()
            if verified.info:
                raise ValueError("Sanitized output retained metadata")
            width, height = verified.size
    except Exception as error:
        raise EvidenceSanitizationError(
            EvidenceSanitizationFailureCode.METADATA_REWRITE_FAILED
        ) from error
    return SanitizedEvidenceImage(
        payload=sanitized,
        media_type=SANITIZED_IMAGE_MEDIA_TYPE,
        content_digest=hashlib.sha256(sanitized).hexdigest(),
        width=width,
        height=height,
    )


def _detected_media_type(payload: bytes) -> str:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    raise EvidenceSanitizationError(EvidenceSanitizationFailureCode.SIGNATURE_MISMATCH)


def _has_exact_container_boundary(payload: bytes, media_type: str) -> bool:
    if media_type == "image/png":
        return payload.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    if media_type == "image/jpeg":
        return payload.endswith(b"\xff\xd9")
    if media_type == "image/webp":
        return len(payload) >= 12 and int.from_bytes(payload[4:8], "little") + 8 == len(payload)
    return False


def _require_expected_format(image: Image.Image, media_type: str) -> None:
    expected = {
        "image/jpeg": "JPEG",
        "image/png": "PNG",
        "image/webp": "WEBP",
    }[media_type]
    if image.format != expected:
        raise EvidenceSanitizationError(EvidenceSanitizationFailureCode.SIGNATURE_MISMATCH)


def _require_single_frame(image: Image.Image) -> None:
    if getattr(image, "n_frames", 1) != 1 or getattr(image, "is_animated", False):
        raise EvidenceSanitizationError(EvidenceSanitizationFailureCode.ANIMATION_UNSUPPORTED)


def _require_pixel_bounds(
    width: int,
    height: int,
    *,
    max_dimension: int,
    max_pixels: int,
) -> None:
    if (
        not 1 <= width <= max_dimension
        or not 1 <= height <= max_dimension
        or width * height > max_pixels
    ):
        raise EvidenceSanitizationError(
            EvidenceSanitizationFailureCode.PIXEL_LIMIT_EXCEEDED
        )
