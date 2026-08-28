from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from opennosh_api.governance.contracts import (
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
    ApprovedFileChange,
)
from opennosh_api.governance.policy import GovernanceBinding
from opennosh_api.publication.adapters import PublicationEffectError
from opennosh_api.publication.forge.adapter import GovernedForgeAdapter
from opennosh_api.publication.forge.contracts import (
    ForgeCheckState,
    ForgeMutation,
    ForgeObservation,
    ForgePullRequestState,
    ForgeRetryableError,
)
from opennosh_api.publication.state import (
    EffectIntent,
    ObservationStatus,
    PublicationStepName,
)

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
CONTRIBUTOR = UUID("11111111-1111-4111-8111-111111111111")
STEWARD = UUID("22222222-2222-4222-8222-222222222222")
PUBLICATION = UUID("33333333-3333-4333-8333-333333333333")
DECISION = UUID("44444444-4444-4444-8444-444444444444")
TARGET = "github:RujitRaval/opennosh"
CHECKS = PROTECTED_STATUS_CHECKS


def approved_changes() -> ApprovedChangeSet:
    return ApprovedChangeSet.build(
        pack_id="global-core",
        files=(
            ApprovedFileChange(
                path="packs/global-core/foods/lentils.json",
                content='{"name":"Lentils"}\n',
            ),
        ),
    )


def binding(**changes: object) -> GovernanceBinding:
    base = GovernanceBinding(
        publication_id=PUBLICATION,
        decision_id=DECISION,
        pack_id="global-core",
        contributor_actor_id=CONTRIBUTOR,
        approving_actor_id=STEWARD,
        approved_at=NOW - timedelta(hours=1),
        approved_changes=approved_changes(),
        expected_base_commit="a" * 40,
        required_checks=CHECKS,
        forge_target=TARGET,
        role_granted_at=NOW - timedelta(days=30),
    )
    return replace(base, **changes)


def authorized_binding(**changes: object) -> GovernanceBinding:
    return binding(
        merge_authorized_at=NOW - timedelta(minutes=30),
        merge_authorized_head_commit="d" * 40,
        merge_authorized_payload_digest=approved_changes().digest,
        **changes,
    )


def intent() -> EffectIntent:
    return EffectIntent(
        publication_id=PUBLICATION,
        workflow_version="1.0",
        workflow_revision=0,
        step=PublicationStepName.COMMIT_RECORD,
        destination=TARGET,
        approved_payload_digest=approved_changes().digest,
        idempotency_key="b" * 64,
        forge_target=TARGET,
    )


class FakeGate:
    def __init__(self, value: GovernanceBinding) -> None:
        self.value = value

    async def binding_for(self, publication_id: UUID) -> GovernanceBinding:
        assert publication_id == PUBLICATION
        return self.value

    async def authorize_merge(
        self,
        publication_id: UUID,
        *,
        head_commit: str,
        expected_payload_digest: str,
        now: datetime,
    ) -> GovernanceBinding:
        value = await self.binding_for(publication_id)
        if value.merge_authorized_at is not None:
            assert value.merge_authorized_head_commit == head_commit
            return value
        value.authorize_at(now)
        assert value.approved_changes.digest == expected_payload_digest
        self.value = replace(
            value,
            merge_authorized_at=now,
            merge_authorized_head_commit=head_commit,
            merge_authorized_payload_digest=expected_payload_digest,
        )
        return self.value


class MissingGate:
    async def binding_for(self, publication_id: UUID) -> GovernanceBinding:
        raise LookupError(publication_id)

    async def authorize_merge(
        self,
        publication_id: UUID,
        *,
        head_commit: str,
        expected_payload_digest: str,
        now: datetime,
    ) -> GovernanceBinding:
        return await self.binding_for(publication_id)


class SequenceGate:
    def __init__(self, *values: GovernanceBinding) -> None:
        self.values = list(values)

    async def binding_for(self, publication_id: UUID) -> GovernanceBinding:
        assert publication_id == PUBLICATION
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]

    async def authorize_merge(
        self,
        publication_id: UUID,
        *,
        head_commit: str,
        expected_payload_digest: str,
        now: datetime,
    ) -> GovernanceBinding:
        value = await self.binding_for(publication_id)
        if value.merge_authorized_at is None:
            value.authorize_at(now)
            value = replace(
                value,
                merge_authorized_at=now,
                merge_authorized_head_commit=head_commit,
                merge_authorized_payload_digest=expected_payload_digest,
            )
        return value


class FakeForge:
    identity = "fake-forge"
    version = "1"

    def __init__(self, observation: ForgeObservation) -> None:
        self.observation = observation
        self.mutations: list[ForgeMutation] = []
        self.auto_merge_mutations: list[ForgeMutation] = []

    async def ensure_protected_pull_request(self, mutation: ForgeMutation) -> None:
        self.mutations.append(mutation)

    async def observe(self, mutation: ForgeMutation) -> ForgeObservation:
        self.mutations.append(mutation)
        return self.observation

    async def enable_protected_auto_merge(
        self, mutation: ForgeMutation, *, expected_head_commit: str
    ) -> None:
        assert expected_head_commit == "d" * 40
        self.auto_merge_mutations.append(mutation)


class FakeAttester:
    def __init__(self) -> None:
        self.attestations: list[tuple[ForgeMutation, str]] = []

    async def attest(self, mutation: ForgeMutation, *, head_commit: str) -> None:
        self.attestations.append((mutation, head_commit))


class BlockingAttester(FakeAttester):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def attest(self, mutation: ForgeMutation, *, head_commit: str) -> None:
        self.entered.set()
        await self.release.wait()
        await super().attest(mutation, head_commit=head_commit)


class LostResponseAttester(FakeAttester):
    async def attest(self, mutation: ForgeMutation, *, head_commit: str) -> None:
        await super().attest(mutation, head_commit=head_commit)
        raise ForgeRetryableError("github_attester_unavailable")


def governed_adapter(
    gate: object,
    forge: FakeForge,
    *,
    attester: FakeAttester | None = None,
    clock: datetime = NOW,
):  # type: ignore[no-untyped-def]
    return GovernedForgeAdapter(
        gate,  # type: ignore[arg-type]
        forge,
        attester or FakeAttester(),
        clock=lambda: clock,
    )


def merged(*, digest: str | None = None) -> ForgeObservation:
    return ForgeObservation(
        state=ForgePullRequestState.MERGED,
        checks=tuple((name, ForgeCheckState.PASSED) for name in CHECKS),
        external_reference="https://github.test/opennosh/pull/42",
        head_commit="d" * 40,
        merged_at=NOW,
        merged_commit="c" * 40,
        merged_tree_digest="e" * 64,
        merged_payload_digest=digest or approved_changes().digest,
    )


@pytest.mark.asyncio
async def test_verified_merge_requires_exact_approved_payload_and_all_checks() -> None:
    forge = FakeForge(merged())
    adapter = governed_adapter(FakeGate(authorized_binding()), forge)

    observed = await adapter.observe(intent())

    assert observed.status is ObservationStatus.VERIFIED
    assert observed.content_digest == approved_changes().digest
    assert observed.external_reference == "c" * 40
    assert observed.context["merged_tree_digest"] == "e" * 64


@pytest.mark.parametrize(
    ("binding_changes", "expected_code"),
    [
        ({"role_revoked_at": NOW - timedelta(minutes=1)}, "steward_role_revoked"),
        ({"recused_at": NOW - timedelta(minutes=1)}, "steward_recused"),
        ({"pause_intervals": ((NOW - timedelta(minutes=1), None),)}, "publication_paused"),
    ],
)
@pytest.mark.asyncio
async def test_merge_blocks_when_governance_authority_changed(
    binding_changes: dict[str, object], expected_code: str
) -> None:
    adapter = governed_adapter(FakeGate(binding(**binding_changes)), FakeForge(merged()))

    observed = await adapter.observe(intent())

    assert observed.status is ObservationStatus.CONFLICT
    assert observed.code == expected_code


@pytest.mark.asyncio
async def test_failed_required_check_blocks_merge() -> None:
    checks = dict(merged().checks)
    checks["API checks"] = ForgeCheckState.FAILED
    forge = FakeForge(replace(merged(), checks=tuple(sorted(checks.items()))))

    observed = await governed_adapter(FakeGate(binding()), forge).observe(intent())

    assert observed.status is ObservationStatus.CONFLICT
    assert observed.code == "required_check_failed"
    assert observed.context["checks"] == ["API checks"]


@pytest.mark.asyncio
async def test_merged_payload_mismatch_is_quarantined() -> None:
    observed = await governed_adapter(
        FakeGate(authorized_binding()), FakeForge(merged(digest="d" * 64))
    ).observe(intent())

    assert observed.status is ObservationStatus.CONFLICT
    assert observed.code == "merged_payload_mismatch"


@pytest.mark.asyncio
async def test_apply_rechecks_authority_before_any_forge_write() -> None:
    forge = FakeForge(ForgeObservation(state=ForgePullRequestState.ABSENT))
    adapter = governed_adapter(FakeGate(binding(role_revoked_at=NOW)), forge)

    with pytest.raises(PublicationEffectError) as captured:
        await adapter.apply(intent())

    assert str(captured.value) == "steward_role_revoked"
    assert forge.mutations == []


@pytest.mark.asyncio
async def test_open_pull_request_with_attestation_waits_for_auto_merge() -> None:
    forge = FakeForge(
        ForgeObservation(
            state=ForgePullRequestState.OPEN,
            checks=tuple((name, ForgeCheckState.PASSED) for name in CHECKS),
            head_commit="d" * 40,
            head_payload_digest=approved_changes().digest,
        )
    )

    adapter = governed_adapter(FakeGate(binding()), forge)
    observed = await adapter.observe(intent())

    assert observed.status is ObservationStatus.RETRYABLE_FAILURE
    assert observed.code == "protected_auto_merge_pending"
    assert forge.auto_merge_mutations == []


@pytest.mark.asyncio
async def test_missing_independent_attestation_is_emitted_before_merge() -> None:
    attester = FakeAttester()
    forge = FakeForge(
        ForgeObservation(
            state=ForgePullRequestState.OPEN,
            checks=tuple(
                (name, ForgeCheckState.PASSED)
                for name in CHECKS
                if name != "OpenNosh governance attestation"
            ),
            head_commit="d" * 40,
            head_payload_digest=approved_changes().digest,
        )
    )

    adapter = governed_adapter(FakeGate(binding()), forge, attester=attester)
    observed = await adapter.observe(intent())

    assert observed.status is ObservationStatus.ABSENT
    assert attester.attestations == []
    await adapter.apply(intent())
    assert [(item[0].binding.decision_id, item[1]) for item in attester.attestations] == [
        (DECISION, "d" * 40)
    ]
    assert len(forge.auto_merge_mutations) == 1


@pytest.mark.asyncio
async def test_emergency_pause_retries_and_can_resume_without_terminal_block() -> None:
    gate = FakeGate(binding(pause_intervals=((NOW, None),)))
    forge = FakeForge(
        ForgeObservation(
            state=ForgePullRequestState.OPEN,
            checks=tuple(
                (name, ForgeCheckState.PASSED)
                for name in CHECKS
                if name != "OpenNosh governance attestation"
            ),
            head_commit="d" * 40,
            head_payload_digest=approved_changes().digest,
        )
    )
    adapter = governed_adapter(gate, forge)

    paused = await adapter.observe(intent())
    gate.value = binding(pause_intervals=((NOW, NOW),))
    resumed = await adapter.observe(intent())
    await adapter.apply(intent())

    assert paused.status is ObservationStatus.RETRYABLE_FAILURE
    assert paused.code == "publication_paused"
    assert resumed.status is ObservationStatus.ABSENT
    assert len(forge.auto_merge_mutations) == 1


@pytest.mark.parametrize(
    ("binding_changes", "expected_code"),
    [
        ({"role_revoked_at": NOW}, "steward_role_revoked"),
        ({"recused_at": NOW}, "steward_recused"),
        ({"pause_intervals": ((NOW, None),)}, "publication_paused"),
        (
            {"intervention_action": "rejected", "intervened_at": NOW},
            "publication_rejected",
        ),
    ],
)
@pytest.mark.asyncio
async def test_authority_is_reloaded_immediately_before_protected_merge(
    binding_changes: dict[str, object], expected_code: str
) -> None:
    forge = FakeForge(
        ForgeObservation(
            state=ForgePullRequestState.OPEN,
            checks=tuple(
                (name, ForgeCheckState.PASSED)
                for name in CHECKS
                if name != "OpenNosh governance attestation"
            ),
            head_commit="d" * 40,
            head_payload_digest=approved_changes().digest,
        )
    )
    gate = SequenceGate(binding(), binding(**binding_changes))

    adapter = governed_adapter(gate, forge)
    with pytest.raises(PublicationEffectError) as captured:
        await adapter.apply(intent())

    expected_status = (
        ObservationStatus.RETRYABLE_FAILURE
        if expected_code == "publication_paused"
        else ObservationStatus.CONFLICT
    )
    assert captured.value.status is expected_status
    assert captured.value.code == expected_code
    assert forge.auto_merge_mutations == []


@pytest.mark.asyncio
async def test_authority_withdrawn_after_auto_merge_arm_never_receives_attestation() -> None:
    forge = FakeForge(
        ForgeObservation(
            state=ForgePullRequestState.OPEN,
            checks=tuple(
                (name, ForgeCheckState.PASSED)
                for name in CHECKS
                if name != "OpenNosh governance attestation"
            ),
            head_commit="d" * 40,
            head_payload_digest=approved_changes().digest,
        )
    )
    attester = FakeAttester()
    gate = SequenceGate(
        binding(),
        binding(),
        binding(role_revoked_at=NOW),
    )
    adapter = governed_adapter(gate, forge, attester=attester)

    with pytest.raises(PublicationEffectError) as captured:
        await adapter.apply(intent())

    assert captured.value.code == "steward_role_revoked"
    assert len(forge.auto_merge_mutations) == 1
    assert attester.attestations == []


@pytest.mark.asyncio
async def test_merge_authorization_is_committed_before_external_attestation() -> None:
    forge = FakeForge(
        ForgeObservation(
            state=ForgePullRequestState.OPEN,
            checks=tuple(
                (name, ForgeCheckState.PASSED)
                for name in CHECKS
                if name != "OpenNosh governance attestation"
            ),
            head_commit="d" * 40,
            head_payload_digest=approved_changes().digest,
            auto_merge_enabled=True,
        )
    )
    gate = FakeGate(binding())
    attester = BlockingAttester()
    adapter = governed_adapter(gate, forge, attester=attester)

    applying = asyncio.create_task(adapter.apply(intent()))
    await attester.entered.wait()

    assert gate.value.merge_authorized_at == NOW
    gate.value = replace(gate.value, role_revoked_at=NOW)
    assert attester.attestations == []

    attester.release.set()
    await applying

    assert len(attester.attestations) == 1
    assert gate.value.role_revoked_at == NOW
    assert forge.auto_merge_mutations == []


@pytest.mark.asyncio
async def test_retry_after_auto_merge_arm_does_not_arm_it_again() -> None:
    attester = FakeAttester()
    forge = FakeForge(
        ForgeObservation(
            state=ForgePullRequestState.OPEN,
            checks=tuple(
                (name, ForgeCheckState.PASSED)
                for name in CHECKS
                if name != "OpenNosh governance attestation"
            ),
            head_commit="d" * 40,
            head_payload_digest=approved_changes().digest,
            auto_merge_enabled=True,
        )
    )

    await governed_adapter(FakeGate(binding()), forge, attester=attester).apply(intent())

    assert forge.auto_merge_mutations == []
    assert len(attester.attestations) == 1


@pytest.mark.asyncio
async def test_lost_attestation_response_preserves_committed_merge_authority() -> None:
    forge = FakeForge(
        ForgeObservation(
            state=ForgePullRequestState.OPEN,
            checks=tuple(
                (name, ForgeCheckState.PASSED)
                for name in CHECKS
                if name != "OpenNosh governance attestation"
            ),
            head_commit="d" * 40,
            head_payload_digest=approved_changes().digest,
            auto_merge_enabled=True,
        )
    )
    gate = FakeGate(binding())

    with pytest.raises(PublicationEffectError) as captured:
        await governed_adapter(
            gate,
            forge,
            attester=LostResponseAttester(),
        ).apply(intent())

    assert captured.value.status is ObservationStatus.RETRYABLE_FAILURE
    assert captured.value.code == "github_attester_unavailable"
    assert gate.value.merge_authorized_at == NOW

    gate.value = replace(gate.value, role_revoked_at=NOW)
    recovered_attester = FakeAttester()
    await governed_adapter(gate, forge, attester=recovered_attester).apply(intent())

    assert len(recovered_attester.attestations) == 1
    assert gate.value.merge_authorized_at == NOW


@pytest.mark.asyncio
async def test_missing_governance_binding_is_a_durable_conflict() -> None:
    forge = FakeForge(ForgeObservation(state=ForgePullRequestState.ABSENT))
    adapter = governed_adapter(MissingGate(), forge)

    observed = await adapter.observe(intent())

    assert observed.status is ObservationStatus.CONFLICT
    assert observed.code == "governance_binding_invalid"
    assert forge.mutations == []
