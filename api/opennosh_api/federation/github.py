from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from opennosh_api.federation.contracts import FederationScope


class FederationProviderError(RuntimeError):
    def __init__(self, code: str, *, identity_mismatch: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.identity_mismatch = identity_mismatch


class GitHubInstallationVerifier:
    """Verify one App installation and repository using immutable GitHub IDs."""

    version = "2022-11-28"

    def __init__(
        self,
        *,
        app_id: int,
        private_key_pem: str,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if app_id <= 0:
            raise ValueError("Federation verifier App ID must be positive")
        try:
            key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
        except (TypeError, ValueError) as error:
            raise ValueError("Federation verifier App private key is invalid") from error
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("Federation verifier App private key must be RSA")
        self._app_id = app_id
        self._key = key
        self._clock = clock or (lambda: datetime.now(UTC))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": self.version,
                "User-Agent": "opennosh-federation-enrollment/1",
            },
            timeout=20,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def verify(self, scope: FederationScope, *, installation_id: int) -> None:
        if installation_id <= 0:
            raise FederationProviderError("github_installation_id_invalid", identity_mismatch=True)
        jwt = self._app_jwt()
        installation = await self._request(
            "GET",
            f"/app/installations/{installation_id}",
            authorization=f"Bearer {jwt}",
            expected={200},
        )
        if not isinstance(installation, dict) or installation.get("id") != installation_id:
            raise FederationProviderError("github_installation_response_invalid")
        token_payload = await self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            authorization=f"Bearer {jwt}",
            expected={201},
            json_body={"repository_ids": [scope.repository_id]},
        )
        token = token_payload.get("token") if isinstance(token_payload, dict) else None
        if not isinstance(token, str) or not token:
            raise FederationProviderError("github_installation_token_invalid")
        repository = await self._request(
            "GET",
            f"/repositories/{scope.repository_id}",
            authorization=f"Bearer {token}",
            expected={200},
        )
        owner = repository.get("owner") if isinstance(repository, dict) else None
        if (
            repository.get("id") != scope.repository_id
            or repository.get("full_name") != scope.repository
            or not isinstance(owner, dict)
        ):
            raise FederationProviderError(
                "github_repository_scope_mismatch", identity_mismatch=True
            )
        user = await self._request(
            "GET",
            f"/user/{scope.github_account_id}",
            authorization=f"Bearer {token}",
            expected={200},
        )
        login = user.get("login")
        if user.get("id") != scope.github_account_id or not isinstance(login, str):
            raise FederationProviderError("github_account_id_mismatch", identity_mismatch=True)
        if login.casefold() != scope.github_login.casefold():
            raise FederationProviderError("github_account_login_mismatch", identity_mismatch=True)
        repository_owner, repository_name = scope.repository.split("/", 1)
        permission = await self._request(
            "GET",
            (
                f"/repos/{repository_owner}/{repository_name}/collaborators/"
                f"{scope.github_login}/permission"
            ),
            authorization=f"Bearer {token}",
            expected={200},
        )
        if permission.get("permission") not in {"admin", "maintain", "write"}:
            raise FederationProviderError(
                "github_maintainer_repository_control_missing",
                identity_mismatch=True,
            )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        authorization: str,
        expected: set[int],
        json_body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            response = await self._client.request(
                method,
                path,
                headers={"Authorization": authorization},
                json=json_body,
            )
        except httpx.TransportError as error:
            raise FederationProviderError("github_provider_unavailable") from error
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise FederationProviderError("github_provider_unavailable")
        if response.status_code not in expected:
            mismatch = response.status_code in {401, 403, 404}
            raise FederationProviderError(
                f"github_provider_http_{response.status_code}",
                identity_mismatch=mismatch,
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise FederationProviderError("github_provider_response_invalid") from error
        if not isinstance(payload, dict):
            raise FederationProviderError("github_provider_response_invalid")
        return payload

    def _app_jwt(self) -> str:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Federation verifier clock must include a timezone")
        header = _encode_json({"alg": "RS256", "typ": "JWT"})
        claims = _encode_json(
            {
                "iat": int((now - timedelta(seconds=60)).timestamp()),
                "exp": int((now + timedelta(minutes=9)).timestamp()),
                "iss": str(self._app_id),
            }
        )
        material = f"{header}.{claims}".encode("ascii")
        signature = self._key.sign(material, padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{claims}.{_encode(signature)}"


def _encode_json(value: object) -> str:
    return _encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
