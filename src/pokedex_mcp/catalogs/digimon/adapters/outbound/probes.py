"""Probes specific to the digi-api adapter. Cache and retry probes live with their generic
building blocks in `platform/resilience`."""

from typing import TYPE_CHECKING, Protocol


class DigiApiProbe(Protocol):
    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None: ...


class NullDigiApiProbe:
    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        pass


if TYPE_CHECKING:
    _: type[DigiApiProbe] = NullDigiApiProbe
