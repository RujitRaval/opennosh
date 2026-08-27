"""Secret-safe Ed25519 signing for production public artifact envelopes."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr

from opennosh_api.nonproduction_keys import NON_PRODUCTION_KEY_IDS, NON_PRODUCTION_PUBLIC_KEYS
from opennosh_api.public_commons.manifests import SignedEnvelope, canonical_json

_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def load_production_signing_key(
    encoded_secret: SecretStr,
    *,
    key_id: str,
) -> Ed25519PrivateKey:
    """Decode one production Ed25519 seed without exposing it in errors or reprs."""

    if not _KEY_ID.fullmatch(key_id) or key_id in NON_PRODUCTION_KEY_IDS:
        raise ValueError("Production signing key ID is invalid or reserved")
    encoded = encoded_secret.get_secret_value().strip()
    try:
        key_bytes = base64.b64decode(
            f"{encoded}{'=' * (-len(encoded) % 4)}",
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as error:
        raise ValueError("Private signing key is not valid base64url") from error
    if len(key_bytes) != 32:
        raise ValueError("Private signing key must contain exactly 32 bytes")
    key = Ed25519PrivateKey.from_private_bytes(key_bytes)
    if public_key_text(key) in NON_PRODUCTION_PUBLIC_KEYS:
        raise ValueError("Nonproduction signing keys cannot sign production artifacts")
    return key


def decode_public_key_text(encoded: str) -> bytes:
    """Decode one textual Ed25519 public key to comparable key material."""

    try:
        payload = base64.b64decode(
            f"{encoded}{'=' * (-len(encoded) % 4)}",
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as error:
        raise ValueError("Public signing key is not valid base64url") from error
    if len(payload) != 32:
        raise ValueError("Public signing key must contain exactly 32 bytes")
    return payload


def public_key_text(private_key: Ed25519PrivateKey) -> str:
    payload = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def sign_envelope(
    payload: Mapping[str, object],
    *,
    key_id: str,
    private_key: Ed25519PrivateKey,
) -> bytes:
    signature = base64.urlsafe_b64encode(private_key.sign(canonical_json(payload))).decode()
    envelope = SignedEnvelope(
        key_id=key_id,
        payload=dict(payload),
        signature=signature.rstrip("="),
    )
    return canonical_json(envelope.model_dump(mode="json"))
