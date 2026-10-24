"""Probes specific to the PokeAPI adapter. Cache and retry probes live with their generic
building blocks in `platform/resilience`."""

from typing import TYPE_CHECKING, Protocol


class PokeApiProbe(Protocol):
    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None: ...


class NullPokeApiProbe:
    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        pass


if TYPE_CHECKING:
    _: type[PokeApiProbe] = NullPokeApiProbe
