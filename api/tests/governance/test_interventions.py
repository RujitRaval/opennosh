from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from opennosh_api.governance.contracts import (
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
    ApprovedFileChange,
)
from opennosh_api.governance.policy import GovernanceAuthorizationError, GovernanceBinding
from opennosh_api.governance.service import (
    grant_steward,
    recuse_steward,
)

NOW = datetime(2026, 8, 26, 12, tzinfo=UTC)
ACTOR = UUID("11111111-1111-4111-8111-111111111111")
ADMIN = UUID("22222222-2222-4222-8222-222222222222")
DRAFT = UUID("33333333-3333-4333-8333-333333333333")


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
