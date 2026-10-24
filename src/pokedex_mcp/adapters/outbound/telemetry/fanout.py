"""Composite probes forwarding every announcement to several adapters (e.g. logs + OTel)."""

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING

from pokedex_mcp.adapters.outbound.probes import CacheProbe, PokeApiProbe, RetryProbe
from pokedex_mcp.application.probes import Lookup, LookupProbe, PokedexProbe
from pokedex_mcp.domain.errors import InvalidQuery, NotFound, PokedexUnavailable


class _FanOutLookupProbe:
    def __init__(self, probes: list[LookupProbe]) -> None:
        self._probes = probes

    def query_normalized(self, key: str) -> None:
        for probe in self._probes:
            probe.query_normalized(key)

    def found(self) -> None:
        for probe in self._probes:
            probe.found()

    def not_found(self, error: NotFound) -> None:
        for probe in self._probes:
            probe.not_found(error)

    def query_rejected(self, error: InvalidQuery) -> None:
        for probe in self._probes:
            probe.query_rejected(error)

    def source_unavailable(self, error: PokedexUnavailable) -> None:
        for probe in self._probes:
            probe.source_unavailable(error)


class FanOutPokedexProbe:
    def __init__(self, *probes: PokedexProbe) -> None:
        self._probes = probes

    @contextmanager
    def lookup(self, lookup: Lookup, query: str) -> Generator[LookupProbe]:
        with ExitStack() as stack:
            yield _FanOutLookupProbe(
                [stack.enter_context(probe.lookup(lookup, query)) for probe in self._probes]
            )


class FanOutCacheProbe:
    def __init__(self, *probes: CacheProbe) -> None:
        self._probes = probes

    def hit(self, resource: str) -> None:
        for probe in self._probes:
            probe.hit(resource)

    def miss(self, resource: str, *, expired: bool) -> None:
        for probe in self._probes:
            probe.miss(resource, expired=expired)

    def evicted(self, resource: str) -> None:
        for probe in self._probes:
            probe.evicted(resource)


class FanOutRetryProbe:
    def __init__(self, *probes: RetryProbe) -> None:
        self._probes = probes

    def retry_scheduled(
        self, *, attempt: int, error: PokedexUnavailable, wait_seconds: float
    ) -> None:
        for probe in self._probes:
            probe.retry_scheduled(attempt=attempt, error=error, wait_seconds=wait_seconds)


class FanOutPokeApiProbe:
    def __init__(self, *probes: PokeApiProbe) -> None:
        self._probes = probes

    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        for probe in self._probes:
            probe.unexpected_payload(resource, path, error)


if TYPE_CHECKING:
    _lookup_probe: type[LookupProbe] = _FanOutLookupProbe
    _pokedex: type[PokedexProbe] = FanOutPokedexProbe
    _cache: type[CacheProbe] = FanOutCacheProbe
    _retry: type[RetryProbe] = FanOutRetryProbe
    _pokeapi: type[PokeApiProbe] = FanOutPokeApiProbe
