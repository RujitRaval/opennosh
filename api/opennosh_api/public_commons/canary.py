from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

import httpx
from pydantic import ValidationError

from opennosh_api.build_version import BuildVersionResponse
from opennosh_api.public_commons.schemas import CommonsSnapshotState

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CommonsCanaryObservation:
    status: Literal["pending", "healthy", "unavailable"]
    error_code: str | None
    build_version: str | None
    observed_commit: str | None
    commons_state: str | None


@dataclass(frozen=True, slots=True)
class CommonsCanaryAlert:
    error_code: str
    expected_commit: str
    observed_commit: str | None
    build_version: str | None
    observed_at: datetime


class CommonsCanaryAlertDestination(Protocol):
    async def send(self, alert: CommonsCanaryAlert) -> None: ...


class WebhookCommonsCanaryAlertDestination:
    """Send a redacted post-deploy Commons alert to Slack or another webhook."""

    def __init__(
        self,
        endpoint: str,
        *,
        bearer_token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._bearer_token = bearer_token
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=5.0)

    async def send(self, alert: CommonsCanaryAlert) -> None:
        short_commit = alert.expected_commit[:12]
        response = await self._client.post(
            self._endpoint,
            headers={
                "User-Agent": "OpenNosh Commons canary/1",
                **(
                    {"Authorization": f"Bearer {self._bearer_token}"}
                    if self._bearer_token
                    else {}
                ),
            },
            json={
                "text": (
                    "OpenNosh post-deploy Commons canary is unavailable for "
                    f"build `{short_commit}` (error: `{alert.error_code}`)."
                ),
                "schema": "opennosh.public-commons.post-deploy-canary.v1",
                "component": "public-commons",
                "state": "unavailable",
                "error_code": alert.error_code,
                "expected_commit": alert.expected_commit,
                "observed_commit": alert.observed_commit,
                "build_version": alert.build_version,
                "occurred_at": alert.observed_at.isoformat(),
            },
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


async def probe_post_deploy_commons(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    expected_commit: str,
) -> CommonsCanaryObservation:
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "OpenNosh Commons canary/1",
    }
    try:
        version_response = await client.get(
            f"{base_url}/api/v1/public/build-version",
            headers=headers,
        )
        version_response.raise_for_status()
    except httpx.HTTPError:
        return CommonsCanaryObservation(
            "pending", "build_version_unavailable", None, None, None
        )

    try:
        build = BuildVersionResponse.model_validate(version_response.json())
    except (ValueError, ValidationError):
        return CommonsCanaryObservation("pending", "build_version_invalid", None, None, None)
    if build.commit != expected_commit:
        return CommonsCanaryObservation(
            "pending",
            "build_commit_mismatch",
            build.version,
            build.commit,
            None,
        )

    try:
        commons_response = await client.get(
            f"{base_url}/api/v1/public/commons-snapshot",
            headers=headers,
        )
        commons_response.raise_for_status()
    except httpx.HTTPError:
        return CommonsCanaryObservation(
            "pending",
            "commons_endpoint_unavailable",
            build.version,
            build.commit,
            None,
        )
    commons_commit = commons_response.headers.get("X-OpenNosh-Build-Commit")
    if commons_commit != expected_commit:
        return CommonsCanaryObservation(
            "pending",
            "commons_build_commit_mismatch",
            build.version,
            commons_commit,
            None,
        )
    try:
        payload = commons_response.json()
        state = CommonsSnapshotState(payload["state"])
    except (KeyError, TypeError, ValueError):
        return CommonsCanaryObservation(
            "pending",
            "commons_response_invalid",
            build.version,
            build.commit,
            None,
        )

    if state in {CommonsSnapshotState.LIVE, CommonsSnapshotState.QUIET}:
        return CommonsCanaryObservation(
            "healthy", None, build.version, build.commit, state.value
        )
    if state is CommonsSnapshotState.UNAVAILABLE:
        return CommonsCanaryObservation(
            "unavailable",
            "commons_state_unavailable",
            build.version,
            build.commit,
            state.value,
        )
    return CommonsCanaryObservation(
        "pending",
        f"commons_state_{state.value}",
        build.version,
        build.commit,
        state.value,
    )


async def run_post_deploy_commons_canary(
    shutdown_requested: asyncio.Event,
    *,
    base_url: str,
    expected_commit: str,
    timeout_seconds: float,
    poll_seconds: float,
    alert_destination: CommonsCanaryAlertDestination,
    client: httpx.AsyncClient | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> CommonsCanaryObservation | None:
    """Wait for the deployed commit, then require Commons to become quiet or live."""

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=5.0, follow_redirects=False)
    deadline = monotonic() + timeout_seconds
    observation = CommonsCanaryObservation(
        "pending", "canary_not_started", None, None, None
    )
    try:
        while not shutdown_requested.is_set():
            observation = await probe_post_deploy_commons(
                http_client,
                base_url=base_url,
                expected_commit=expected_commit,
            )
            if observation.status == "healthy":
                logger.info(
                    "Public Commons post-deploy canary state=healthy commons_state=%s "
                    "build_version=%s commit=%s",
                    observation.commons_state,
                    observation.build_version,
                    expected_commit,
                )
                return observation
            if observation.status == "unavailable":
                await _deliver_canary_alert(
                    alert_destination,
                    CommonsCanaryAlert(
                        error_code=observation.error_code or "commons_state_unavailable",
                        expected_commit=expected_commit,
                        observed_commit=observation.observed_commit,
                        build_version=observation.build_version,
                        observed_at=datetime.now(UTC),
                    ),
                )
                logger.error(
                    "Public Commons post-deploy canary state=unavailable error=%s commit=%s",
                    observation.error_code,
                    expected_commit,
                )
                return observation
            remaining = deadline - monotonic()
            if remaining <= 0:
                await _deliver_canary_alert(
                    alert_destination,
                    CommonsCanaryAlert(
                        error_code=observation.error_code or "post_deploy_canary_timeout",
                        expected_commit=expected_commit,
                        observed_commit=observation.observed_commit,
                        build_version=observation.build_version,
                        observed_at=datetime.now(UTC),
                    ),
                )
                logger.error(
                    "Public Commons post-deploy canary state=unavailable error=%s commit=%s",
                    observation.error_code,
                    expected_commit,
                )
                return CommonsCanaryObservation(
                    "unavailable",
                    observation.error_code or "post_deploy_canary_timeout",
                    observation.build_version,
                    observation.observed_commit,
                    observation.commons_state,
                )
            try:
                await asyncio.wait_for(
                    shutdown_requested.wait(),
                    timeout=min(poll_seconds, remaining),
                )
            except TimeoutError:
                pass
        return None
    finally:
        if owns_client:
            await http_client.aclose()


async def _deliver_canary_alert(
    destination: CommonsCanaryAlertDestination,
    alert: CommonsCanaryAlert,
) -> None:
    try:
        await destination.send(alert)
    except Exception as error:
        logger.error(
            "Public Commons post-deploy external alert delivery failed error_type=%s",
            type(error).__name__,
        )
