"""Decorator adding an in-memory TTL + LRU cache in front of any PokedexRepository."""

import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import cast

from pokedex_mcp.application.ports import PokedexRepository
from pokedex_mcp.domain.models import (
    EvolutionChain,
    Move,
    Pokemon,
    PokemonPage,
    PokemonSpecies,
    PokemonType,
)

type Clock = Callable[[], float]


class CachedPokedexRepository:
    def __init__(
        self,
        inner: PokedexRepository,
        *,
        ttl_seconds: float,
        max_entries: int,
        clock: Clock = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._inner = inner
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, object]] = OrderedDict()

    async def get_pokemon(self, key: str) -> Pokemon:
        return await self._cached(("pokemon", key), lambda: self._inner.get_pokemon(key))

    async def get_species(self, key: str) -> PokemonSpecies:
        return await self._cached(("species", key), lambda: self._inner.get_species(key))

    async def list_pokemon(self, limit: int, offset: int) -> PokemonPage:
        return await self._cached(
            ("list", limit, offset), lambda: self._inner.list_pokemon(limit, offset)
        )

    async def get_type(self, key: str) -> PokemonType:
        return await self._cached(("type", key), lambda: self._inner.get_type(key))

    async def get_evolution_chain(self, chain_id: int) -> EvolutionChain:
        return await self._cached(
            ("evolution-chain", chain_id), lambda: self._inner.get_evolution_chain(chain_id)
        )

    async def get_move(self, key: str) -> Move:
        return await self._cached(("move", key), lambda: self._inner.get_move(key))

    async def _cached[T](self, cache_key: Hashable, load: Callable[[], Awaitable[T]]) -> T:
        entry = self._entries.get(cache_key)
        if entry is not None:
            expires_at, value = entry
            if self._clock() < expires_at:
                self._entries.move_to_end(cache_key)
                return cast(T, value)
            del self._entries[cache_key]

        loaded = await load()
        self._entries[cache_key] = (self._clock() + self._ttl, loaded)
        self._entries.move_to_end(cache_key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return loaded
