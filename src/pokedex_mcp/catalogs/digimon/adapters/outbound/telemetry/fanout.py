"""Composite Digimon probes forwarding every announcement to several adapters (logs + OTel).

Cache and retry fan-outs are generic: `platform/telemetry/resilience.py`.
"""

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING

from pokedex_mcp.catalogs.digimon.adapters.outbound.probes import DigiApiProbe
from pokedex_mcp.catalogs.digimon.application.probes import DigimonProbe, Lookup, LookupProbe
from pokedex_mcp.catalogs.digimon.domain.errors import DigimonUnavailable, InvalidQuery, NotFound


class _FanOutLookupProbe:
    def __init__(self, probes: list[LookupProbe]) -> None:
        self._probes = probes

    def query_normalized(self, key: str) -> None:
        for probe in self._probes:
            probe.query_normalized(key)

    def query_resolved(self, key: str) -> None:
        for probe in self._probes:
            probe.query_resolved(key)

    def found(self) -> None:
        for probe in self._probes:
            probe.found()

    def not_found(self, error: NotFound) -> None:
        for probe in self._probes:
            probe.not_found(error)

    def query_rejected(self, error: InvalidQuery) -> None:
        for probe in self._probes:
            probe.query_rejected(error)

    def source_unavailable(self, error: DigimonUnavailable) -> None:
        for probe in self._probes:
            probe.source_unavailable(error)


class FanOutDigimonProbe:
    def __init__(self, *probes: DigimonProbe) -> None:
        self._probes = probes

    @contextmanager
    def lookup(self, lookup: Lookup, query: str) -> Generator[LookupProbe]:
        with ExitStack() as stack:
            yield _FanOutLookupProbe(
                [stack.enter_context(probe.lookup(lookup, query)) for probe in self._probes]
            )


class FanOutDigiApiProbe:
    def __init__(self, *probes: DigiApiProbe) -> None:
        self._probes = probes

    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        for probe in self._probes:
            probe.unexpected_payload(resource, path, error)


if TYPE_CHECKING:
    _lookup_probe: type[LookupProbe] = _FanOutLookupProbe
    _digimon: type[DigimonProbe] = FanOutDigimonProbe
    _digiapi: type[DigiApiProbe] = FanOutDigiApiProbe
