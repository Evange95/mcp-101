"""The Pokédex catalog as a CatalogModule: builds its adapters and exposes its tools."""

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

import httpx
from mcp.server import MCPServer

from pokedex_mcp.catalogs.pokedex.adapters.inbound.mcp.tools import INSTRUCTIONS, register_tools
from pokedex_mcp.catalogs.pokedex.adapters.outbound.cache import CachedPokedexRepository
from pokedex_mcp.catalogs.pokedex.adapters.outbound.pokeapi.client import PokeApiRepository
from pokedex_mcp.catalogs.pokedex.adapters.outbound.retry import RetryingPokedexRepository
from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.probes import Probes, build_probes
from pokedex_mcp.catalogs.pokedex.application.ports import PokedexRepository
from pokedex_mcp.catalogs.pokedex.application.use_cases import PokedexService
from pokedex_mcp.config import Settings
from pokedex_mcp.platform.catalog import Platform
from pokedex_mcp.platform.health import ReadinessCheck

USER_AGENT = "pokedex-mcp/0.1"


def build_repository(
    http: httpx.AsyncClient, settings: Settings, probes: Probes
) -> PokedexRepository:
    """Cache outermost so cache hits never pay for retries: Cached(Retrying(PokeApi))."""
    return CachedPokedexRepository(
        RetryingPokedexRepository(
            PokeApiRepository(http, probe=probes.pokeapi),
            attempts=settings.retry_attempts,
            budget_seconds=settings.retry_budget_seconds,
            probe=probes.retry,
        ),
        ttl_seconds=settings.cache_ttl_seconds,
        max_entries=settings.cache_max_entries,
        probe=probes.cache,
    )


class PokedexModule:
    name = "pokedex"
    instructions = INSTRUCTIONS

    def __init__(self, settings: Settings, platform: Platform) -> None:
        probes = build_probes(platform)
        self._http = platform.http_client(
            base_url=settings.pokeapi_base_url,
            timeout=settings.http_timeout_seconds,
            connect_timeout=settings.http_connect_timeout_seconds,
            headers={"User-Agent": USER_AGENT},
        )
        repository = build_repository(self._http, settings, probes)
        self._service = PokedexService(repository, probe=probes.pokedex)

    def register_tools(self, mcp: MCPServer) -> None:
        register_tools(mcp, self._service)

    def readiness_checks(self) -> Mapping[str, ReadinessCheck]:
        # Nothing local can break: the HTTP client only closes at shutdown, when the phase
        # already reports it, and PokeAPI itself is deliberately not a readiness check.
        return {}

    def running(self) -> AbstractAsyncContextManager[object]:
        return self._http


if TYPE_CHECKING:
    from pokedex_mcp.platform.catalog import CatalogModule

    _: type[CatalogModule] = PokedexModule
