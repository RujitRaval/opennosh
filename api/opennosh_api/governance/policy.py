from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from opennosh_api.governance.contracts import (
    CANONICAL_FORGE_TARGET,
    PROTECTED_STATUS_CHECKS,
    ApprovedChangeSet,
)


class GovernanceAuthorizationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class GovernanceBinding:
    publication_id: UUID
    decision_id: UUID
    pack_id: str
    contributor_actor_id: UUID
    approving_actor_id: UUID
    approved_at: datetime
    approved_changes: ApprovedChangeSet
    expected_base_commit: str
    required_checks: tuple[str, ...]
    forge_target: str
    role_granted_at: datetime
    role_revoked_at: datetime | None = None
    recused_at: datetime | None = None
    intervention_action: str | None = None
    intervened_at: datetime | None = None
    pause_intervals: tuple[tuple[datetime, datetime | None], ...] = ()
    merge_authorized_at: datetime | None = None
    merge_authorized_head_commit: str | None = None
    merge_authorized_payload_digest: str | None = None

    def __post_init__(self) -> None:
        for value in (self.approved_at, self.role_granted_at):
            _require_aware(value)
        if self.role_revoked_at is not None:
            _require_aware(self.role_revoked_at)
        if self.approved_changes.pack_id != self.pack_id:
            raise ValueError("Governance binding pack does not match its approved changes")
        if self.required_checks != PROTECTED_STATUS_CHECKS:
            raise ValueError("Governance binding must use the canonical protected checks")
        if self.forge_target != CANONICAL_FORGE_TARGET:
            raise ValueError("Governance binding must target the canonical repository")
        if (self.intervention_action is None) != (self.intervened_at is None):
            raise ValueError("Governance intervention action and time must be recorded together")
        if self.intervention_action not in {None, "changes_requested", "rejected"}:
            raise ValueError("Governance intervention action is unsupported")
        if self.intervened_at is not None:
            _require_aware(self.intervened_at)
        merge_evidence = (
            self.merge_authorized_at,
            self.merge_authorized_head_commit,
            self.merge_authorized_payload_digest,
        )
        if any(value is None for value in merge_evidence) != all(
            value is None for value in merge_evidence
        ):
            raise ValueError("Merge authorization evidence must be all present or all absent")
        if self.merge_authorized_at is not None:
            _require_aware(self.merge_authorized_at)
            if self.merge_authorized_payload_digest != self.approved_changes.digest:
                raise ValueError("Merge authorization digest must match the approved changes")
            assert self.merge_authorized_head_commit is not None
            if len(self.merge_authorized_head_commit) not in {40, 64} or any(
                character not in "0123456789abcdef"
                for character in self.merge_authorized_head_commit
            ):
                raise ValueError("Merge authorization head must be a lowercase Git hash")

    def authorize_at(self, when: datetime) -> None:
        _require_aware(when)
        if self.contributor_actor_id == self.approving_actor_id:
            raise GovernanceAuthorizationError("self_review_prohibited")
        if self.merge_authorized_at is not None and self.merge_authorized_at <= when:
            if self.role_granted_at > self.merge_authorized_at:
                raise GovernanceAuthorizationError("steward_role_not_active")
            return
        if self.role_granted_at > when:
            raise GovernanceAuthorizationError("steward_role_not_active")
        if self.role_revoked_at is not None and self.role_revoked_at <= when:
            raise GovernanceAuthorizationError("steward_role_revoked")
        if self.recused_at is not None and self.recused_at <= when:
            raise GovernanceAuthorizationError("steward_recused")
        if self.intervened_at is not None and self.intervened_at <= when:
            raise GovernanceAuthorizationError(f"publication_{self.intervention_action}")
        for paused_at, resumed_at in self.pause_intervals:
            _require_aware(paused_at)
            if resumed_at is not None:
                _require_aware(resumed_at)
            if paused_at <= when and (resumed_at is None or when < resumed_at):
                raise GovernanceAuthorizationError("publication_paused")


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Governance time must include a timezone")
