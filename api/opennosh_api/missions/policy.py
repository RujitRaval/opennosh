from __future__ import annotations

from opennosh_api.missions.contracts import MissionLifecycleAction, MissionLifecycleState


class MissionLifecycleError(RuntimeError):
    """Fail-closed mission mutation error with a stable public-safe code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_TRANSITIONS: dict[
    tuple[MissionLifecycleState, MissionLifecycleAction], MissionLifecycleState
] = {
    (MissionLifecycleState.PROPOSED, MissionLifecycleAction.APPROVE): MissionLifecycleState.ACTIVE,
    (MissionLifecycleState.PROPOSED, MissionLifecycleAction.CLOSE): MissionLifecycleState.CLOSED,
    (MissionLifecycleState.ACTIVE, MissionLifecycleAction.PAUSE): MissionLifecycleState.PAUSED,
    (
        MissionLifecycleState.ACTIVE,
        MissionLifecycleAction.COMPLETE,
    ): MissionLifecycleState.COMPLETED,
    (MissionLifecycleState.ACTIVE, MissionLifecycleAction.CLOSE): MissionLifecycleState.CLOSED,
    (MissionLifecycleState.PAUSED, MissionLifecycleAction.RESUME): MissionLifecycleState.ACTIVE,
    (MissionLifecycleState.PAUSED, MissionLifecycleAction.CLOSE): MissionLifecycleState.CLOSED,
    (
        MissionLifecycleState.COMPLETED,
        MissionLifecycleAction.RELEASE,
    ): MissionLifecycleState.RELEASED,
    (MissionLifecycleState.COMPLETED, MissionLifecycleAction.CLOSE): MissionLifecycleState.CLOSED,
    (MissionLifecycleState.RELEASED, MissionLifecycleAction.CLOSE): MissionLifecycleState.CLOSED,
}


def state_after(
    state: MissionLifecycleState,
    action: MissionLifecycleAction,
) -> MissionLifecycleState:
    try:
        return _TRANSITIONS[(state, action)]
    except KeyError as error:
        raise MissionLifecycleError("mission_transition_not_allowed") from error


__all__ = ["MissionLifecycleError", "state_after"]
