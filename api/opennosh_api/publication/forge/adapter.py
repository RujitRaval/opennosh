from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from opennosh_api.governance.contracts import PROTECTED_STATUS_CHECKS
from opennosh_api.governance.gate import GovernanceGate
from opennosh_api.governance.policy import GovernanceAuthorizationError, GovernanceBinding
from opennosh_api.publication.adapters import PublicationEffectError
from opennosh_api.publication.forge.contracts import (
    ForgeCheckState,
    ForgeClient,
    ForgeConflictError,
    ForgeGovernanceAttester,
    ForgeMutation,
    ForgePullRequestState,
    ForgeRetryableError,
    ForgeTerminalError,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    ObservationStatus,
    PublicationStepName,
)


class GovernedForgeAdapter:
    """Turns one immutable steward decision into one protected forge merge."""

    identity = "opennosh-governed-forge"
    version = "1"

    def __init__(
        self,
        gate: GovernanceGate,
        client: ForgeClient,
        attester: ForgeGovernanceAttester,
        *,
        clock: Callable[[], datetime] | None = None,
        retry_delay: timedelta = timedelta(seconds=20),
    ) -> None:
        if retry_delay <= timedelta():
            raise ValueError("Forge retry delay must be positive")
        self._gate = gate
        self._client = client
        self._attester = attester
        self._clock = clock or (lambda: datetime.now(UTC))
        self._retry_delay = retry_delay

    async def apply(self, intent: EffectIntent) -> None:
        self._validate_intent(intent)
        try:
            binding = await self._binding(intent)
            binding.authorize_at(self._now())
            mutation = ForgeMutation(binding=binding, idempotency_key=intent.idempotency_key)
            forge = await self._client.observe(mutation)
            if forge.state is ForgePullRequestState.ABSENT:
                await self._client.ensure_protected_pull_request(mutation)
                return
            if forge.state is ForgePullRequestState.MERGED:
                return
            if forge.state is ForgePullRequestState.CLOSED:
                raise ForgeConflictError("protected_pull_request_closed")
            if forge.head_payload_digest != binding.approved_changes.digest:
                raise ForgeConflictError("open_payload_mismatch")
            checks = dict(forge.checks)
            failed = sorted(
                name
                for name in binding.required_checks
                if checks.get(name) is ForgeCheckState.FAILED
            )
            if failed:
                raise ForgeConflictError("required_check_failed")
            attestation_check = "OpenNosh governance attestation"
            other_checks = set(binding.required_checks) - {attestation_check}
            other_missing = sorted(other_checks - set(checks))
            other_pending = sorted(
                name for name in other_checks if checks.get(name) is ForgeCheckState.PENDING
            )
            if other_missing or other_pending:
                raise ForgeRetryableError("protected_merge_pending")
            if attestation_check not in checks:
                if not forge.auto_merge_enabled:
                    binding = await self._binding(intent)
                    binding.authorize_at(self._now())
                    await self._client.enable_protected_auto_merge(
                        ForgeMutation(binding=binding, idempotency_key=intent.idempotency_key),
                        expected_head_commit=forge.head_commit or "",
                    )
                binding = await self._gate.authorize_merge(
                    intent.publication_id,
                    head_commit=forge.head_commit or "",
                    expected_payload_digest=intent.approved_payload_digest,
                    now=self._now(),
                )
                self._validate_binding(intent, binding)
                await self._attester.attest(
                    ForgeMutation(
                        binding=binding,
                        idempotency_key=intent.idempotency_key,
                    ),
                    head_commit=forge.head_commit or "",
                )
                return
            if checks[attestation_check] is ForgeCheckState.PENDING:
                raise ForgeRetryableError("governance_attestation_pending")
            raise ForgeRetryableError("protected_auto_merge_pending")
        except GovernanceAuthorizationError as error:
            if error.code == "publication_paused":
                raise PublicationEffectError(
                    status=ObservationStatus.RETRYABLE_FAILURE,
                    code=error.code,
                    retry_at=self._now() + self._retry_delay,
                ) from error
            raise PublicationEffectError(
                status=ObservationStatus.CONFLICT,
                code=error.code,
            ) from error
        except (LookupError, ValueError) as error:
            raise PublicationEffectError(
                status=ObservationStatus.CONFLICT,
                code="governance_binding_invalid",
            ) from error
        except ForgeRetryableError as error:
            raise PublicationEffectError(
                status=ObservationStatus.RETRYABLE_FAILURE,
                code=error.code,
                retry_at=self._now() + self._retry_delay,
            ) from error
        except ForgeConflictError as error:
            raise PublicationEffectError(
                status=ObservationStatus.CONFLICT,
                code=error.code,
            ) from error
        except ForgeTerminalError as error:
            raise PublicationEffectError(
                status=ObservationStatus.TERMINAL_FAILURE,
                code=error.code,
            ) from error

    async def observe(self, intent: EffectIntent) -> ExternalObservation:
        self._validate_intent(intent)
        observed_at = self._now()
        try:
            binding = await self._binding(intent)
        except (LookupError, ValueError):
            return self._observation(
                intent,
                ObservationStatus.CONFLICT,
                observed_at,
                code="governance_binding_invalid",
            )
        mutation = ForgeMutation(binding=binding, idempotency_key=intent.idempotency_key)
        try:
            forge = await self._client.observe(mutation)
        except ForgeRetryableError as error:
            return self._observation(
                intent,
                ObservationStatus.RETRYABLE_FAILURE,
                observed_at,
                code=error.code,
                retry_at=observed_at + self._retry_delay,
            )
        except ForgeConflictError as error:
            return self._observation(
                intent, ObservationStatus.CONFLICT, observed_at, code=error.code
            )
        except ForgeTerminalError as error:
            return self._observation(
                intent, ObservationStatus.TERMINAL_FAILURE, observed_at, code=error.code
            )

        authorization_time = forge.merged_at or observed_at
        try:
            binding.authorize_at(authorization_time)
        except GovernanceAuthorizationError as error:
            if (
                error.code == "publication_paused"
                and forge.state is not ForgePullRequestState.MERGED
            ):
                return self._observation(
                    intent,
                    ObservationStatus.RETRYABLE_FAILURE,
                    observed_at,
                    code=error.code,
                    retry_at=observed_at + self._retry_delay,
                )
            return self._observation(
                intent, ObservationStatus.CONFLICT, observed_at, code=error.code
            )

        if forge.state is ForgePullRequestState.ABSENT:
            return self._observation(intent, ObservationStatus.ABSENT, observed_at)
        if forge.state is ForgePullRequestState.CLOSED:
            return self._observation(
                intent,
                ObservationStatus.CONFLICT,
                observed_at,
                code="protected_pull_request_closed",
            )

        checks = dict(forge.checks)
        if forge.state is ForgePullRequestState.OPEN and (
            forge.head_payload_digest != binding.approved_changes.digest
        ):
            return self._observation(
                intent,
                ObservationStatus.CONFLICT,
                observed_at,
                code="open_payload_mismatch",
                context={
                    "expected": binding.approved_changes.digest,
                    "observed": forge.head_payload_digest,
                },
            )
        missing = sorted(set(binding.required_checks) - set(checks))
        failed = sorted(
            name for name in binding.required_checks if checks.get(name) is ForgeCheckState.FAILED
        )
        pending = sorted(
            name for name in binding.required_checks if checks.get(name) is ForgeCheckState.PENDING
        )
        if failed:
            return self._observation(
                intent,
                ObservationStatus.CONFLICT,
                observed_at,
                code="required_check_failed",
                context={"checks": failed},
            )
        attestation_check = "OpenNosh governance attestation"
        if attestation_check not in PROTECTED_STATUS_CHECKS:
            raise RuntimeError("Governance attestation check is not protected")
        other_missing = [name for name in missing if name != attestation_check]
        other_pending = [name for name in pending if name != attestation_check]
        if other_missing or other_pending:
            return self._observation(
                intent,
                ObservationStatus.RETRYABLE_FAILURE,
                observed_at,
                code="protected_merge_pending",
                retry_at=observed_at + self._retry_delay,
                context={
                    "missing_checks": other_missing,
                    "pending_checks": other_pending,
                },
            )
        if forge.state is ForgePullRequestState.OPEN and attestation_check in missing:
            return self._observation(intent, ObservationStatus.ABSENT, observed_at)
        if missing or pending:
            return self._observation(
                intent,
                ObservationStatus.RETRYABLE_FAILURE,
                observed_at,
                code="protected_merge_pending",
                retry_at=observed_at + self._retry_delay,
                context={"missing_checks": missing, "pending_checks": pending},
            )

        if forge.state is ForgePullRequestState.OPEN:
            return self._observation(
                intent,
                ObservationStatus.RETRYABLE_FAILURE,
                observed_at,
                code="protected_auto_merge_pending",
                retry_at=observed_at + self._retry_delay,
            )

        if binding.merge_authorized_at is None:
            return self._observation(
                intent,
                ObservationStatus.CONFLICT,
                observed_at,
                code="merge_authorization_missing",
            )
        if forge.head_commit != binding.merge_authorized_head_commit:
            return self._observation(
                intent,
                ObservationStatus.CONFLICT,
                observed_at,
                code="merged_head_not_authorized",
                context={
                    "expected": binding.merge_authorized_head_commit,
                    "observed": forge.head_commit,
                },
            )
        if forge.merged_payload_digest != binding.approved_changes.digest:
            return self._observation(
                intent,
                ObservationStatus.CONFLICT,
                observed_at,
                code="merged_payload_mismatch",
                context={
                    "expected": binding.approved_changes.digest,
                    "observed": forge.merged_payload_digest,
                },
            )
        return self._observation(
            intent,
            ObservationStatus.VERIFIED,
            observed_at,
            content_digest=binding.approved_changes.digest,
            external_reference=forge.merged_commit or forge.external_reference,
            context={"pull_request": forge.external_reference or ""},
        )

    async def _binding(self, intent: EffectIntent):  # type: ignore[no-untyped-def]
        binding = await self._gate.binding_for(intent.publication_id)
        self._validate_binding(intent, binding)
        return binding

    @staticmethod
    def _validate_binding(intent: EffectIntent, binding: GovernanceBinding) -> None:
        if binding.approved_changes.digest != intent.approved_payload_digest:
            raise ValueError("Publication intent digest does not match governance decision")
        if (
            binding.forge_target != intent.forge_target
            or binding.forge_target != intent.destination
        ):
            raise ValueError("Publication forge target does not match governance decision")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Forge clock must include a timezone")
        return value

    @staticmethod
    def _validate_intent(intent: EffectIntent) -> None:
        if intent.step is not PublicationStepName.COMMIT_RECORD:
            raise ValueError("Governed forge adapter only supports commit_record")

    def _observation(
        self,
        intent: EffectIntent,
        status: ObservationStatus,
        observed_at: datetime,
        *,
        content_digest: str | None = None,
        external_reference: str | None = None,
        retry_at: datetime | None = None,
        code: str | None = None,
        context: dict[str, object] | None = None,
    ) -> ExternalObservation:
        return ExternalObservation(
            step=intent.step,
            status=status,
            observed_at=observed_at,
            destination=intent.destination,
            effect_idempotency_key=intent.idempotency_key,
            adapter_identity=self.identity,
            adapter_version=self.version,
            content_digest=content_digest,
            external_reference=external_reference,
            retry_at=retry_at,
            code=code,
            context=context or {},
        )
