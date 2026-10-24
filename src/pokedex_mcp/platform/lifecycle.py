"""Process lifecycle as seen by an orchestrator: starting → ready → draining → stopping."""

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol


class Phase(StrEnum):
    STARTING = "starting"  # lifespan startup has not completed yet
    READY = "ready"  # serving traffic
    DRAINING = "draining"  # SIGTERM received: still serving, but asking to be taken out of rotation
    STOPPING = "stopping"  # no new connections; finishing in-flight requests, closing resources


class LifecycleProbe(Protocol):
    def phase_changed(self, previous: Phase, current: Phase, *, reason: str) -> None: ...


class NullLifecycleProbe:
    def phase_changed(self, previous: Phase, current: Phase, *, reason: str) -> None:
        pass


class Lifecycle:
    """Moves forward only: a draining or stopping process never becomes ready again."""

    def __init__(self, probe: LifecycleProbe | None = None) -> None:
        self._phase = Phase.STARTING
        self._probe = probe or NullLifecycleProbe()

    @property
    def phase(self) -> Phase:
        return self._phase

    def ready(self) -> None:
        self._move(Phase.READY, reason="startup complete", allowed_from={Phase.STARTING})

    def drain(self, *, reason: str) -> None:
        self._move(Phase.DRAINING, reason=reason, allowed_from={Phase.STARTING, Phase.READY})

    def stop(self, *, reason: str) -> None:
        self._move(
            Phase.STOPPING,
            reason=reason,
            allowed_from={Phase.STARTING, Phase.READY, Phase.DRAINING},
        )

    def _move(self, phase: Phase, *, reason: str, allowed_from: set[Phase]) -> None:
        if self._phase not in allowed_from:
            return
        previous, self._phase = self._phase, phase
        self._probe.phase_changed(previous, phase, reason=reason)


if TYPE_CHECKING:
    _: type[LifecycleProbe] = NullLifecycleProbe
