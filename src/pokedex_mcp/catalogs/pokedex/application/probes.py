"""Domain probes: the observability port of the Pokédex core.

Following domain-oriented observability, the core announces *what happened* in Pokédex
language and never talks to a tracing, metrics or logging library. Adapters decide how
each announcement becomes spans, metrics or log lines.
"""

from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Literal, Protocol

from pokedex_mcp.catalogs.pokedex.domain.errors import InvalidQuery, NotFound, PokedexUnavailable

type Lookup = Literal["pokemon", "species", "pokemon_list", "type", "evolution_chain", "move"]


class LookupProbe(Protocol):
    """Observes one lookup, from the caller's raw query to exactly one outcome."""

    def query_normalized(self, key: str) -> None: ...

    def found(self) -> None: ...

    def not_found(self, error: NotFound) -> None: ...

    def query_rejected(self, error: InvalidQuery) -> None: ...

    def source_unavailable(self, error: PokedexUnavailable) -> None: ...


class PokedexProbe(Protocol):
    def lookup(self, lookup: Lookup, query: str) -> AbstractContextManager[LookupProbe]:
        """Bracket a lookup: entered before validation, exited after the outcome is reported."""
        ...


class _NullLookupProbe:
    def query_normalized(self, key: str) -> None:
        pass

    def found(self) -> None:
        pass

    def not_found(self, error: NotFound) -> None:
        pass

    def query_rejected(self, error: InvalidQuery) -> None:
        pass

    def source_unavailable(self, error: PokedexUnavailable) -> None:
        pass


class NullPokedexProbe:
    """Observes nothing: the default when no observability adapter is wired."""

    @contextmanager
    def lookup(self, lookup: Lookup, query: str) -> Generator[LookupProbe]:
        yield _NullLookupProbe()


if TYPE_CHECKING:
    _lookup_probe: type[LookupProbe] = _NullLookupProbe
    _pokedex: type[PokedexProbe] = NullPokedexProbe
