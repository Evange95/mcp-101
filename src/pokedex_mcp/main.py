"""Composition root: builds the catalog modules and hosts their MCP tools in FastAPI."""

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

import uvicorn
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from pokedex_mcp.catalogs.digimon.module import DigimonModule
from pokedex_mcp.catalogs.pokedex.module import PokedexModule
from pokedex_mcp.config import Settings, legacy_variables
from pokedex_mcp.platform.catalog import (
    CatalogModule,
    Platform,
    build_mcp_server,
    readiness_checks,
)
from pokedex_mcp.platform.health import ReadinessCheck, build_health_router
from pokedex_mcp.platform.inbound.auth import FileClientTokens
from pokedex_mcp.platform.lifecycle import Lifecycle
from pokedex_mcp.platform.server import GracefulServer
from pokedex_mcp.platform.telemetry.log import (
    LOGGER_NAME,
    LoggingLifecycleProbe,
    configure_logging,
)
from pokedex_mcp.platform.telemetry.sdk import Telemetry

LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def build_modules(settings: Settings, platform: Platform) -> list[CatalogModule]:
    """The catalogs this deployment serves. Add a catalog here (behind a setting if optional)."""
    modules: list[CatalogModule] = [PokedexModule(settings.pokedex, platform)]
    if settings.digimon.enabled:
        modules.append(DigimonModule(settings.digimon, platform))
    return modules


def create_app(
    settings: Settings | None = None,
    telemetry: Telemetry | None = None,
    lifecycle: Lifecycle | None = None,
) -> FastAPI:
    """`telemetry` is owned by the caller, which installs it and shuts it down. `lifecycle`
    is shared with the server that drives the graceful shutdown (see `run`)."""
    settings = settings or Settings()
    lifecycle = lifecycle or Lifecycle(LoggingLifecycleProbe())
    platform = Platform(telemetry, settings.upstream)
    modules = build_modules(settings, platform)
    checks = readiness_checks(modules)
    clients = None
    if settings.auth_clients_file is not None:
        clients = FileClientTokens(settings.auth_clients_file, probe=platform.clients_probe())
        # Not gating: the Secret is the same on every replica (see platform/health.py).
        checks["auth.clients_file"] = ReadinessCheck(clients.available, gating=False)
    # A fresh MCPServer per app: its session manager can only be run once.
    mcp = build_mcp_server(modules, token_verifier=clients)
    mcp_app = mcp.streamable_http_app(
        # Every tool is read-only and keeps no per-client state, so no session is needed:
        # any replica can answer any request, and a rolling update or a drain loses
        # nothing. Plain JSON responses: there is nothing to stream.
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[*LOCAL_HOSTS, *settings.allowed_hosts],
            allowed_origins=LOCAL_ORIGINS,
        ),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with AsyncExitStack() as resources:
            for module in modules:
                await resources.enter_async_context(module.running())
            await resources.enter_async_context(mcp.session_manager.run())
            lifecycle.ready()
            try:
                yield
            finally:
                lifecycle.stop(reason="lifespan shutdown")

    app = FastAPI(title="Monsterdex MCP", version="0.1.0", lifespan=lifespan)
    app.include_router(build_health_router(lifecycle, checks=checks))
    app.mount("/", mcp_app)  # serves POST /mcp; must stay the last route
    if telemetry is not None:
        telemetry.instrument_app(app)
    return app


def run() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    if legacy := legacy_variables(os.environ):
        # Ignoring them silently would run an old deployment with defaults.
        logging.getLogger(LOGGER_NAME).warning(
            "ignored, renamed to MONSTERDEX_*: %s", ", ".join(legacy)
        )
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
