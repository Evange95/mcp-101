"""Decorator adding an in-memory TTL + LRU cache in front of any DigimonRepository."""

import time
from typing import TYPE_CHECKING

from pokedex_mcp.catalogs.digimon.application.ports import DigimonRepository
from pokedex_mcp.catalogs.digimon.domain.models import (
    Digimon,
    DigimonFilter,
    DigimonPage,
    DigimonSummary,
)
from pokedex_mcp.platform.resilience.cache import CacheProbe, Clock, TtlLruCache


class CachedDigimonRepository:
    """One line per port method: the cache key names the resource, `TtlLruCache` does the rest."""

    def __init__(
        self,
        inner: DigimonRepository,
        *,
        ttl_seconds: float,
        max_entries: int,
        clock: Clock = time.monotonic,
        probe: CacheProbe | None = None,
    ) -> None:
        self._inner = inner
        self._cache = TtlLruCache(
            ttl_seconds=ttl_seconds, max_entries=max_entries, clock=clock, probe=probe
        )

    async def get_digimon(self, key: str) -> Digimon:
        return await self._cache.get_or_load(("digimon", key), lambda: self._inner.get_digimon(key))

    async def digimon_index(self) -> tuple[DigimonSummary, ...]:
        return await self._cache.get_or_load(("index",), self._inner.digimon_index)

    async def list_digimon(self, page: int, page_size: int, where: DigimonFilter) -> DigimonPage:
        return await self._cache.get_or_load(
            ("list", page, page_size, where),
            lambda: self._inner.list_digimon(page, page_size, where),
        )


if TYPE_CHECKING:
    _: type[DigimonRepository] = CachedDigimonRepository
