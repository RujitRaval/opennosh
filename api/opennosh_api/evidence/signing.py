from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from opennosh_api.evidence.contracts import (
    EvidenceManifest,
    MaintainerAttestationManifest,
    VersionedPublicDatasetManifest,
)

_DOMAIN = b"opennosh:evidence-source-signature:1.0\0"


class EvidenceSignatureError(ValueError):
    pass


def signature_material(manifest: EvidenceManifest) -> bytes:
    if not isinstance(
        manifest, (VersionedPublicDatasetManifest, MaintainerAttestationManifest)
    ):
        raise TypeError("This evidence class does not carry a source signature")
    unsigned = manifest.model_dump(mode="json", exclude={"signature"})
    return _DOMAIN + json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


class EvidenceVerificationKeyRing:
    """Trusted source/authority keys, bound to the claimed principal and key ID."""

    def __init__(self, keys: Mapping[tuple[str, str], Ed25519PublicKey]) -> None:
        self._keys = dict(keys)

    @classmethod
    def empty(cls) -> EvidenceVerificationKeyRing:
        return cls({})

    @classmethod
    def from_config(cls, value: str) -> EvidenceVerificationKeyRing:
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Evidence verifying keys must be valid JSON") from error
        if not isinstance(raw, dict):
            raise ValueError("Evidence verifying keys must be a JSON object")
        keys: dict[tuple[str, str], Ed25519PublicKey] = {}
        for principal, entries in raw.items():
            if not isinstance(principal, str) or not principal or not isinstance(entries, dict):
                raise ValueError("Evidence verifying keys must map principals to key objects")
            for key_id, encoded in entries.items():
                if not isinstance(key_id, str) or not key_id or not isinstance(encoded, str):
                    raise ValueError("Evidence key IDs and public keys must be strings")
                keys[(principal, key_id)] = Ed25519PublicKey.from_public_bytes(
                    _decode(encoded, 32)
                )
        return cls(keys)

    def verify(self, manifest: EvidenceManifest) -> None:
        if isinstance(manifest, VersionedPublicDatasetManifest):
            principal = manifest.publisher
        elif isinstance(manifest, MaintainerAttestationManifest):
            principal = manifest.authority_id
        else:
            return
        key = self._keys.get((principal, manifest.signature_key_id))
        if key is None:
            raise EvidenceSignatureError("Evidence signature key is not trusted for this principal")
        try:
            key.verify(_decode(manifest.signature, 64), signature_material(manifest))
        except InvalidSignature as error:
            raise EvidenceSignatureError("Evidence source signature verification failed") from error


def _decode(value: str, expected_bytes: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(f"{value}{'=' * (-len(value) % 4)}")
    except (ValueError, binascii.Error) as error:
        raise ValueError("Evidence verifying key material is not valid base64url") from error
    if len(decoded) != expected_bytes:
        raise ValueError("Evidence verifying key material has an invalid decoded length")
    return decoded
