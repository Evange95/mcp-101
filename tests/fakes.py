"""In-memory test doubles for the PokedexRepository port and the probes."""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol

import pytest

from pokedex_mcp.application.probes import Lookup, LookupProbe
from pokedex_mcp.domain.errors import InvalidQuery, NotFound, PokedexUnavailable
from pokedex_mcp.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonSummary,
    PokemonType,
)
from tests.builders import make_evolution_chain, make_move, make_pokemon, make_species, make_type


class _Identified(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def name(self) -> str: ...


def _index[T: _Identified](items: Iterable[T]) -> dict[str, T]:
    index: dict[str, T] = {}
    for item in items:
        index[item.name] = item
        index[str(item.id)] = item
    return index


def _lookup[T](index: dict[str, T], resource: str, key: str) -> T:
    try:
        return index[key]
    except KeyError:
        raise NotFound(resource, key) from None


class FakePokedexRepository:
    def __init__(
        self,
        *,
        pokemon: Iterable[Pokemon] = (),
        species: Iterable[PokemonSpecies] = (),
        types: Iterable[PokemonType] = (),
        chains: Iterable[EvolutionChain] = (),
        moves: Iterable[Move] = (),
    ) -> None:
        self._pokemon = _index(pokemon)
        self._species = _index(species)
        self._types = _index(types)
        self._chains = {str(chain.id): chain for chain in chains}
        self._moves = _index(moves)
        self._errors: list[Exception] = []
        self.calls: list[str] = []

    def fail_next(self, *errors: Exception) -> None:
        self._errors.extend(errors)

    def _record(self, *parts: object) -> None:
        self.calls.append(":".join(str(part) for part in parts))
        if self._errors:
            raise self._errors.pop(0)

    async def get_pokemon(self, key: str) -> Pokemon:
        self._record("get_pokemon", key)
        return _lookup(self._pokemon, "Pokémon", key)

    async def get_species(self, key: str) -> PokemonSpecies:
        self._record("get_species", key)
        return _lookup(self._species, "Species", key)

    async def list_pokemon(self, limit: int, offset: int) -> PokemonPage:
        self._record("list_pokemon", limit, offset)
        ordered = sorted({p.id: p for p in self._pokemon.values()}.values(), key=lambda p: p.id)
        items = tuple(
            PokemonSummary(id=p.id, name=p.name) for p in ordered[offset : offset + limit]
        )
        return PokemonPage(total=len(ordered), limit=limit, offset=offset, items=items)

    async def get_type(self, key: str) -> PokemonType:
        self._record("get_type", key)
        return _lookup(self._types, "Type", key)

    async def get_evolution_chain(self, chain_id: int) -> EvolutionChain:
        self._record("get_evolution_chain", chain_id)
        return _lookup(self._chains, "Evolution chain", str(chain_id))

    async def get_move(self, key: str) -> Move:
        self._record("get_move", key)
        return _lookup(self._moves, "Move", key)


class _RecordingLookupProbe:
    def __init__(self, events: list[str], lookup: Lookup) -> None:
        self._events = events
        self._lookup = lookup

    def query_normalized(self, key: str) -> None:
        self._events.append(f"{self._lookup}: normalized {key}")

    def found(self) -> None:
        self._events.append(f"{self._lookup}: found")

    def not_found(self, error: NotFound) -> None:
        self._events.append(f"{self._lookup}: not found {error.resource}")

    def query_rejected(self, error: InvalidQuery) -> None:
        self._events.append(f"{self._lookup}: rejected")

    def source_unavailable(self, error: PokedexUnavailable) -> None:
        self._events.append(f"{self._lookup}: unavailable")


class RecordingPokedexProbe:
    """Records lookup announcements as readable lines, in order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    @contextmanager
    def lookup(self, lookup: Lookup, query: str) -> Generator[LookupProbe]:
        self.events.append(f"{lookup}: started {query}")
        try:
            yield _RecordingLookupProbe(self.events, lookup)
        finally:
            self.events.append(f"{lookup}: ended")


class RecordingAdapterProbe:
    """Records cache, retry and PokeAPI announcements (one double for all three probes)."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def hit(self, resource: str) -> None:
        self.events.append(f"hit {resource}")

    def miss(self, resource: str, *, expired: bool) -> None:
        self.events.append(f"{'expired' if expired else 'miss'} {resource}")

    def evicted(self, resource: str) -> None:
        self.events.append(f"evicted {resource}")

    def retry_scheduled(self, *, attempt: int, reason: str, wait_seconds: float) -> None:
        self.events.append(f"retry after attempt {attempt}: {reason}")

    def unexpected_payload(self, resource: str, path: str, error: Exception) -> None:
        self.events.append(f"unexpected payload {resource} {path}")


def full_repository() -> FakePokedexRepository:
    return FakePokedexRepository(
        pokemon=[make_pokemon("bulbasaur", 1), make_pokemon("pikachu", 25)],
        species=[make_species("pikachu", 25), make_species("tauros", 128, evolution_chain_id=None)],
        types=[make_type()],
        chains=[make_evolution_chain(10)],
        moves=[make_move()],
    )


# Each param is a Callable[[PokedexRepository], Awaitable[object]].
REPOSITORY_OPERATIONS: list[object] = [
    pytest.param(lambda r: r.get_pokemon("pikachu"), id="get_pokemon"),
    pytest.param(lambda r: r.get_species("pikachu"), id="get_species"),
    pytest.param(lambda r: r.list_pokemon(20, 0), id="list_pokemon"),
    pytest.param(lambda r: r.get_type("electric"), id="get_type"),
    pytest.param(lambda r: r.get_evolution_chain(10), id="get_evolution_chain"),
    pytest.param(lambda r: r.get_move("thunderbolt"), id="get_move"),
]


if TYPE_CHECKING:
    from pokedex_mcp.adapters.outbound.probes import PokeApiProbe
    from pokedex_mcp.application.ports import PokedexRepository
    from pokedex_mcp.application.probes import PokedexProbe
    from pokedex_mcp.platform.resilience.cache import CacheProbe
    from pokedex_mcp.platform.resilience.retry import RetryProbe

    _repository: type[PokedexRepository] = FakePokedexRepository
    _lookup_probe: type[LookupProbe] = _RecordingLookupProbe
    _pokedex: type[PokedexProbe] = RecordingPokedexProbe
    _cache: type[CacheProbe] = RecordingAdapterProbe
    _retry: type[RetryProbe] = RecordingAdapterProbe
    _pokeapi: type[PokeApiProbe] = RecordingAdapterProbe
