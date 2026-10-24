"""Assembles the probe set: always structured logs, plus OpenTelemetry when enabled."""

from dataclasses import dataclass

from pokedex_mcp.catalogs.pokedex.adapters.outbound.probes import PokeApiProbe
from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.fanout import (
    FanOutCacheProbe,
    FanOutPokeApiProbe,
    FanOutPokedexProbe,
    FanOutRetryProbe,
)
from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.log import (
    LoggingCacheProbe,
    LoggingPokeApiProbe,
    LoggingPokedexProbe,
    LoggingRetryProbe,
)
from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.otel import (
    OtelCacheProbe,
    OtelPokeApiProbe,
    OtelPokedexProbe,
    OtelRetryProbe,
)
from pokedex_mcp.catalogs.pokedex.application.probes import PokedexProbe
from pokedex_mcp.platform.resilience.cache import CacheProbe
from pokedex_mcp.platform.resilience.retry import RetryProbe
from pokedex_mcp.platform.telemetry.sdk import Telemetry


@dataclass(frozen=True, slots=True)
class Probes:
    pokedex: PokedexProbe
    cache: CacheProbe
    retry: RetryProbe
    pokeapi: PokeApiProbe


def build_probes(telemetry: Telemetry | None) -> Probes:
    if telemetry is None:
        return Probes(
            pokedex=LoggingPokedexProbe(),
            cache=LoggingCacheProbe(),
            retry=LoggingRetryProbe(),
            pokeapi=LoggingPokeApiProbe(),
        )
    tracer_provider, meter_provider = telemetry.tracer_provider, telemetry.meter_provider
    return Probes(
        pokedex=FanOutPokedexProbe(
            LoggingPokedexProbe(), OtelPokedexProbe(tracer_provider, meter_provider)
        ),
        cache=FanOutCacheProbe(LoggingCacheProbe(), OtelCacheProbe(meter_provider)),
        retry=FanOutRetryProbe(LoggingRetryProbe(), OtelRetryProbe(meter_provider)),
        pokeapi=FanOutPokeApiProbe(LoggingPokeApiProbe(), OtelPokeApiProbe(meter_provider)),
    )
