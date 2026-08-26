from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, runtime_checkable

from opennosh_api.publication.state import (
    EffectIntent,
    ExternalObservation,
    ObservationStatus,
    PublicationStepName,
)


@runtime_checkable
class PublicationEffectAdapter(Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def version(self) -> str: ...

    async def apply(self, intent: EffectIntent) -> None: ...

    async def observe(self, intent: EffectIntent) -> ExternalObservation: ...


PublicationAdapterRegistry = Mapping[PublicationStepName, PublicationEffectAdapter]


class MissingPublicationAdapterError(RuntimeError):
    pass


class PublicationEffectError(RuntimeError):
    def __init__(
        self,
        *,
        status: ObservationStatus,
        code: str,
        retry_at: datetime | None = None,
        context: Mapping[str, object] | None = None,
    ) -> None:
        if status not in {
            ObservationStatus.RETRYABLE_FAILURE,
            ObservationStatus.CONFLICT,
            ObservationStatus.TERMINAL_FAILURE,
        }:
            raise ValueError("Effect errors require a failure observation status")
        super().__init__(code)
        self.status = status
        self.code = code
        self.retry_at = retry_at
        self.context = dict(context or {})
