from __future__ import annotations

from dataclasses import dataclass

from opennosh_api.publication.orchestrator import PublicationFailpoint
from opennosh_api.publication.state import PublicationStepName, publication_protocol


@dataclass(frozen=True, slots=True)
class PublicationCrashScenario:
    step: PublicationStepName
    ordinal: int
    failpoint: PublicationFailpoint

    @property
    def id(self) -> str:
        return f"{self.step.value}:{self.failpoint.value}"


FINAL_ACCEPTANCE_FAILPOINTS = (
    PublicationFailpoint.BEFORE_REDUCER,
    PublicationFailpoint.AFTER_REDUCER,
)

REQUIRED_PUBLICATION_FAILPOINTS = (
    PublicationFailpoint.BEFORE_EFFECT,
    PublicationFailpoint.AFTER_EFFECT,
    PublicationFailpoint.BEFORE_VERIFICATION,
    PublicationFailpoint.AFTER_VERIFICATION,
    PublicationFailpoint.BEFORE_REDUCER,
    PublicationFailpoint.AFTER_REDUCER,
)


def publication_crash_scenarios(forge_target: str) -> tuple[PublicationCrashScenario, ...]:
    return tuple(
        PublicationCrashScenario(
            step=definition.name,
            ordinal=definition.ordinal,
            failpoint=failpoint,
        )
        for definition in publication_protocol(forge_target)
        for failpoint in REQUIRED_PUBLICATION_FAILPOINTS
    )


def assert_complete_scenario_coverage(
    scenarios: tuple[PublicationCrashScenario, ...], forge_target: str
) -> None:
    expected = {
        (definition.name, failpoint)
        for definition in publication_protocol(forge_target)
        for failpoint in REQUIRED_PUBLICATION_FAILPOINTS
    }
    actual = {(scenario.step, scenario.failpoint) for scenario in scenarios}
    if actual != expected or len(actual) != len(scenarios):
        missing = sorted(f"{step.value}:{failpoint.value}" for step, failpoint in expected - actual)
        duplicate_count = len(scenarios) - len(actual)
        raise AssertionError(
            f"Publication crash matrix is incomplete; missing={missing}, "
            f"duplicate_count={duplicate_count}"
        )
