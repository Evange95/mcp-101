"""The Pokédex catalog as a CatalogModule: builds its adapters and exposes its tools."""

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

import httpx

from pokedex_mcp.catalogs.pokedex.adapters.inbound.mcp.tools import (
    INSTRUCTIONS,
    PREFIX,
    PokedexTools,
)
from pokedex_mcp.catalogs.pokedex.adapters.outbound.cache import CachedPokedexRepository
from pokedex_mcp.catalogs.pokedex.adapters.outbound.pokeapi.client import PokeApiRepository
from pokedex_mcp.catalogs.pokedex.adapters.outbound.retry import RetryingPokedexRepository
from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.probes import Probes, build_probes
from pokedex_mcp.catalogs.pokedex.application.ports import PokedexRepository
from pokedex_mcp.catalogs.pokedex.application.use_cases import PokedexService
from pokedex_mcp.catalogs.pokedex.settings import PokedexSettings
from pokedex_mcp.platform.catalog import Platform, Tool
from pokedex_mcp.platform.health import ReadinessCheck
from pokedex_mcp.platform.settings import UpstreamSettings


def build_repository(
    http: httpx.AsyncClient, upstream: UpstreamSettings, probes: Probes
) -> PokedexRepository:
    """Cache outermost so cache hits never pay for retries: Cached(Retrying(PokeApi))."""
    return CachedPokedexRepository(
        RetryingPokedexRepository(
            PokeApiRepository(http, probe=probes.pokeapi),
            attempts=upstream.retry_attempts,
            budget_seconds=upstream.retry_budget_seconds,
            probe=probes.retry,
        ),
        ttl_seconds=upstream.cache_ttl_seconds,
        max_entries=upstream.cache_max_entries,
        probe=probes.cache,
    )


class PokedexModule:
    name = "pokedex"
    tool_prefix = PREFIX
    instructions = INSTRUCTIONS

    def __init__(self, settings: PokedexSettings, platform: Platform) -> None:
        probes = build_probes(platform)
        self._http = platform.http_client(base_url=settings.pokeapi_base_url)
        repository = build_repository(self._http, platform.upstream, probes)
        self._service = PokedexService(repository, probe=probes.pokedex)

    def tools(self) -> Sequence[Tool]:
        return PokedexTools(self._service).all()

    def readiness_checks(self) -> Mapping[str, ReadinessCheck]:
        # Nothing local can break: the HTTP client only closes at shutdown, when the phase
        # already reports it, and PokeAPI itself is deliberately not a readiness check.
        return {}

    def running(self) -> AbstractAsyncContextManager[object]:
        return self._http


if TYPE_CHECKING:
    from pokedex_mcp.platform.catalog import CatalogModule

    _: type[CatalogModule] = PokedexModule
