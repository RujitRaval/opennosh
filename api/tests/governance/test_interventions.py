from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from opennosh_api.governance.contracts import (
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
    ApprovedFileChange,
    GovernanceDecisionOutcome,
)
from opennosh_api.governance.policy import GovernanceAuthorizationError, GovernanceBinding
from opennosh_api.governance.service import (
    GovernanceDecisionError,
    ResubmitPublication,
    grant_steward,
    intervene_publication,
    recuse_steward,
    resubmit_publication,
)

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
ACTOR = UUID("11111111-1111-4111-8111-111111111111")
ADMIN = UUID("22222222-2222-4222-8222-222222222222")
DRAFT = UUID("33333333-3333-4333-8333-333333333333")
PUBLICATION = UUID("44444444-4444-4444-8444-444444444444")
DECISION = UUID("55555555-5555-4555-8555-555555555555")


def test_steward_grant_preserves_scope_actor_time_and_reason() -> None:
    assignment = grant_steward(
        pack_id="global-core",
        actor_id=ACTOR,
        granted_by_actor_id=ADMIN,
        reason="Initial steward appointment",
        now=NOW,
    )

    assert assignment.pack_id == "global-core"
    assert assignment.actor_id == ACTOR
    assert assignment.granted_by_actor_id == ADMIN
    assert assignment.grant_reason == "Initial steward appointment"
    assert assignment.granted_at == NOW


def test_recusal_is_a_scoped_immutable_record() -> None:
    recusal = recuse_steward(
        pack_id="global-core",
        source_draft_id=DRAFT,
        actor_id=ACTOR,
        reason="Personal connection to contributor",
        now=NOW,
    )

    assert recusal.source_draft_id == DRAFT
    assert recusal.actor_id == ACTOR
    assert recusal.recused_at == NOW


def test_audited_intervention_invalidates_the_bound_authority() -> None:
    binding = GovernanceBinding(
        publication_id=UUID("44444444-4444-4444-8444-444444444444"),
        decision_id=UUID("55555555-5555-4555-8555-555555555555"),
        pack_id="global-core",
        contributor_actor_id=ADMIN,
        approving_actor_id=ACTOR,
        approved_at=NOW,
        approved_changes=ApprovedChangeSet.build(
            pack_id="global-core",
            files=(
                ApprovedFileChange(
                    path="packs/global-core/foods/lentils.json",
                    content='{"name":"Lentils"}\n',
                ),
            ),
        ),
        expected_base_commit="a" * 40,
        required_checks=PROTECTED_STATUS_CHECKS,
        forge_target="github:RujitRaval/opennosh",
        role_granted_at=NOW,
        intervention_action="changes_requested",
        intervened_at=NOW,
    )

    with pytest.raises(GovernanceAuthorizationError, match="publication_changes_requested"):
        binding.authorize_at(NOW)


@pytest.mark.parametrize(
    ("expected_base_commit", "reason"),
    [("not-a-hash", "retry"), ("b" * 40, " ")],
)
def test_resubmission_command_rejects_unbounded_operator_inputs(
    expected_base_commit: str,
    reason: str,
) -> None:
    with pytest.raises(ValueError):
        ResubmitPublication(
            prior_publication_intent_id=PUBLICATION,
            deciding_actor_id=ACTOR,
            expected_base_commit=expected_base_commit,
            reason=reason,
        )


@pytest.mark.parametrize(
    ("scalar_values", "get_value", "error_code"),
    [
        ([None], None, "publication_not_found"),
        (["global-core", None], None, "publication_not_found"),
        (
            ["global-core", SimpleNamespace(pack_id="other-pack")],
            None,
            "publication_governance_binding_mismatch",
        ),
        (
            [
                "global-core",
                SimpleNamespace(
                    id=PUBLICATION,
                    pack_id="global-core",
                    reviewed_decision_id=DECISION,
                ),
                SimpleNamespace(reviewed_decision_id=DECISION),
            ],
            None,
            "governance_decision_not_found",
        ),
    ],
)
def test_resubmission_fails_closed_on_missing_or_drifted_bindings(
    scalar_values: list[object],
    get_value: object,
    error_code: str,
) -> None:
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=scalar_values),
        execute=AsyncMock(),
        get=AsyncMock(return_value=get_value),
    )
    command = ResubmitPublication(
        prior_publication_intent_id=PUBLICATION,
        deciding_actor_id=ACTOR,
        expected_base_commit="b" * 40,
        reason="Retry reviewed material.",
    )

    with pytest.raises(GovernanceDecisionError, match=error_code):
        asyncio.run(resubmit_publication(session, object(), command, now=NOW))


@pytest.mark.parametrize(
    ("scalar_values", "error_code"),
    [
        ([None], "publication_not_found"),
        (
            ["global-core", SimpleNamespace(pack_id="other-pack")],
            "publication_governance_binding_mismatch",
        ),
        (
            [
                "global-core",
                SimpleNamespace(
                    id=PUBLICATION,
                    pack_id="global-core",
                    state="failed",
                    reviewed_decision_id=DECISION,
                ),
                None,
                None,
                SimpleNamespace(pack_id="other-pack"),
            ],
            "publication_governance_binding_mismatch",
        ),
    ],
)
def test_intervention_fails_closed_on_missing_or_drifted_bindings(
    scalar_values: list[object],
    error_code: str,
) -> None:
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=scalar_values),
        execute=AsyncMock(),
    )

    with pytest.raises(GovernanceDecisionError, match=error_code):
        asyncio.run(
            intervene_publication(
                session,
                PUBLICATION,
                actor_id=ACTOR,
                action=GovernanceDecisionOutcome.CHANGES_REQUESTED,
                reason="Fail closed.",
                now=NOW,
            )
        )
