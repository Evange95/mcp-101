"""The Digimon catalog as a CatalogModule: builds its adapters and exposes its tools."""

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

import httpx

from pokedex_mcp.catalogs.digimon.adapters.inbound.mcp.tools import (
    INSTRUCTIONS,
    PREFIX,
    DigimonTools,
)
from pokedex_mcp.catalogs.digimon.adapters.outbound.cache import CachedDigimonRepository
from pokedex_mcp.catalogs.digimon.adapters.outbound.digiapi.client import (
    UPSTREAM,
    DigiApiRepository,
)
from pokedex_mcp.catalogs.digimon.adapters.outbound.retry import RetryingDigimonRepository
from pokedex_mcp.catalogs.digimon.adapters.outbound.telemetry.probes import Probes, build_probes
from pokedex_mcp.catalogs.digimon.application.ports import DigimonRepository
from pokedex_mcp.catalogs.digimon.application.use_cases import DigimonService
from pokedex_mcp.catalogs.digimon.settings import DigimonSettings
from pokedex_mcp.platform.catalog import Platform, Tool
from pokedex_mcp.platform.health import ReadinessCheck
from pokedex_mcp.platform.settings import UpstreamSettings
from pokedex_mcp.platform.upstream.auth import (
    AccessTokenProvider,
    FileTokenProvider,
    StaticTokenProvider,
)


def build_token_provider(
    settings: DigimonSettings, platform: Platform
) -> AccessTokenProvider | None:
    """The configured service token, if any. digi-api.com itself is public and needs none."""
    if settings.token_file is not None:
        return FileTokenProvider(UPSTREAM, settings.token_file, probe=platform.token_probe())
    if settings.token is not None:
        return StaticTokenProvider(UPSTREAM, settings.token)
    return None


def build_repository(
    http: httpx.AsyncClient, upstream: UpstreamSettings, probes: Probes
) -> DigimonRepository:
    """Cache outermost so cache hits never pay for retries: Cached(Retrying(DigiApi))."""
    return CachedDigimonRepository(
        RetryingDigimonRepository(
            DigiApiRepository(http, probe=probes.digiapi),
            attempts=upstream.retry_attempts,
            budget_seconds=upstream.retry_budget_seconds,
            probe=probes.retry,
        ),
        ttl_seconds=upstream.cache_ttl_seconds,
        max_entries=upstream.cache_max_entries,
        not_found_ttl_seconds=upstream.cache_not_found_ttl_seconds,
        probe=probes.cache,
    )


class DigimonModule:
    name = "digimon"
    tool_prefix = PREFIX
    instructions = INSTRUCTIONS

    def __init__(self, settings: DigimonSettings, platform: Platform) -> None:
        probes = build_probes(platform)
        self._tokens = build_token_provider(settings, platform)
        self._http = platform.http_client(
            base_url=settings.base_url,
            auth=platform.bearer_auth(self._tokens) if self._tokens is not None else None,
        )
        repository = build_repository(self._http, platform.upstream, probes)
        self._service = DigimonService(repository, probe=probes.digimon)

    def tools(self) -> Sequence[Tool]:
        return DigimonTools(self._service).all()

    def readiness_checks(self) -> Mapping[str, ReadinessCheck]:
        if self._tokens is None:
            return {}
        # Local only: is the Secret mounted? Not gating: a missing Secret is missing on
        # every replica, and the Pokédex must keep serving. Digimon calls then fail with a
        # clear "no access token" tool error, and /readyz lists the catalog as degraded.
        return {"digiapi_token": ReadinessCheck(self._tokens.available, gating=False)}

    def running(self) -> AbstractAsyncContextManager[object]:
        return self._http


if TYPE_CHECKING:
    from pokedex_mcp.platform.catalog import CatalogModule

    _: type[CatalogModule] = DigimonModule
