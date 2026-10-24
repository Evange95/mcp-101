"""Probes the outbound adapters announce through, so they never log or meter directly.

`resource` is the low-cardinality kind of data involved ("pokemon", "species", ...).
"""

from typing import TYPE_CHECKING, Protocol

from pokedex_mcp.domain.errors import PokedexUnavailable


class CacheProbe(Protocol):
    def hit(self, resource: str) -> None: ...

    def miss(self, resource: str, *, expired: bool) -> None: ...

    def evicted(self, resource: str) -> None: ...


class RetryProbe(Protocol):
    def retry_scheduled(
        self, *, attempt: int, error: PokedexUnavailable, wait_seconds: float
    ) -> None: ...


class PokeApiProbe(Protocol):
    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None: ...


class NullCacheProbe:
    def hit(self, resource: str) -> None:
        pass

    def miss(self, resource: str, *, expired: bool) -> None:
        pass

    def evicted(self, resource: str) -> None:
        pass


class NullRetryProbe:
    def retry_scheduled(
        self, *, attempt: int, error: PokedexUnavailable, wait_seconds: float
    ) -> None:
        pass


class NullPokeApiProbe:
    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        pass


if TYPE_CHECKING:
    _cache: type[CacheProbe] = NullCacheProbe
    _retry: type[RetryProbe] = NullRetryProbe
    _pokeapi: type[PokeApiProbe] = NullPokeApiProbe
