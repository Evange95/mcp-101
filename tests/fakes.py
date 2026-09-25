"""In-memory test double for the PokedexRepository output port."""

from collections.abc import Iterable
from typing import Protocol

import pytest

from pokedex_mcp.domain.errors import NotFound
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
