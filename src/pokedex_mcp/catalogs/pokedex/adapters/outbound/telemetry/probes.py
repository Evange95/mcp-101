"""Assembles the Pokédex probe set: always structured logs, plus OpenTelemetry when enabled."""

from dataclasses import dataclass

from pokedex_mcp.catalogs.pokedex.adapters.outbound.probes import PokeApiProbe
from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.fanout import (
    FanOutPokeApiProbe,
    FanOutPokedexProbe,
)
from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.log import (
    CATALOG,
    LoggingPokeApiProbe,
    LoggingPokedexProbe,
)
from pokedex_mcp.catalogs.pokedex.adapters.outbound.telemetry.otel import (
    OtelPokeApiProbe,
    OtelPokedexProbe,
)
from pokedex_mcp.catalogs.pokedex.application.probes import PokedexProbe
from pokedex_mcp.platform.catalog import Platform
from pokedex_mcp.platform.resilience.cache import CacheProbe
from pokedex_mcp.platform.resilience.retry import RetryProbe


@dataclass(frozen=True, slots=True)
class Probes:
    pokedex: PokedexProbe
    cache: CacheProbe
    retry: RetryProbe
    pokeapi: PokeApiProbe


def build_probes(platform: Platform) -> Probes:
    """Cache and retry probes are the platform's generic ones, tagged `catalog.name=pokedex`."""
    cache, retry = platform.cache_probe(CATALOG), platform.retry_probe(CATALOG)
    telemetry = platform.telemetry
    if telemetry is None:
        return Probes(
            pokedex=LoggingPokedexProbe(), cache=cache, retry=retry, pokeapi=LoggingPokeApiProbe()
        )
    tracer_provider, meter_provider = telemetry.tracer_provider, telemetry.meter_provider
    return Probes(
        pokedex=FanOutPokedexProbe(
            LoggingPokedexProbe(), OtelPokedexProbe(tracer_provider, meter_provider)
        ),
        cache=cache,
        retry=retry,
        pokeapi=FanOutPokeApiProbe(LoggingPokeApiProbe(), OtelPokeApiProbe(meter_provider)),
    )
