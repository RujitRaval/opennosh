from __future__ import annotations

import asyncio
from io import BytesIO

import httpx
import pytest
from opennosh_api.evidence.sanitization import (
    CallbackEvidenceScanner,
    EvidenceSanitizationError,
    EvidenceSanitizationFailureCode,
    HttpEvidenceScanner,
    _has_exact_container_boundary,
    _require_expected_format,
    sanitize_evidence_image,
)
from PIL import Image


def _image_bytes(
    image_format: str,
    *,
    size: tuple[int, int] = (4, 3),
    metadata: bool = False,
) -> bytes:
    image = Image.new("RGB", size, (214, 75, 52))
    output = BytesIO()
    options: dict[str, object] = {}
    if metadata and image_format == "JPEG":
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = "private source description"
        options["exif"] = exif
        options["icc_profile"] = b"private-color-profile"
    if metadata and image_format == "PNG":
        from PIL.PngImagePlugin import PngInfo

        text = PngInfo()
        text.add_text("Comment", "private comment")
        options["pnginfo"] = text
    image.save(output, format=image_format, **options)
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_format", "media_type"),
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")],
)
def test_sanitizer_rewrites_supported_images_as_metadata_free_png(
    image_format: str,
    media_type: str,
) -> None:
    result = sanitize_evidence_image(
        _image_bytes(image_format, metadata=image_format != "WEBP"),
        declared_media_type=media_type,
    )

    assert result.media_type == "image/png"
    assert len(result.content_digest) == 64
    with Image.open(BytesIO(result.payload)) as image:
        image.load()
        assert image.format == "PNG"
        assert image.info == {}
        assert image.getexif() == {}
        expected_size = (3, 4) if image_format == "JPEG" else (4, 3)
        assert image.size == expected_size


@pytest.mark.parametrize(
    ("payload", "declared", "code"),
    [
        (b"not-an-image", "image/png", EvidenceSanitizationFailureCode.SIGNATURE_MISMATCH),
        (
            _image_bytes("PNG"),
            "image/jpeg",
            EvidenceSanitizationFailureCode.SIGNATURE_MISMATCH,
        ),
        (
            _image_bytes("PNG") + b"<script>alert(1)</script>",
            "image/png",
            EvidenceSanitizationFailureCode.DECODE_FAILED,
        ),
        (
            _image_bytes("JPEG")[:-1],
            "image/jpeg",
            EvidenceSanitizationFailureCode.DECODE_FAILED,
        ),
    ],
)
def test_sanitizer_rejects_spoofed_polyglot_and_truncated_input(
    payload: bytes,
    declared: str,
    code: EvidenceSanitizationFailureCode,
) -> None:
    with pytest.raises(EvidenceSanitizationError, match=code.value) as raised:
        sanitize_evidence_image(payload, declared_media_type=declared)

    assert raised.value.code is code


def test_sanitizer_rejects_pixel_bound_before_full_decode() -> None:
    with pytest.raises(EvidenceSanitizationError, match="pixel_limit_exceeded"):
        sanitize_evidence_image(
            _image_bytes("PNG", size=(5, 5)),
            declared_media_type="image/png",
            max_pixels=24,
        )


def test_sanitizer_rejects_animated_webp() -> None:
    first = Image.new("RGB", (2, 2), "red")
    second = Image.new("RGB", (2, 2), "blue")
    output = BytesIO()
    first.save(output, format="WEBP", save_all=True, append_images=[second], duration=100, loop=0)

    with pytest.raises(EvidenceSanitizationError, match="animation_unsupported"):
        sanitize_evidence_image(output.getvalue(), declared_media_type="image/webp")


@pytest.mark.parametrize(
    "arguments",
    [
        {"identity": "", "version": "1", "timeout_seconds": 1},
        {"identity": "scanner", "version": "", "timeout_seconds": 1},
        {"identity": "scanner", "version": "1", "timeout_seconds": 0},
    ],
)
def test_callback_scanner_rejects_incomplete_identity_and_timeout(
    arguments: dict[str, object],
) -> None:
    async def accept(_image: object) -> bool:
        return True

    with pytest.raises(ValueError):
        CallbackEvidenceScanner(callback=accept, **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "arguments",
    [
        {"endpoint": "http://scanner.example.test", "bearer_token": "secret"},
        {"endpoint": "https://scanner.example.test", "bearer_token": ""},
        {
            "endpoint": "https://scanner.example.test",
            "bearer_token": "secret",
            "timeout_seconds": 11,
        },
    ],
)
def test_http_scanner_rejects_unsafe_configuration(arguments: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        HttpEvidenceScanner(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "arguments",
    [
        {"max_bytes": 0},
        {"max_dimension": 0},
        {"max_pixels": 0},
    ],
)
def test_sanitizer_rejects_invalid_resource_bounds(arguments: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        sanitize_evidence_image(
            _image_bytes("PNG"),
            declared_media_type="image/png",
            **arguments,
        )


def test_sanitizer_rejects_empty_oversized_and_post_rewrite_oversized_images() -> None:
    with pytest.raises(EvidenceSanitizationError, match="decode_failed"):
        sanitize_evidence_image(b"", declared_media_type="image/png")
    payload = _image_bytes("PNG")
    with pytest.raises(EvidenceSanitizationError, match="decode_failed"):
        sanitize_evidence_image(
            payload,
            declared_media_type="image/png",
            max_bytes=len(payload) - 1,
        )
    compact = _image_bytes("WEBP")
    with pytest.raises(EvidenceSanitizationError, match="sanitized_size_exceeded"):
        sanitize_evidence_image(
            compact,
            declared_media_type="image/webp",
            max_bytes=len(compact),
        )


@pytest.mark.asyncio
async def test_callback_scanner_maps_detection_and_outage_to_bounded_codes() -> None:
    image = sanitize_evidence_image(_image_bytes("PNG"), declared_media_type="image/png")

    async def detected(_image: object) -> bool:
        return False

    scanner = CallbackEvidenceScanner(
        identity="test.scanner",
        version="1",
        callback=detected,
    )
    with pytest.raises(EvidenceSanitizationError, match="malware_detected"):
        await scanner.scan(image)

    async def slow(_image: object) -> bool:
        await asyncio.sleep(0.05)
        return True

    scanner = CallbackEvidenceScanner(
        identity="test.scanner",
        version="1",
        callback=slow,
        timeout_seconds=0.001,
    )
    with pytest.raises(EvidenceSanitizationError, match="scanner_unavailable"):
        await scanner.scan(image)


@pytest.mark.asyncio
async def test_callback_scanner_preserves_cancellation() -> None:
    image = sanitize_evidence_image(_image_bytes("PNG"), declared_media_type="image/png")

    async def cancelled(_image: object) -> bool:
        raise asyncio.CancelledError

    scanner = CallbackEvidenceScanner(
        identity="test.scanner",
        version="1",
        callback=cancelled,
    )
    with pytest.raises(asyncio.CancelledError):
        await scanner.scan(image)


@pytest.mark.asyncio
async def test_http_scanner_sends_only_rewritten_bytes_and_safe_metadata() -> None:
    image = sanitize_evidence_image(_image_bytes("PNG"), declared_media_type="image/png")

    def accept(request: httpx.Request) -> httpx.Response:
        assert request.content == image.payload
        assert request.headers["authorization"] == "Bearer scanner-secret"
        assert request.headers["content-type"] == "image/png"
        assert request.headers["x-content-sha256"] == image.content_digest
        assert request.headers["x-image-width"] == str(image.width)
        assert request.headers["x-image-height"] == str(image.height)
        assert "filename" not in request.headers
        return httpx.Response(200, json={"accepted": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(accept)) as client:
        scanner = HttpEvidenceScanner(
            endpoint="https://scanner.example.test/v1/scan",
            bearer_token="scanner-secret",
            client=client,
        )
        await scanner.scan(image)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(200, json={"accepted": False}), "malware_detected"),
        (httpx.Response(503), "scanner_unavailable"),
        (httpx.Response(200, content=b"x" * 4097), "scanner_unavailable"),
        (
            httpx.Response(200, content=b'{"accepted":"yes"}'),
            "scanner_unavailable",
        ),
        (
            httpx.Response(
                200,
                content=b'{"accepted":true}',
                headers={"content-length": "4097"},
            ),
            "scanner_unavailable",
        ),
    ],
)
async def test_http_scanner_fails_closed_on_detection_outage_and_oversized_response(
    response: httpx.Response,
    code: str,
) -> None:
    image = sanitize_evidence_image(_image_bytes("PNG"), declared_media_type="image/png")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response)
    ) as client:
        scanner = HttpEvidenceScanner(
            endpoint="https://scanner.example.test/v1/scan",
            bearer_token="scanner-secret",
            client=client,
        )
        with pytest.raises(EvidenceSanitizationError, match=code):
            await scanner.scan(image)


@pytest.mark.asyncio
async def test_http_scanner_bounds_chunked_response_and_preserves_cancellation() -> None:
    image = sanitize_evidence_image(_image_bytes("PNG"), declared_media_type="image/png")

    class Chunked(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            yield b"x" * 3000
            yield b"x" * 1097

    def chunked(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Chunked())

    async with httpx.AsyncClient(transport=httpx.MockTransport(chunked)) as client:
        with pytest.raises(EvidenceSanitizationError, match="scanner_unavailable"):
            await HttpEvidenceScanner(
                endpoint="https://scanner.example.test/v1/scan",
                bearer_token="scanner-secret",
                client=client,
            ).scan(image)

    async def cancelled(_request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(cancelled)) as client:
        with pytest.raises(asyncio.CancelledError):
            await HttpEvidenceScanner(
                endpoint="https://scanner.example.test/v1/scan",
                bearer_token="scanner-secret",
                client=client,
            ).scan(image)


@pytest.mark.asyncio
async def test_http_scanner_maps_invalid_json_and_closes_owned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = sanitize_evidence_image(_image_bytes("PNG"), declared_media_type="image/png")
    closed: list[bool] = []

    class ResponseContext:
        async def __aenter__(self) -> httpx.Response:
            return httpx.Response(200, content=b"{")

        async def __aexit__(self, *args: object) -> None:
            del args

    class OwnedClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def stream(self, *args: object, **kwargs: object) -> ResponseContext:
            del args, kwargs
            return ResponseContext()

        async def aclose(self) -> None:
            closed.append(True)

    monkeypatch.setattr(httpx, "AsyncClient", OwnedClient)
    with pytest.raises(EvidenceSanitizationError, match="scanner_unavailable"):
        await HttpEvidenceScanner(
            endpoint="https://scanner.example.test/v1/scan",
            bearer_token="scanner-secret",
        ).scan(image)
    assert closed == [True]


@pytest.mark.parametrize(
    "error",
    [Image.DecompressionBombError("bomb"), Image.DecompressionBombWarning("warning")],
)
def test_sanitizer_maps_pillow_decompression_limits(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    monkeypatch.setattr(Image, "open", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(EvidenceSanitizationError, match="pixel_limit_exceeded"):
        sanitize_evidence_image(_image_bytes("PNG"), declared_media_type="image/png")


def test_sanitizer_fails_if_rewritten_output_cannot_be_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = Image.open
    calls = 0

    class WrongFormat:
        format = "JPEG"
        n_frames = 1

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args: object) -> None:
            del args

    def open_with_bad_verification(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 3:
            return WrongFormat()
        return real_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", open_with_bad_verification)
    with pytest.raises(EvidenceSanitizationError, match="metadata_rewrite_failed"):
        sanitize_evidence_image(_image_bytes("PNG"), declared_media_type="image/png")


def test_sanitizer_fails_if_rewritten_output_retains_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = Image.open
    calls = 0

    class MetadataOutput:
        info = {"private": "retained"}
        size = (4, 3)

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def load(self) -> None:
            return None

    def open_with_metadata(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 4:
            return MetadataOutput()
        return real_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", open_with_metadata)
    with pytest.raises(EvidenceSanitizationError, match="metadata_rewrite_failed"):
        sanitize_evidence_image(_image_bytes("PNG"), declared_media_type="image/png")


def test_sanitization_helpers_fail_closed_for_unknown_or_mismatched_formats() -> None:
    assert _has_exact_container_boundary(b"unknown", "application/octet-stream") is False
    image = Image.new("RGB", (1, 1))
    image.format = "JPEG"
    with pytest.raises(EvidenceSanitizationError, match="signature_mismatch"):
        _require_expected_format(image, "image/png")
