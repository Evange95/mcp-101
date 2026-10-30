"""The contract between the platform and a catalog, and the single MCP server they share.

Each catalog (Pokédex, Digimon, ...) is a `CatalogModule`. It owns its resources (HTTP
clients, caches), registers its own tools, and reports its readiness checks. The platform
puts every module behind one MCP endpoint, so an agent needs a single connection, and the
tool name prefix (`pokemon_`, `digimon_`, ...) tells the model which catalog a tool is for.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from mcp.server import MCPServer
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

from pokedex_mcp.platform.health import ReadinessCheck
from pokedex_mcp.platform.inbound.auth import ClientsProbe
from pokedex_mcp.platform.resilience.cache import CacheProbe
from pokedex_mcp.platform.resilience.retry import RetryProbe
from pokedex_mcp.platform.settings import UpstreamSettings
from pokedex_mcp.platform.telemetry.log import LoggingClientsProbe, LoggingTokenProbe
from pokedex_mcp.platform.telemetry.lookups import (
    FanOutLookupRecorder,
    FanOutPayloadRecorder,
    LoggingLookupRecorder,
    LoggingPayloadRecorder,
    LookupRecorder,
    OtelLookupRecorder,
    OtelPayloadRecorder,
    PayloadRecorder,
)
from pokedex_mcp.platform.telemetry.otel import (
    FanOutClientsProbe,
    FanOutTokenProbe,
    OtelClientsProbe,
    OtelTokenProbe,
)
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
# Sent to every upstream, so their operators can tell who is calling.
USER_AGENT = "monsterdex-mcp/0.1"
INTRODUCTION = "Creature catalogs. Every tool name starts with its catalog's prefix:"


@dataclass(frozen=True, slots=True)
class Platform:
    """What the platform offers a catalog while it is being built."""

    telemetry: Telemetry | None = None
    upstream: UpstreamSettings = field(default_factory=UpstreamSettings)

    def http_client(
        self,
        *,
        base_url: str,
        headers: Mapping[str, str] | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        """An HTTP client with the shared timeouts and User-Agent, traced and metered like
        every other outbound call.

        Pass `auth=self.bearer_auth(tokens)` for an upstream that needs a service token.
        """
        timeouts = httpx.Timeout(
            self.upstream.http_timeout_seconds, connect=self.upstream.http_connect_timeout_seconds
        )
        client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeouts,
            headers={"User-Agent": USER_AGENT, **(headers or {})},
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

    def lookup_recorder(self, catalog: str) -> LookupRecorder:
        """Where a catalog's lookups are recorded: logs, plus spans and metrics when on."""
        if self.telemetry is None:
            return LoggingLookupRecorder(catalog)
        return FanOutLookupRecorder(
            LoggingLookupRecorder(catalog),
            OtelLookupRecorder(
                catalog, self.telemetry.tracer_provider, self.telemetry.meter_provider
            ),
        )

    def payload_recorder(self, catalog: str, upstream: str) -> PayloadRecorder:
        """Where a catalog's unexpected upstream payloads are recorded."""
        if self.telemetry is None:
            return LoggingPayloadRecorder(catalog, upstream)
        return FanOutPayloadRecorder(
            LoggingPayloadRecorder(catalog, upstream),
            OtelPayloadRecorder(catalog, self.telemetry.meter_provider),
        )

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

    def clients_probe(self) -> ClientsProbe:
        """Inbound authentication signals: logs always, OTel metrics too when on."""
        if self.telemetry is None:
            return LoggingClientsProbe()
        return FanOutClientsProbe(
            LoggingClientsProbe(), OtelClientsProbe(self.telemetry.meter_provider)
        )

    def bearer_auth(self, tokens: AccessTokenProvider) -> BearerAuth:
        return BearerAuth(tokens, probe=self.token_probe())


type Tool = Callable[..., Awaitable[object]]

# Every tool is read-only: catalogs only answer questions about reference data. A tool that
# changes something would need its own annotations, and a change to this contract.
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)


class CatalogModule(Protocol):
    @property
    def name(self) -> str:
        """Short id, used to namespace readiness checks (e.g. "pokedex")."""
        ...

    @property
    def tool_prefix(self) -> str:
        """What every tool name of this catalog starts with, e.g. "pokemon_"."""
        ...

    @property
    def instructions(self) -> str:
        """One paragraph for the MCP `instructions`: what the catalog holds, its tool prefix."""
        ...

    def tools(self) -> Sequence[Tool]:
        """The catalog's tools, in the order clients list them; each is named after its
        function (`pokemon_get`) and documented by its docstring."""
        ...

    def readiness_checks(self) -> Mapping[str, ReadinessCheck]: ...

    def running(self) -> AbstractAsyncContextManager[object]:
        """Open the module's resources for the app's lifetime; closing releases them."""
        ...


class CatalogConflict(ValueError):
    """Two catalogs (or two tools) would be indistinguishable on the shared endpoint."""


def add_catalog_tools(mcp: MCPServer, prefix: str, tools: Sequence[Tool]) -> None:
    """Register one catalog's tools, read-only, after checking every name has its prefix."""
    if not prefix.endswith("_"):
        raise CatalogConflict(f"tool prefix {prefix!r} must end with '_'")
    for tool in tools:
        if not tool.__name__.startswith(prefix):
            raise CatalogConflict(f"tool {tool.__name__!r} does not start with {prefix!r}")
        mcp.add_tool(tool, name=tool.__name__, annotations=READ_ONLY)


# Static client tokens have no authorization server, but the SDK's auth settings require an
# issuer URL. It is only published in OAuth metadata, which stays off (no resource URL).
STATIC_TOKENS_ISSUER = AnyHttpUrl("https://static-tokens.invalid")


def build_mcp_server(
    modules: Sequence[CatalogModule], token_verifier: TokenVerifier | None = None
) -> MCPServer:
    """One MCP server for every catalog. Fails at startup if two catalogs share a name or a
    tool prefix, or one prefix starts another: a model could no longer tell them apart.

    With a `token_verifier`, every MCP request needs a known bearer token (401 otherwise).
    """
    names = [module.name for module in modules]
    if len(set(names)) != len(names):
        raise CatalogConflict(f"duplicate catalog names: {names}")
    prefixes = [module.tool_prefix for module in modules]
    for prefix in prefixes:
        if clash := [p for p in prefixes if p != prefix and p.startswith(prefix)]:
            raise CatalogConflict(f"tool prefix {prefix!r} is a prefix of {clash}")
    if len(set(prefixes)) != len(prefixes):
        raise CatalogConflict(f"duplicate tool prefixes: {prefixes}")
    instructions = "\n".join([INTRODUCTION, *(f"- {m.instructions}" for m in modules)])
    auth = (
        AuthSettings(issuer_url=STATIC_TOKENS_ISSUER, resource_server_url=None)
        if token_verifier is not None
        else None
    )
    mcp = MCPServer(
        name=SERVER_NAME, instructions=instructions, token_verifier=token_verifier, auth=auth
    )
    for module in modules:
        add_catalog_tools(mcp, module.tool_prefix, module.tools())
    return mcp


def readiness_checks(modules: Sequence[CatalogModule]) -> dict[str, ReadinessCheck]:
    """Every module's checks, namespaced: {"digimon.digiapi_token": ...}."""
    return {
        f"{module.name}.{check}": probe
        for module in modules
        for check, probe in module.readiness_checks().items()
    }
