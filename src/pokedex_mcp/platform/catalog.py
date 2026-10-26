"""The contract between the platform and a catalog, and the single MCP server they share.

Each catalog (Pokédex, Digimon, ...) is a `CatalogModule`. It owns its resources (HTTP
clients, caches), registers its own tools, and reports its readiness checks. The platform
puts every module behind one MCP endpoint, so an agent needs a single connection, and the
tool name prefix (`pokemon_`, `digimon_`, ...) tells the model which catalog a tool is for.
"""

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol

import httpx
from mcp.server import MCPServer

from pokedex_mcp.platform.health import ReadinessCheck
from pokedex_mcp.platform.resilience.cache import CacheProbe
from pokedex_mcp.platform.resilience.retry import RetryProbe
from pokedex_mcp.platform.telemetry.log import LoggingTokenProbe
from pokedex_mcp.platform.telemetry.otel import FanOutTokenProbe, OtelTokenProbe
from pokedex_mcp.platform.telemetry.resilience import (
    FanOutCacheProbe,
    FanOutRetryProbe,
    LoggingCacheProbe,
    LoggingRetryProbe,
    OtelCacheProbe,
    OtelRetryProbe,
)
from pokedex_mcp.platform.telemetry.sdk import Telemetry
from pokedex_mcp.platform.upstream.auth import AccessTokenProvider, BearerAuth, TokenProbe

SERVER_NAME = "monsterdex"
INTRODUCTION = "Creature catalogs. Every tool name starts with its catalog's prefix:"


@dataclass(frozen=True, slots=True)
class Platform:
    """What the platform offers a catalog while it is being built."""

    telemetry: Telemetry | None = None

    def http_client(
        self,
        *,
        base_url: str,
        timeout: float,
        connect_timeout: float | None = None,
        headers: Mapping[str, str],
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        """An HTTP client traced and metered like every other outbound call.

        Pass `auth=self.bearer_auth(tokens)` for an upstream that needs a service token.
        """
        client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=connect_timeout or timeout),
            headers=dict(headers),
            auth=auth,
        )
        if self.telemetry is not None:
            self.telemetry.instrument_http(client)
        return client

    def token_probe(self) -> TokenProbe:
        """Logs always; OpenTelemetry metrics and span events too when telemetry is on."""
        if self.telemetry is None:
            return LoggingTokenProbe()
        return FanOutTokenProbe(LoggingTokenProbe(), OtelTokenProbe(self.telemetry.meter_provider))

    def cache_probe(self, catalog: str) -> CacheProbe:
        """A catalog's cache signals, tagged `catalog.name`: logs, plus OTel when on."""
        if self.telemetry is None:
            return LoggingCacheProbe(catalog)
        return FanOutCacheProbe(
            LoggingCacheProbe(catalog), OtelCacheProbe(self.telemetry.meter_provider, catalog)
        )

    def retry_probe(self, catalog: str) -> RetryProbe:
        """A catalog's retry signals, tagged `catalog.name`: logs, plus OTel when on."""
        if self.telemetry is None:
            return LoggingRetryProbe(catalog)
        return FanOutRetryProbe(
            LoggingRetryProbe(catalog), OtelRetryProbe(self.telemetry.meter_provider, catalog)
        )

    def bearer_auth(self, tokens: AccessTokenProvider) -> BearerAuth:
        return BearerAuth(tokens, probe=self.token_probe())


class CatalogModule(Protocol):
    @property
    def name(self) -> str:
        """Short id, used to namespace readiness checks (e.g. "pokedex")."""
        ...

    @property
    def instructions(self) -> str:
        """One paragraph for the MCP `instructions`: what the catalog holds, its tool prefix."""
        ...

    def register_tools(self, mcp: MCPServer) -> None: ...

    def readiness_checks(self) -> Mapping[str, ReadinessCheck]: ...

    def running(self) -> AbstractAsyncContextManager[object]:
        """Open the module's resources for the app's lifetime; closing releases them."""
        ...


def build_mcp_server(modules: Sequence[CatalogModule]) -> MCPServer:
    instructions = "\n".join([INTRODUCTION, *(f"- {m.instructions}" for m in modules)])
    mcp = MCPServer(name=SERVER_NAME, instructions=instructions)
    for module in modules:
        module.register_tools(mcp)
    return mcp


def readiness_checks(modules: Sequence[CatalogModule]) -> dict[str, ReadinessCheck]:
    """Every module's checks, namespaced: {"digimon.digiapi_token": ...}."""
    return {
        f"{module.name}.{check}": probe
        for module in modules
        for check, probe in module.readiness_checks().items()
    }
