from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opennosh_api.public.signing import (
    decode_public_key_text,
    load_production_signing_key,
    public_key_text,
)
from pydantic import SecretStr


def _encoded(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def test_production_signing_key_requires_exact_ed25519_seed_length() -> None:
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        load_production_signing_key(SecretStr(_encoded(b"short")), key_id="online-v1")


def test_production_signing_key_rejects_nonproduction_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = b"p" * 32
    key = Ed25519PrivateKey.from_private_bytes(seed)
    monkeypatch.setattr(
        "opennosh_api.public.signing.NON_PRODUCTION_PUBLIC_KEYS",
        frozenset({public_key_text(key)}),
    )

    with pytest.raises(ValueError, match="Nonproduction signing keys"):
        load_production_signing_key(SecretStr(_encoded(seed)), key_id="online-v1")


@pytest.mark.parametrize(
    ("encoded", "message"),
    [
        ("not+base64url", "not valid base64url"),
        (_encoded(b"short"), "exactly 32 bytes"),
    ],
)
def test_public_key_decoder_rejects_invalid_material(encoded: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        decode_public_key_text(encoded)
