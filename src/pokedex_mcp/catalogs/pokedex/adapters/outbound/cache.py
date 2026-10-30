"""Decorator adding an in-memory TTL + LRU cache in front of any PokedexRepository."""

import time
from typing import TYPE_CHECKING

from pokedex_mcp.catalogs.pokedex.application.ports import PokedexRepository
from pokedex_mcp.catalogs.pokedex.domain.errors import NotFound
from pokedex_mcp.catalogs.pokedex.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonSpecies,
    PokemonSummary,
    PokemonType,
)
from pokedex_mcp.platform.resilience.cache import CacheProbe, Clock, TtlLruCache


def _is_not_found(exc: BaseException) -> bool:
    return isinstance(exc, NotFound)


class CachedPokedexRepository:
    """One line per port method: the cache key names the resource, `TtlLruCache` does the rest."""

    def __init__(
        self,
        inner: PokedexRepository,
        *,
        ttl_seconds: float,
        max_entries: int,
        not_found_ttl_seconds: float = 0.0,
        clock: Clock = time.monotonic,
        probe: CacheProbe | None = None,
    ) -> None:
        self._inner = inner
        self._cache = TtlLruCache(
            ttl_seconds=ttl_seconds,
            max_entries=max_entries,
            remember_error=_is_not_found,
            error_ttl_seconds=not_found_ttl_seconds,
            clock=clock,
            probe=probe,
        )

    async def get_pokemon(self, key: str) -> Pokemon:
        return await self._cache.get_or_load(("pokemon", key), lambda: self._inner.get_pokemon(key))

    async def get_species(self, key: str) -> PokemonSpecies:
        return await self._cache.get_or_load(("species", key), lambda: self._inner.get_species(key))

    async def pokemon_index(self) -> tuple[PokemonSummary, ...]:
        return await self._cache.get_or_load(("index",), self._inner.pokemon_index)

    async def get_type(self, key: str) -> PokemonType:
        return await self._cache.get_or_load(("type", key), lambda: self._inner.get_type(key))

    async def get_evolution_chain(self, chain_id: int) -> EvolutionChain:
        return await self._cache.get_or_load(
            ("evolution-chain", chain_id), lambda: self._inner.get_evolution_chain(chain_id)
        )

    async def get_move(self, key: str) -> Move:
        return await self._cache.get_or_load(("move", key), lambda: self._inner.get_move(key))


if TYPE_CHECKING:
    _: type[PokedexRepository] = CachedPokedexRepository
