from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from opennosh_api.public.r2 import S3R2ObjectWriter
from opennosh_api.public.signing import (
    decode_public_key_text,
    load_production_signing_key,
    public_key_text,
)
from opennosh_api.publication.forge.github import (
    GitHubAppInstallationTokenProvider,
    GitHubForgeClient,
    GitHubGovernanceAttester,
)
from opennosh_api.publication.receipts import (
    Ed25519ReceiptSigner,
    PublicationReceiptKeyRing,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from opennosh_api.settings import Settings


@dataclass(frozen=True, slots=True)
class GitHubAppIdentity:
    app_id: int
    installation_id: int
    repository_id: int
    public_key_fingerprint: str


@dataclass(frozen=True, slots=True)
class PublicationCredentialIdentity:
    forge: GitHubAppIdentity
    attester: GitHubAppIdentity
    manifest_key_id: str
    manifest_public_key: str
    receipt_key_id: str
    receipt_public_key: str
    artifact_bucket: str


@dataclass(frozen=True, slots=True)
class ProductionPublicationClients:
    """Secret-bearing clients built before the publication database is opened."""

    identity: PublicationCredentialIdentity
    forge_tokens: GitHubAppInstallationTokenProvider
    attester_tokens: GitHubAppInstallationTokenProvider
    forge: GitHubForgeClient
    attester: GitHubGovernanceAttester
    manifest_signing_key: Ed25519PrivateKey
    receipt_signer: Ed25519ReceiptSigner
    receipt_key_ring: PublicationReceiptKeyRing
    r2_writer: S3R2ObjectWriter

    @classmethod
    def from_settings(cls, settings: Settings) -> ProductionPublicationClients:
        identity = validate_publication_claim_credentials(settings)
        assert settings.github_forge_private_key is not None
        assert settings.github_attester_private_key is not None
        assert settings.online_manifest_signing_key is not None
        assert settings.online_manifest_signing_key_id is not None
        assert settings.online_receipt_signing_key is not None
        assert settings.online_receipt_signing_key_id is not None
        assert settings.r2_account_id is not None
        assert settings.r2_access_key_id is not None
        assert settings.r2_secret_access_key is not None

        forge_tokens = GitHubAppInstallationTokenProvider(
            app_id=identity.forge.app_id,
            installation_id=identity.forge.installation_id,
            repository_id=identity.forge.repository_id,
            private_key_pem=settings.github_forge_private_key.get_secret_value(),
        )
        attester_tokens = GitHubAppInstallationTokenProvider(
            app_id=identity.attester.app_id,
            installation_id=identity.attester.installation_id,
            repository_id=identity.attester.repository_id,
            private_key_pem=settings.github_attester_private_key.get_secret_value(),
        )
        manifest_key = load_production_signing_key(
            settings.online_manifest_signing_key,
            key_id=settings.online_manifest_signing_key_id,
        )
        receipt_key = load_production_signing_key(
            settings.online_receipt_signing_key,
            key_id=settings.online_receipt_signing_key_id,
        )
        return cls(
            identity=identity,
            forge_tokens=forge_tokens,
            attester_tokens=attester_tokens,
            forge=GitHubForgeClient(forge_tokens),
            attester=GitHubGovernanceAttester(attester_tokens),
            manifest_signing_key=manifest_key,
            receipt_signer=Ed25519ReceiptSigner(
                key_id=settings.online_receipt_signing_key_id,
                publisher_identity="opennosh:production-publication",
                private_key=receipt_key,
                adapter_identity="opennosh.production.receipt-signer",
            ),
            receipt_key_ring=PublicationReceiptKeyRing.from_json(
                settings.publication_receipt_verifying_keys.get_secret_value()
            ),
            r2_writer=S3R2ObjectWriter(
                account_id=settings.r2_account_id,
                access_key_id=settings.r2_access_key_id.get_secret_value(),
                secret_access_key=settings.r2_secret_access_key.get_secret_value(),
            ),
        )

    async def aclose(self) -> None:
        await asyncio.gather(
            self.forge.aclose(),
            self.attester.aclose(),
            self.forge_tokens.aclose(),
            self.attester_tokens.aclose(),
            self.r2_writer.aclose(),
        )


def validate_publication_claim_credentials(
    settings: Settings,
) -> PublicationCredentialIdentity:
    """Validate claims-time identities without retaining or returning private material."""

    required = {
        "GITHUB_REPOSITORY_ID": settings.github_repository_id,
        "GITHUB_FORGE_APP_ID": settings.github_forge_app_id,
        "GITHUB_FORGE_INSTALLATION_ID": settings.github_forge_installation_id,
        "GITHUB_FORGE_PRIVATE_KEY": settings.github_forge_private_key,
        "GITHUB_ATTESTER_APP_ID": settings.github_attester_app_id,
        "GITHUB_ATTESTER_INSTALLATION_ID": settings.github_attester_installation_id,
        "GITHUB_ATTESTER_PRIVATE_KEY": settings.github_attester_private_key,
        "ONLINE_RECEIPT_SIGNING_KEY_ID": settings.online_receipt_signing_key_id,
        "ONLINE_RECEIPT_SIGNING_KEY": settings.online_receipt_signing_key,
        "PUBLICATION_ARTIFACT_BUCKET": settings.publication_artifact_bucket,
    }
    missing = sorted(name for name, value in required.items() if value is None)
    if missing:
        raise ValueError(
            "Publication claims configuration is incomplete: " + ",".join(missing)
        )
    assert settings.github_repository_id is not None
    assert settings.github_forge_app_id is not None
    assert settings.github_forge_installation_id is not None
    assert settings.github_forge_private_key is not None
    assert settings.github_attester_app_id is not None
    assert settings.github_attester_installation_id is not None
    assert settings.github_attester_private_key is not None
    assert settings.online_receipt_signing_key_id is not None
    assert settings.online_receipt_signing_key is not None
    assert settings.publication_artifact_bucket is not None
    assert settings.online_manifest_signing_key_id is not None
    assert settings.online_manifest_signing_key is not None

    forge = GitHubAppIdentity(
        app_id=settings.github_forge_app_id,
        installation_id=settings.github_forge_installation_id,
        repository_id=settings.github_repository_id,
        public_key_fingerprint=_rsa_public_key_fingerprint(
            settings.github_forge_private_key.get_secret_value()
        ),
    )
    attester = GitHubAppIdentity(
        app_id=settings.github_attester_app_id,
        installation_id=settings.github_attester_installation_id,
        repository_id=settings.github_repository_id,
        public_key_fingerprint=_rsa_public_key_fingerprint(
            settings.github_attester_private_key.get_secret_value()
        ),
    )
    if (
        forge.app_id == attester.app_id
        or forge.installation_id == attester.installation_id
        or forge.public_key_fingerprint == attester.public_key_fingerprint
    ):
        raise ValueError("Forge and governance-attester identities must be independent")

    manifest_key = load_production_signing_key(
        settings.online_manifest_signing_key,
        key_id=settings.online_manifest_signing_key_id,
    )
    receipt_key = load_production_signing_key(
        settings.online_receipt_signing_key,
        key_id=settings.online_receipt_signing_key_id,
    )
    manifest_public = public_key_text(manifest_key)
    receipt_public = public_key_text(receipt_key)
    receipt_keys = json.loads(settings.publication_receipt_verifying_keys.get_secret_value())
    if not isinstance(receipt_keys, dict):
        raise ValueError("Publication receipt verifying keys must be an object")
    trusted_receipt = receipt_keys.get(settings.online_receipt_signing_key_id)
    if not isinstance(trusted_receipt, str) or (
        decode_public_key_text(trusted_receipt) != decode_public_key_text(receipt_public)
    ):
        raise ValueError("Online receipt signing key must be present in its verifying key ring")
    other_receipt_keys = tuple(
        decode_public_key_text(value)
        for key_id, value in receipt_keys.items()
        if key_id != settings.online_receipt_signing_key_id and isinstance(value, str)
    )
    receipt_material = decode_public_key_text(receipt_public)
    manifest_verifying_keys = tuple(
        decode_public_key_text(encoded)
        for _key_id, separator, encoded in (
            entry.partition(":") for entry in settings.public_commons_verifying_keys.split(",")
        )
        if separator
    )
    if (
        receipt_material == decode_public_key_text(manifest_public)
        or receipt_material in other_receipt_keys
        or receipt_material in manifest_verifying_keys
    ):
        raise ValueError(
            "Online receipt signing key must be independent from manifest and offline keys"
        )
    if settings.r2_bucket != settings.publication_artifact_bucket:
        raise ValueError("Publication artifact bucket must match the refresh R2 bucket")
    return PublicationCredentialIdentity(
        forge=forge,
        attester=attester,
        manifest_key_id=settings.online_manifest_signing_key_id,
        manifest_public_key=manifest_public,
        receipt_key_id=settings.online_receipt_signing_key_id,
        receipt_public_key=receipt_public,
        artifact_bucket=settings.publication_artifact_bucket,
    )


def _rsa_public_key_fingerprint(private_key_pem: str) -> str:
    try:
        private_key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"), password=None
        )
    except (TypeError, ValueError) as error:
        raise ValueError("GitHub App private key is invalid") from error
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError("GitHub App private key must be RSA")
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(public_der).hexdigest()
