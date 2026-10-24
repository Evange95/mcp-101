"""Composition root: wires adapters to the core and hosts the MCP server in FastAPI."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from pokedex_mcp.adapters.inbound.mcp.server import build_mcp_server
from pokedex_mcp.adapters.outbound.cache import CachedPokedexRepository
from pokedex_mcp.adapters.outbound.pokeapi.client import PokeApiRepository
from pokedex_mcp.adapters.outbound.retry import RetryingPokedexRepository
from pokedex_mcp.adapters.outbound.telemetry.probes import Probes, build_probes
from pokedex_mcp.application.ports import PokedexRepository
from pokedex_mcp.application.use_cases import PokedexService
from pokedex_mcp.config import Settings
from pokedex_mcp.platform.health import build_health_router
from pokedex_mcp.platform.lifecycle import Lifecycle
from pokedex_mcp.platform.server import GracefulServer
from pokedex_mcp.platform.telemetry.log import LoggingLifecycleProbe, configure_logging
from pokedex_mcp.platform.telemetry.sdk import Telemetry

LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
USER_AGENT = "pokedex-mcp/0.1"


def build_repository(
    http: httpx.AsyncClient, settings: Settings, probes: Probes
) -> PokedexRepository:
    """Cache outermost so cache hits never pay for retries: Cached(Retrying(PokeApi))."""
    return CachedPokedexRepository(
        RetryingPokedexRepository(
            PokeApiRepository(http, probe=probes.pokeapi),
            attempts=settings.retry_attempts,
            probe=probes.retry,
        ),
        ttl_seconds=settings.cache_ttl_seconds,
        max_entries=settings.cache_max_entries,
        probe=probes.cache,
    )


def create_app(
    settings: Settings | None = None,
    telemetry: Telemetry | None = None,
    lifecycle: Lifecycle | None = None,
) -> FastAPI:
    """`telemetry` is owned by the caller, which installs it and shuts it down. `lifecycle`
    is shared with the server that drives the graceful shutdown (see `run`)."""
    settings = settings or Settings()
    lifecycle = lifecycle or Lifecycle(LoggingLifecycleProbe())
    probes = build_probes(telemetry)
    http = httpx.AsyncClient(
        base_url=settings.pokeapi_base_url,
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
    )
    # A fresh MCPServer per app: its session manager can only be run once.
    service = PokedexService(build_repository(http, settings, probes), probe=probes.pokedex)
    mcp = build_mcp_server(service)
    mcp_app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            allowed_hosts=[*LOCAL_HOSTS, *settings.allowed_hosts],
            allowed_origins=LOCAL_ORIGINS,
        )
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with http, mcp.session_manager.run():
            lifecycle.ready()
            try:
                yield
            finally:
                lifecycle.stop(reason="lifespan shutdown")

    app = FastAPI(title="Pokédex MCP", version="0.1.0", lifespan=lifespan)
    app.include_router(
        build_health_router(lifecycle, checks={"pokeapi_client": lambda: not http.is_closed})
    )
    app.mount("/", mcp_app)  # serves POST /mcp; must stay the last route
    if telemetry is not None:
        telemetry.instrument(app, http)
    return app


def run() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    telemetry = Telemetry.otlp() if settings.telemetry_enabled else None
    if telemetry is not None:
        telemetry.install()
    lifecycle = Lifecycle(LoggingLifecycleProbe())
    config = uvicorn.Config(
        create_app(settings, telemetry, lifecycle),
        host=settings.host,
        port=settings.port,
        timeout_graceful_shutdown=settings.shutdown_timeout_seconds,
    )
    server = GracefulServer(
        config,
        lifecycle=lifecycle,
        drain_seconds=settings.shutdown_drain_seconds,
        on_stopped=telemetry.shutdown if telemetry is not None else None,
    )
    try:
        server.run()
    finally:  # startup failures never reach GracefulServer.shutdown
        if telemetry is not None:
            telemetry.shutdown()
